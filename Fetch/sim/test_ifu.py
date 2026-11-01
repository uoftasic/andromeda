"""cocotb tests for the fetch unit's ifu (Spec 02, Section 3.1).

Run from Fetch/sim:

    make COCOTB_TOPLEVEL=ifu COCOTB_TEST_MODULES=test_ifu

A Python model of instruction memory answers the ifu's requests in order after
a random latency. A reference model of the instruction stream checks every slot
the ifu hands to the fetch buffer: with no branch prediction yet, the stream
runs sequentially from boot_addr and restarts at every redirect. Monitors check
both handshakes every cycle.

Set COCOTB_RANDOM_SEED=<n> to repeat a run; each test logs the seed it used.

With make DEBUG=1 the ifu prints a trace of every handshake ("[ifu] Cycle N:
..."), and the tests print every slot they expect the fetch buffer to take in
the same format, just before the ifu's own line for that cycle.
"""

import os
import random
from collections import deque

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

FETCH_W = 4  # instructions per fetch block
FETCH_BYTES = 16  # bytes per fetch block
MAX_IN_FLIGHT = 2  # Spec 02, Section 2.2: up to 2 requests in flight

# Set by make DEBUG=1, which also compiles the ifu's debug trace in.
DEBUG = os.environ.get("ANDROMEDA_DEBUG") == "1"

# fetch_slot_t from pkg/andromeda_pkg.sv. The first field is the MSB.
SLOT_FIELDS = [
    ("valid", 1),
    ("pc", 32),
    ("instr", 32),
    ("pred_taken", 1),
    ("pred_target", 32),
    ("fetch_err", 1),
]
SLOT_BITS = sum(width for _, width in SLOT_FIELDS)


def instr_at(addr):
    """Instruction word the memory model holds at addr.

    Any 32-bit value works since the ifu doesn't decode; a hash of the address
    makes a slot with the wrong instruction easy to spot.
    """
    return (addr * 0x9E3779B1 + 0x13) & 0xFFFFFFFF


def block_of(pc):
    """Address of the 16-byte fetch block holding pc."""
    return pc - pc % FETCH_BYTES


def unpack_slots(value):
    """Split a packed fb_enq_slot value into FETCH_W dicts, slot 0 first."""
    slots = []
    for i in range(FETCH_W):
        bits = (value >> (SLOT_BITS * i)) & ((1 << SLOT_BITS) - 1)
        slot = {}
        lsb = SLOT_BITS
        for name, width in SLOT_FIELDS:
            lsb -= width
            slot[name] = (bits >> lsb) & ((1 << width) - 1)
        slots.append(slot)
    return slots


class IfuEnv:
    """Drives the ifu, models instruction memory and checks everything it sends.

    Every cycle, run() drives the inputs just after the rising edge, waits for
    the outputs to settle, then checks them. A handshake counts as done when
    valid and ready are both 1 in that settled view.
    """

    def __init__(
        self,
        dut,
        *,
        seed,
        boot_addr=0x1000,
        latency=(1, 1),
        req_ready_pct=100,
        fb_ready_pct=100,
        redirect_pct=0,
        fault_blocks=(),
        verbose=True,
    ):
        self.dut = dut
        self.rng = random.Random(seed)
        self.boot_addr = boot_addr
        self.latency = latency  # (min, max) cycles from request accepted to response
        self.req_ready_pct = req_ready_pct  # chance imem_req_ready is 1 in a cycle
        self.fb_ready_pct = fb_ready_pct  # chance fb_enq_ready is 1 in a cycle
        self.redirect_pct = redirect_pct  # chance of a random redirect in a cycle
        self.fault_blocks = set(fault_blocks)  # blocks memory answers with imem_resp_err
        self.verbose = verbose  # log every request, block and redirect

        # Directed stimulus, keyed by cycle; overrides the random choice.
        self.redirect_at = {}  # cycle -> redirect_pc
        self.req_ready_at = {}  # cycle -> imem_req_ready
        self.fb_ready_at = {}  # cycle -> fb_enq_ready

        # Memory model: accepted requests, oldest first, as [block, due cycle, epoch].
        # The epoch counts redirects so responses from the old path can be counted.
        self.mem = deque()
        self.epoch = 0
        self.in_flight = 0

        # Reference model: PC of the next instruction the fetch buffer should get.
        self.expected_pc = boot_addr

        # Handshakes left waiting at the end of the previous cycle.
        self.waiting_req = None  # imem_req_addr
        self.waiting_fb = None  # packed fb_enq_slot

        # Record of what happened, for the tests' own checks.
        self.cycle = 0
        self.redirect_pc = None  # redirect driven this cycle
        self.accepted = []  # (cycle, block) for every request imem accepted
        self.blocks = []  # (cycle, block, first valid slot) for every block enqueued
        self.redirects = []  # (cycle, target, requests in flight, request waiting)
        self.stale_responses = 0  # responses that returned after their path was redirected
        self.fault_slots = 0
        self.max_in_flight = 0

    def log(self, msg):
        if self.verbose:
            cocotb.log.info(f"Cycle {self.cycle}: {msg}")

    def chance(self, pct):
        return int(self.rng.randrange(100) < pct)

    def read(self, name):
        """Read a DUT signal as an int, failing clearly if it holds X or Z."""
        value = getattr(self.dut, name).value
        assert value.is_resolvable, (
            f"Cycle {self.cycle}: {name} should be 0 or 1 in every bit, but it is {value}"
        )
        return int(value)

    async def reset(self):
        """Hold rst_n low for 3 cycles with every input idle, then release it."""
        dut = self.dut
        dut.boot_addr.value = self.boot_addr
        dut.flush.value = 0
        dut.redirect_valid.value = 0
        dut.redirect_pc.value = 0
        dut.imem_req_ready.value = 0
        dut.imem_resp_valid.value = 0
        dut.imem_resp_data.value = 0
        dut.imem_resp_err.value = 0
        dut.fb_enq_ready.value = 0
        cocotb.log.info(f"Holding rst_n low for 3 cycles with boot_addr = {self.boot_addr:#x}")
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 3)
        dut.rst_n.value = 1
        cocotb.log.info("Reset released")

    async def run(self, cycles):
        """Run for a number of cycles."""
        for _ in range(cycles):
            await self.step()

    async def finish(self):
        """Let one more clock edge pass at the end of a test.

        The ifu's debug trace prints a cycle at the edge that ends it, so
        without this the trace would miss the test's last cycle.
        """
        await RisingEdge(self.dut.clk)
        await ReadOnly()

    async def run_until_blocks(self, count, max_cycles):
        """Run until the fetch buffer has taken `count` blocks."""
        start = self.cycle
        while len(self.blocks) < count:
            assert self.cycle - start < max_cycles, (
                f"The fetch buffer should have taken {count} blocks within {max_cycles} "
                f"cycles, but it only took {len(self.blocks)}; fetch looks stuck"
            )
            await self.step()

    async def step(self):
        await RisingEdge(self.dut.clk)
        self.cycle += 1
        self.drive()
        await ReadOnly()
        was_waiting = self.waiting_req is not None
        self.check_imem_request()
        self.check_fetch_buffer()
        if self.redirect_pc is not None:
            self.redirects.append((self.cycle, self.redirect_pc, self.in_flight, was_waiting))
            self.log(
                f"redirect to {self.redirect_pc:#x} with {self.in_flight} request(s) in "
                f"flight{' and one waiting on imem' if was_waiting else ''}; the stream "
                f"restarts there"
            )
            self.expected_pc = self.redirect_pc

    def drive(self):
        dut, c = self.dut, self.cycle

        # A redirect comes with fe_flush, as pipe_ctrl raises both together.
        if c in self.redirect_at:
            self.redirect_pc = self.redirect_at[c]
        elif self.chance(self.redirect_pct):
            self.redirect_pc = 0x4000 + 4 * self.rng.randrange(256)
        else:
            self.redirect_pc = None
        dut.redirect_valid.value = int(self.redirect_pc is not None)
        dut.flush.value = int(self.redirect_pc is not None)
        if self.redirect_pc is not None:
            dut.redirect_pc.value = self.redirect_pc
            self.epoch += 1

        dut.imem_req_ready.value = self.req_ready_at.get(c, self.chance(self.req_ready_pct))
        dut.fb_enq_ready.value = self.fb_ready_at.get(c, self.chance(self.fb_ready_pct))

        # Memory answers the oldest accepted request once its latency is up.
        if self.mem and self.mem[0][1] <= c:
            block, _, epoch = self.mem.popleft()
            self.in_flight -= 1
            if epoch != self.epoch:
                self.stale_responses += 1
            dut.imem_resp_valid.value = 1
            dut.imem_resp_data.value = sum(
                instr_at(block + 4 * i) << (32 * i) for i in range(FETCH_W)
            )
            dut.imem_resp_err.value = int(block in self.fault_blocks)
        else:
            dut.imem_resp_valid.value = 0

    def check_imem_request(self):
        c = self.cycle
        valid = self.read("imem_req_valid")
        ready = self.read("imem_req_ready")
        addr = self.read("imem_req_addr") if valid else None

        if self.waiting_req is not None and self.redirect_pc is None:
            assert valid, (
                f"Cycle {c}: imem_req_valid dropped while the request for "
                f"{self.waiting_req:#x} was still waiting for imem_req_ready"
            )
            assert addr == self.waiting_req, (
                f"Cycle {c}: imem_req_addr changed from {self.waiting_req:#x} to "
                f"{addr:#x} before imem accepted the request"
            )
        if valid:
            assert addr % FETCH_BYTES == 0, (
                f"Cycle {c}: imem_req_addr should be 16-byte aligned, but it is {addr:#x}"
            )
        assert not (valid and self.redirect_pc is not None), (
            f"Cycle {c}: imem_req_valid is 1 during a redirect; the request should be withdrawn"
        )

        if valid and ready:
            lat = self.rng.randint(*self.latency)
            self.mem.append([addr, c + lat, self.epoch])
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            self.accepted.append((c, addr))
            self.log(f"imem accepted a request for block {addr:#x}; it returns in {lat} cycle(s)")
            assert self.in_flight <= MAX_IN_FLIGHT, (
                f"Cycle {c}: {self.in_flight} requests are in flight, but the spec allows "
                f"at most {MAX_IN_FLIGHT}"
            )
        self.waiting_req = addr if valid and not ready else None

    def check_fetch_buffer(self):
        c = self.cycle
        valid = self.read("fb_enq_valid")
        ready = self.read("fb_enq_ready")
        raw = self.read("fb_enq_slot") if valid else None

        if self.redirect_pc is not None:
            assert not valid, (
                f"Cycle {c}: fb_enq_valid is 1 during a redirect; nothing from the old "
                f"path may reach the fetch buffer"
            )
        elif self.waiting_fb is not None:
            assert valid, (
                f"Cycle {c}: fb_enq_valid dropped while a block was still waiting for "
                f"fb_enq_ready"
            )
            assert raw == self.waiting_fb, (
                f"Cycle {c}: fb_enq_slot changed before the fetch buffer took the block"
            )

        if valid and ready:
            self.check_block(unpack_slots(raw))
        self.waiting_fb = raw if valid and not ready else None

    def check_block(self, slots):
        """Compare one block the fetch buffer took against the reference stream."""
        c = self.cycle
        block = block_of(self.expected_pc)
        first = (self.expected_pc % FETCH_BYTES) // 4
        want_err = int(block in self.fault_blocks)
        if DEBUG:
            # Printed before the checks, so a failing block still shows what was expected.
            for i in range(first, FETCH_W):
                pc = block + 4 * i
                cocotb.log.info(
                    f"Cycle {c}: expected slot {i} pc {pc:#010x} instr {instr_at(pc):#010x} "
                    f"err {want_err}"
                )
        for i, slot in enumerate(slots):
            want_valid = int(i >= first)
            assert slot["valid"] == want_valid, (
                f"Cycle {c}: block {block:#x} should have slots {first}..{FETCH_W - 1} "
                f"valid (next expected pc {self.expected_pc:#x}), but slot {i} has "
                f"valid = {slot['valid']}"
            )
            if not want_valid:
                continue
            pc = block + 4 * i
            assert slot["pc"] == pc, (
                f"Cycle {c}: slot {i} should hold pc {pc:#x}, but it holds {slot['pc']:#x}"
            )
            assert slot["instr"] == instr_at(pc), (
                f"Cycle {c}: slot {i} (pc {pc:#x}) should hold instruction "
                f"{instr_at(pc):#010x} from memory, but it holds {slot['instr']:#010x}"
            )
            assert slot["pred_taken"] == 0, (
                f"Cycle {c}: slot {i} (pc {pc:#x}) should be predicted not taken, "
                f"but pred_taken is 1"
            )
            assert slot["fetch_err"] == want_err, (
                f"Cycle {c}: slot {i} (pc {pc:#x}) should have fetch_err = {want_err}, "
                f"but it is {slot['fetch_err']}"
            )
            self.fault_slots += want_err
        self.blocks.append((c, block, first))
        self.expected_pc = block + FETCH_BYTES
        self.log(
            f"fetch buffer took block {block:#x} with slots {first}..{FETCH_W - 1} valid"
            f"{' and fetch_err set' if want_err else ''}; pcs and instructions match"
        )


async def start(dut, name, **kwargs):
    """Start the clock, build the environment and reset the ifu."""
    seed = cocotb.RANDOM_SEED
    cocotb.log.info(f"Starting {name} with seed {seed} and a 10 ns clock")
    Clock(dut.clk, 10, unit="ns").start()
    env = IfuEnv(dut, seed=seed, **kwargs)
    await env.reset()
    return env


@cocotb.test()
async def test_sequential_fetch(dut):
    """Fetch 16 blocks in a row from an aligned boot address, one block per cycle."""
    env = await start(dut, "test_sequential_fetch", boot_addr=0x1000)
    await env.run_until_blocks(16, max_cycles=40)

    cycles = [cyc for cyc, _, _ in env.blocks]
    gaps = [b - a for a, b in zip(cycles, cycles[1:])]
    assert all(gap == 1 for gap in gaps), (
        f"With 1-cycle memory and a ready fetch buffer, a block should arrive every "
        f"cycle, but the gaps between blocks were {gaps}"
    )
    await env.finish()
    cocotb.log.info(
        f"16 sequential blocks from {env.boot_addr:#x} arrived one per cycle and matched "
        f"the reference stream"
    )


@cocotb.test()
async def test_unaligned_boot(dut):
    """Boot from the middle of a block: the slots below boot_addr are invalid."""
    env = await start(dut, "test_unaligned_boot", boot_addr=0x2008)
    await env.run_until_blocks(3, max_cycles=20)

    _, block, first = env.blocks[0]
    assert (block, first) == (0x2000, 2), (
        f"The first block should be 0x2000 with slots 2..3 valid, but it was "
        f"{block:#x} with slots {first}..3 valid"
    )
    await env.finish()
    cocotb.log.info("First block 0x2000 had slots 0 and 1 invalid; later blocks were whole")


@cocotb.test()
async def test_redirect_to_unaligned_pc(dut):
    """Redirect mid-stream to 0x3004: fetch restarts at block 0x3000 with slot 0 invalid."""
    env = await start(dut, "test_redirect_to_unaligned_pc", boot_addr=0x1000)
    env.redirect_at[6] = 0x3004
    await env.run_until_blocks(8, max_cycles=40)

    after = [(block, first) for cyc, block, first in env.blocks if cyc > 6]
    assert after and after[0] == (0x3000, 1), (
        f"The first block after the redirect should be 0x3000 with slots 1..3 valid, "
        f"but the blocks after it were {[(hex(b), f) for b, f in after]}"
    )
    await env.finish()
    cocotb.log.info("After the redirect, fetch restarted at 0x3004 with slot 0 of 0x3000 invalid")


@cocotb.test()
async def test_redirect_drops_in_flight_response(dut):
    """Redirect while a request is in flight: its response is dropped when it returns."""
    env = await start(
        dut, "test_redirect_drops_in_flight_response", boot_addr=0x1000, latency=(3, 3)
    )
    env.redirect_at[9] = 0x5000
    await env.run_until_blocks(6, max_cycles=60)

    _, _, in_flight, _ = env.redirects[0]
    assert in_flight > 0, (
        f"The test needs a request in flight at the redirect, but there were {in_flight}"
    )
    assert env.stale_responses > 0, (
        "A response from before the redirect should have come back after it, but none did"
    )
    after = [block for cyc, block, _ in env.blocks if cyc > 9]
    assert after and after[0] == 0x5000, (
        f"The first block after the redirect should be 0x5000, but the blocks after "
        f"it were {[hex(b) for b in after]}"
    )
    await env.finish()
    cocotb.log.info(
        f"{env.stale_responses} response(s) from the old path came back after the "
        f"redirect and none reached the fetch buffer"
    )


@cocotb.test()
async def test_redirect_withdraws_waiting_request(dut):
    """Redirect while a request waits for imem_req_ready: it is withdrawn, never sent."""
    env = await start(dut, "test_redirect_withdraws_waiting_request", boot_addr=0x1000)
    for cyc in range(5, 13):
        env.req_ready_at[cyc] = 0
    env.redirect_at[8] = 0x6008
    await env.run_until_blocks(6, max_cycles=40)

    _, _, _, was_waiting = env.redirects[0]
    assert was_waiting, "The test needs a request waiting on imem at the redirect"
    after = [block for cyc, block in env.accepted if cyc > 8]
    assert after and after[0] == 0x6000, (
        f"The first request imem accepts after the redirect should be block 0x6000, "
        f"but the requests were {[hex(b) for b in after]}"
    )
    await env.finish()
    cocotb.log.info(
        "The request waiting at the redirect was withdrawn; the next one imem accepted "
        "was block 0x6000"
    )


@cocotb.test()
async def test_fetch_buffer_full(dut):
    """Fetch buffer full for 25 cycles: fetch holds, then resumes without losing or
    duplicating slots."""
    env = await start(dut, "test_fetch_buffer_full", boot_addr=0x1000, latency=(1, 3))
    for cyc in range(6, 31):
        env.fb_ready_at[cyc] = 0
    await env.run(40)

    during = [block for cyc, block in env.accepted if 8 <= cyc <= 30]
    assert len(during) <= 1, (
        f"With the fetch buffer full, fetch should stop requesting once it holds a "
        f"block, but imem accepted {len(during)} requests during the stall: "
        f"{[hex(b) for b in during]}"
    )
    held = [block for cyc, block, _ in env.blocks if cyc == 31]
    assert held, (
        "The block fetch held during the stall should reach the fetch buffer in cycle "
        "31, the first cycle it is ready again, but no block arrived then"
    )
    await env.run_until_blocks(len(env.blocks) + 8, max_cycles=60)
    await env.finish()
    cocotb.log.info(
        f"Fetch held block {held[0]:#x} through the stall and sent {len(during)} "
        f"request(s) meanwhile, then resumed; all {len(env.blocks)} blocks matched the "
        f"reference stream"
    )


@cocotb.test()
async def test_fetch_fault(dut):
    """imem_resp_err on block 0x1040 sets fetch_err on all of its slots and no others."""
    env = await start(dut, "test_fetch_fault", boot_addr=0x1000, fault_blocks={0x1040})
    await env.run_until_blocks(8, max_cycles=30)

    assert env.fault_slots == FETCH_W, (
        f"Exactly the {FETCH_W} slots of block 0x1040 should have fetch_err set, but "
        f"{env.fault_slots} did"
    )
    await env.finish()
    cocotb.log.info("All 4 slots of block 0x1040 had fetch_err set; no other slot did")


@cocotb.test()
async def test_random_traffic(dut):
    """20000 cycles of random memory latency, backpressure, redirects and faults."""
    env = await start(
        dut,
        "test_random_traffic",
        boot_addr=0x4000,
        latency=(1, 4),
        req_ready_pct=70,
        fb_ready_pct=65,
        redirect_pct=3,
        fault_blocks={0x4040, 0x4200, 0x43F0},
        verbose=False,
    )
    for part in range(4):
        await env.run(5000)
        cocotb.log.info(
            f"After {env.cycle} cycles: {len(env.blocks)} blocks matched the reference "
            f"stream, {len(env.redirects)} redirects, {env.stale_responses} stale "
            f"responses dropped, {env.fault_slots} fault slots"
        )

    assert len(env.blocks) > env.cycle // 20, (
        f"Fetch should keep making progress, but only {len(env.blocks)} blocks arrived "
        f"in {env.cycle} cycles"
    )
    assert env.stale_responses > 0 and env.fault_slots > 0, (
        "The random run should cover stale responses and faults, but "
        f"saw {env.stale_responses} stale responses and {env.fault_slots} fault slots"
    )
    await env.finish()
    cocotb.log.info(
        f"Random traffic passed: at most {env.max_in_flight} request(s) were in flight "
        f"at once and every handshake held steady until it completed"
    )
