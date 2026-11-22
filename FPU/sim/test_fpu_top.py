"""cocotb tests for fpu_top, the FPnew wrapper on port P2.

Run from this directory:

    make

fpu_model.py predicts the result and fflags of every uop bit-exactly. The FPU
finishes uops out of order and with different latencies, so FpuEnv keeps a
scoreboard keyed by rob_id: every uop the FPU accepts goes in, and every cycle
the writeback bus is checked against the entry for the rob_id it shows. A
result for a rob_id that is not in flight (for example one that a flush should
have dropped) is an error, and so is a uop that never comes back.

Every cycle also checks:
  - wakeup is valid in the same cycle as wb, only when rd_en is set, with prd;
  - the branch fields and exc_tval of wb are tied to 0;
  - in_ready is 0 while in_valid is 0 or flush is 1;
  - nothing appears on wb or wakeup in a flush cycle.

uop fields the FPU does not use are randomized, unused operand ports carry
random values, and all inputs are random while in_valid is 0.

Set COCOTB_RANDOM_SEED=<n> to repeat a run, and FPU_RANDOM_PER_CASE=<n> to
change the number of random operand sets per (op, rounding mode) pair.
"""

import os
import random
from collections import Counter, namedtuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

from fpu_model import (
    ADD, CLASS, CVT_S_W, CVT_S_WU, CVT_W_S, CVT_WU_S, DIV, DYN, EQ, FMA_OPS, KNOWN_ANSWERS,
    LE, LT, MADD, MAX, MIN, MUL, MV_W_X, MV_X_W, NUM_OPS, NUM_SRCS, OP_NAMES,
    RM_NAMES, RMM, RNE, ROUNDING_OPS, RTZ, SGNJ, SGNJN, SGNJX, SIGN, SQRT, SUB,
    describe, describe_flags, fpu_model, resolve_rm,
)  # fmt: skip

XLEN = 32
MASK = (1 << XLEN) - 1
EXC_ILLEGAL_INSTR = 2
NUM_ROB_IDS = 1 << 6  # rob_id_t is 6 bits

# The slowest uop is an FDIV/FSQRT that also waits behind other results for wb.
MAX_LATENCY = 64

RANDOM_PER_CASE = int(os.environ.get("FPU_RANDOM_PER_CASE") or "150")

# Packed structs from andromeda_pkg.sv. The first field is the MSB.
UOP_FIELDS = [
    ("pc", 32), ("fu_type", 3), ("op", 6),
    ("rs1_arch", 5), ("rs2_arch", 5), ("rs3_arch", 5),
    ("rs1_en", 1), ("rs2_en", 1), ("rs3_en", 1),
    ("rs1_fp", 1), ("rs2_fp", 1),
    ("rd_arch", 5), ("rd_en", 1), ("rd_fp", 1),
    ("imm", 32), ("use_imm", 1), ("rm", 3),
    ("mem_size", 2), ("mem_signed", 1),
    ("pred_taken", 1), ("pred_target", 32),
    ("exc_valid", 1), ("exc_cause", 5), ("serialize", 1),
    ("prs1", 7), ("prs2", 7), ("prs3", 7),
    ("prs1_ready", 1), ("prs2_ready", 1), ("prs3_ready", 1),
    ("prd", 7), ("old_prd", 7),
    ("rob_id", 6), ("lq_id", 4), ("sq_id", 4),
]  # fmt: skip
WB_FIELDS = [
    ("valid", 1), ("rob_id", 6), ("rd_en", 1), ("prd", 7), ("data", 32),
    ("exc_valid", 1), ("exc_cause", 5), ("exc_tval", 32), ("fflags", 5),
    ("br_valid", 1), ("br_taken", 1), ("br_target", 32), ("br_mispredict", 1),
]  # fmt: skip
WAKEUP_FIELDS = [("valid", 1), ("ptag", 7)]

# wb_t fields the FPU never uses. They must read 0 in every cycle.
WB_TIED_TO_ZERO = ["exc_tval", "br_valid", "br_taken", "br_target", "br_mispredict"]

# Float32 operands on the edges of every unit.
SPECIALS = [
    0x00000000, 0x80000000,  # +0, -0
    0x3F800000, 0xBF800000,  # +1, -1
    0x3FC00000, 0x40000000,  # 1.5, 2
    0x00000001, 0x807FFFFF,  # smallest subnormal, -largest subnormal
    0x00800000, 0x80800001,  # smallest normal, just past it (negative)
    0x7F7FFFFF, 0xFF7FFFFF,  # +-largest finite
    0x7F800000, 0xFF800000,  # +-inf
    0x7FC00000, 0xFFC00001,  # quiet NaNs (canonical, negative with payload)
    0x7FA00000, 0xFF800001,  # signalling NaNs
    0x34000000, 0x4B000000,  # 2^-23, 2^23
]  # fmt: skip

INT_CORNERS = [
    0x00000000, 0x00000001, 0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 0x80000001,
    0x00FFFFFF, 0x01000000, 0x01000001, 0x01000003, 0xFEFFFFFF, 0xFFFFFF80,
]  # fmt: skip

Expected = namedtuple("Expected", "op a b c rm_field frm rob_id rd_en prd data fflags exc issue_cycle")
Completed = namedtuple("Completed", "cycle op rob_id latency")


def pack(fields, layout):
    """Build a packed struct value from a dict of field values."""
    value = 0
    for name, width in layout:
        value = (value << width) | (fields[name] & ((1 << width) - 1))
    return value


def split_fields(value, layout):
    """Split a packed struct value into {field: bit string}."""
    bits = str(value)
    fields, pos = {}, 0
    for name, width in layout:
        fields[name] = bits[pos : pos + width]
        pos += width
    return fields


def op_name(op):
    return OP_NAMES[op] if op < NUM_OPS else f"undefined op {op}"


class FpuEnv:
    """Drives fpu_top one cycle at a time and checks every output in every cycle."""

    def __init__(self, dut, *, seed, verbose=True):
        self.dut = dut
        self.rng = random.Random(seed)
        self.verbose = verbose

        self.frm = RNE  # value driven on the frm input
        self.outstanding = {}  # rob_id -> Expected, for every accepted uop not yet on wb

        self.cycle = 0
        self.completed = []  # one Completed for every result seen on wb
        self.dropped = 0  # accepted uops a flush removed before they finished
        self.wakeups = 0
        self.op_counts = Counter()
        self.flag_counts = Counter()  # how often each fflags bit was expected, for coverage

    # ---- helpers -------------------------------------------------------------
    def log(self, msg):
        if self.verbose:
            cocotb.log.info(f"Cycle {self.cycle}: {msg}")

    def chance(self, pct):
        return self.rng.randrange(100) < pct

    def bits_to_int(self, name, bits):
        assert set(bits) <= {"0", "1"}, (
            f"Cycle {self.cycle}: {name} should be 0 or 1 in every bit, but it is {bits}"
        )
        return int(bits, 2)

    def read_bit(self, name):
        return self.bits_to_int(name, str(getattr(self.dut, name).value))

    def random_uop(self, **overrides):
        """A uop with every field random except the ones named in overrides."""
        fields = {name: self.rng.getrandbits(width) for name, width in UOP_FIELDS}
        fields["op"] = self.rng.randrange(NUM_OPS)
        fields.update(overrides)
        return fields

    # ---- stimulus generators ---------------------------------------------------
    def random_f32(self):
        rng = self.rng
        style = rng.randrange(6)
        if style == 0:
            return rng.choice(SPECIALS)
        if style == 1:
            return rng.getrandbits(32)
        sign = rng.getrandbits(1) << 31
        if style == 2:
            exp = rng.randint(117, 137)  # around 1.0
        elif style == 3:
            exp = rng.randint(0, 12)  # subnormal and tiny results
        elif style == 4:
            exp = rng.randint(235, 254)  # results near overflow
        else:
            exp = rng.randint(1, 254)
        man = rng.choice([0, 1, 0x400000, 0x7FFFFF]) if self.chance(25) else rng.getrandbits(23)
        return sign | (exp << 23) | man

    def random_int(self):
        rng = self.rng
        style = rng.randrange(3)
        if style == 0:
            return rng.choice(INT_CORNERS)
        if style == 1:
            return rng.getrandbits(32)
        return (rng.getrandbits(rng.randint(1, 32)) * rng.choice([1, -1])) & MASK

    def random_f32_near_int_range(self):
        """Floats whose integer conversion lands near the int32/uint32 limits or near 0."""
        sign = self.rng.getrandbits(1) << 31
        exp = self.rng.choice([self.rng.randint(120, 160), self.rng.randint(155, 159)])
        return sign | (exp << 23) | self.rng.getrandbits(23)

    def random_operands(self, op):
        """Operands for op, biased towards cancellation and range edges."""
        rng = self.rng
        if op in (CVT_S_W, CVT_S_WU, MV_W_X):
            return self.random_int(), rng.getrandbits(32), rng.getrandbits(32)
        if op in (CVT_W_S, CVT_WU_S) and self.chance(60):
            return self.random_f32_near_int_range(), rng.getrandbits(32), rng.getrandbits(32)
        a, b, c = self.random_f32(), self.random_f32(), self.random_f32()
        if op in (ADD, SUB) and self.chance(30):
            # Near-total cancellation: b is about -a (ADD) or about a (SUB)
            target = a ^ SIGN if op == ADD else a
            b = (target + rng.randint(-3, 3)) & MASK
        if op in FMA_OPS and self.chance(30):
            product, _ = fpu_model(MUL, a, b, 0, RNE)
            c = ((product ^ (SIGN * rng.getrandbits(1))) + rng.randint(-3, 3)) & MASK
        return a, b, c

    # ---- cycle driver ------------------------------------------------------------
    async def reset(self):
        dut = self.dut
        dut.in_valid.value = 0
        dut.in_uop.value = 0
        dut.in_rs1.value = 0
        dut.in_rs2.value = 0
        dut.in_rs3.value = 0
        dut.frm.value = 0
        dut.flush.value = 0
        cocotb.log.info("Holding rst_n low for 3 cycles with every input idle")
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 3)
        await RisingEdge(dut.clk)
        dut.rst_n.value = 1
        cocotb.log.info("Reset released")

    async def step(self, req=None, *, flush=False):
        """Run one cycle. req is (uop, rs1, rs2, rs3), or None to leave in_valid low.

        Returns True if the FPU accepted req at the end of this cycle.
        """
        dut = self.dut
        await RisingEdge(dut.clk)
        self.cycle += 1

        valid = req is not None
        if valid:
            uop, rs1, rs2, rs3 = req
        else:
            uop = self.random_uop()
            rs1, rs2, rs3 = (self.rng.getrandbits(XLEN) for _ in range(3))
        dut.in_valid.value = int(valid)
        dut.in_uop.value = pack(uop, UOP_FIELDS)
        dut.in_rs1.value = rs1
        dut.in_rs2.value = rs2
        dut.in_rs3.value = rs3
        dut.frm.value = self.frm
        dut.flush.value = int(flush)

        await ReadOnly()
        ready = self.read_bit("in_ready")
        if not valid:
            assert not ready, f"Cycle {self.cycle}: in_valid is 0, so in_ready should be 0"
        if flush:
            assert not ready, f"Cycle {self.cycle}: flush is high, so in_ready should be 0"

        accepted = valid and ready and not flush
        if accepted:
            self.outstanding[uop["rob_id"]] = self.predict(uop, rs1, rs2, rs3)

        self.check_outputs(flush)

        if flush:
            if self.outstanding:
                self.log(f"flush drops {len(self.outstanding)} uops in flight: "
                         f"rob_ids {sorted(self.outstanding)}")
            self.dropped += len(self.outstanding)
            self.outstanding.clear()
        return accepted

    def predict(self, uop, rs1, rs2, rs3):
        op = uop["op"]
        rm = resolve_rm(op, uop["rm"], self.frm)
        if rm is None:
            data, fflags, exc = None, 0, True
        else:
            data, fflags = fpu_model(op, rs1, rs2, rs3, rm)
            exc = False
        return Expected(op=op, a=rs1, b=rs2, c=rs3, rm_field=uop["rm"], frm=self.frm,
                        rob_id=uop["rob_id"], rd_en=uop["rd_en"], prd=uop["prd"],
                        data=data, fflags=fflags, exc=exc, issue_cycle=self.cycle)

    def describe_uop(self, e):
        srcs = [e.a, e.b, e.c][: NUM_SRCS[e.op]]
        if e.op in (CVT_S_W, CVT_S_WU, MV_W_X):
            text = f"{op_name(e.op)} {e.a:#010x}"
        else:
            text = f"{op_name(e.op)} " + ", ".join(describe(x) for x in srcs)
        rm = RM_NAMES[e.rm_field]
        if e.rm_field == DYN:
            rm += f" (frm={RM_NAMES[e.frm]})"
        return f"{text} rm={rm} rob_id={e.rob_id}"

    def check_outputs(self, flush):
        c = self.cycle
        wb = split_fields(self.dut.wb.value, WB_FIELDS)
        wakeup = split_fields(self.dut.wakeup.value, WAKEUP_FIELDS)

        for name in WB_TIED_TO_ZERO:
            assert self.bits_to_int(f"wb.{name}", wb[name]) == 0, (
                f"Cycle {c}: wb.{name} is unused by the FPU and should be 0, but it is {wb[name]}"
            )

        wb_valid = self.bits_to_int("wb.valid", wb["valid"])
        wakeup_valid = self.bits_to_int("wakeup.valid", wakeup["valid"])

        if flush:
            assert not wb_valid and not wakeup_valid, (
                f"Cycle {c}: flush is high, so wb.valid and wakeup.valid should be 0, "
                f"but they are {wb_valid} and {wakeup_valid}"
            )
            return
        if not wb_valid:
            assert not wakeup_valid, (
                f"Cycle {c}: wb.valid is 0, so wakeup.valid should be 0 too "
                f"(wakeup.ptag bits {wakeup['ptag']})"
            )
            return

        rob_id = self.bits_to_int("wb.rob_id", wb["rob_id"])
        assert rob_id in self.outstanding, (
            f"Cycle {c}: wb shows rob_id {rob_id}, but no uop with that rob_id is in flight "
            f"(in flight: {sorted(self.outstanding)}). It was never issued, already "
            f"finished, or should have been dropped by a flush."
        )
        e = self.outstanding.pop(rob_id)
        what = self.describe_uop(e)

        rd_en = self.bits_to_int("wb.rd_en", wb["rd_en"])
        prd = self.bits_to_int("wb.prd", wb["prd"])
        assert rd_en == e.rd_en, f"Cycle {c}: {what}: wb.rd_en should be {e.rd_en}, but it is {rd_en}"
        assert prd == e.prd, f"Cycle {c}: {what}: wb.prd should be {e.prd}, but it is {prd}"

        exc_valid = self.bits_to_int("wb.exc_valid", wb["exc_valid"])
        fflags = self.bits_to_int("wb.fflags", wb["fflags"])
        data = self.bits_to_int("wb.data", wb["data"])
        if e.exc:
            assert exc_valid, (
                f"Cycle {c}: {what}: the rounding mode is reserved, so wb.exc_valid should be 1"
            )
            cause = self.bits_to_int("wb.exc_cause", wb["exc_cause"])
            assert cause == EXC_ILLEGAL_INSTR, (
                f"Cycle {c}: {what}: wb.exc_cause should be {EXC_ILLEGAL_INSTR} "
                f"(illegal instruction), but it is {cause}"
            )
            assert fflags == 0, (
                f"Cycle {c}: {what}: a trapping uop should raise no fflags, "
                f"but wb.fflags is {describe_flags(fflags)}"
            )
        else:
            assert not exc_valid, f"Cycle {c}: {what}: wb.exc_valid should be 0, but it is 1"
            int_result = e.op in (EQ, LT, LE, CLASS, CVT_W_S, CVT_WU_S, MV_X_W)
            show = (lambda x: f"{x:#010x}") if int_result else describe
            assert data == e.data, (
                f"Cycle {c}: {what}: wb.data should be {show(e.data)}, but it is {show(data)}"
            )
            assert fflags == e.fflags, (
                f"Cycle {c}: {what} = {show(data)}: wb.fflags should be "
                f"{describe_flags(e.fflags)}, but it is {describe_flags(fflags)}"
            )
            for bit in range(5):
                if e.fflags >> bit & 1:
                    self.flag_counts[bit] += 1

        assert wakeup_valid == e.rd_en, (
            f"Cycle {c}: {what}: rd_en is {e.rd_en}, so wakeup.valid should be {e.rd_en} "
            f"in the same cycle as wb, but it is {wakeup_valid}"
        )
        if e.rd_en:
            ptag = self.bits_to_int("wakeup.ptag", wakeup["ptag"])
            assert ptag == e.prd, f"Cycle {c}: {what}: wakeup.ptag should be {e.prd}, but it is {ptag}"
            self.wakeups += 1

        latency = c - e.issue_cycle
        assert latency <= MAX_LATENCY, (
            f"Cycle {c}: {what} took {latency} cycles, more than the {MAX_LATENCY} allowed"
        )
        self.completed.append(Completed(c, e.op, rob_id, latency))
        self.op_counts[e.op] += 1
        self.log(f"{what} -> {data:#010x} {describe_flags(fflags)}"
                 f"{' EXC' if exc_valid else ''} after {latency} cycles  OK")

    # ---- higher-level actions ---------------------------------------------------
    async def issue(self, op, a, b=None, c=None, *, rm=RNE, flush=False, **fields):
        """Present one uop until the FPU accepts it. Returns the cycles spent waiting.

        Unused operand ports get random values. With flush=True the uop is presented
        for one cycle together with flush, must not be accepted, and None is returned.
        """
        rng = self.rng
        if b is None or NUM_SRCS[op] < 2:
            b = rng.getrandbits(XLEN)
        if c is None or NUM_SRCS[op] < 3:
            c = rng.getrandbits(XLEN)
        rob_id = fields.pop("rob_id", None)
        if rob_id is None:
            while len(self.outstanding) >= NUM_ROB_IDS:
                await self.step()
            rob_id = rng.choice([i for i in range(NUM_ROB_IDS) if i not in self.outstanding])
        assert rob_id not in self.outstanding, f"Test bug: rob_id {rob_id} is already in flight"
        uop = self.random_uop(op=op, rm=rm, rob_id=rob_id, **fields)
        if flush:
            accepted = await self.step((uop, a, b, c), flush=True)
            assert not accepted
            return None
        for waited in range(4 * MAX_LATENCY):
            if await self.step((uop, a, b, c)):
                return waited
        raise AssertionError(
            f"Cycle {self.cycle}: in_ready stayed 0 for {4 * MAX_LATENCY} cycles "
            f"while {op_name(op)} (rob_id {rob_id}) waited to issue"
        )

    async def idle(self, cycles=1, *, flush=False):
        for _ in range(cycles):
            await self.step(flush=flush)

    async def drain(self):
        """Idle until every accepted uop has come back on wb."""
        for _ in range(4 * MAX_LATENCY):
            if not self.outstanding:
                return
            await self.step()
        left = [self.describe_uop(e) for e in self.outstanding.values()]
        raise AssertionError(f"Cycle {self.cycle}: these uops never came back on wb: {left}")


def check_struct_widths(dut):
    for handle, layout, name in (
        (dut.in_uop, UOP_FIELDS, "uop_t"),
        (dut.wb, WB_FIELDS, "wb_t"),
        (dut.wakeup, WAKEUP_FIELDS, "wakeup_t"),
    ):
        bits = sum(width for _, width in layout)
        assert len(handle) == bits, (
            f"This testbench expects {name} to be {bits} bits, but the DUT port is "
            f"{len(handle)}; update the field list in test_fpu_top.py to match the package"
        )


async def start(dut, name, *, verbose=True):
    seed = cocotb.RANDOM_SEED
    cocotb.log.info(f"Starting {name} with seed {seed} and a 10 ns clock")
    check_struct_widths(dut)
    Clock(dut.clk, 10, unit="ns").start()
    env = FpuEnv(dut, seed=seed, verbose=verbose)
    await env.reset()
    return env


# =============================================================================
# Tests
# =============================================================================
@cocotb.test()
async def test_idle_after_reset(dut):
    """With in_valid low and random junk on every input, nothing comes out."""
    env = await start(dut, "test_idle_after_reset")
    for frm in range(8):
        env.frm = frm
        await env.idle(5)
    assert not env.completed, f"Nothing was issued, but {len(env.completed)} results appeared"


@cocotb.test()
async def test_known_answers(dut):
    """The hand-worked answers from fpu_model.py, one of each kind of special case."""
    env = await start(dut, "test_known_answers")
    for op, a, b, c, rm, want, want_flags in KNOWN_ANSWERS:
        got = fpu_model(op, a, b, c, rm)
        assert got == (want, want_flags), f"Reference model bug on {op_name(op)}; run fpu_model.py"
        await env.issue(op, a, b, c, rm=rm)
    await env.drain()
    assert len(env.completed) == len(KNOWN_ANSWERS)
    cocotb.log.info(f"All {len(KNOWN_ANSWERS)} hand-worked answers matched")


@cocotb.test()
async def test_special_operands(dut):
    """Every op on every pair of special operands (FMA: random triples), random rm."""
    env = await start(dut, "test_special_operands", verbose=False)
    rng = env.rng
    count = 0
    for op in range(NUM_OPS):
        if op in FMA_OPS:
            cases = [tuple(rng.choice(SPECIALS) for _ in range(3)) for _ in range(len(SPECIALS) ** 2)]
        elif NUM_SRCS[op] == 1:
            pool = INT_CORNERS if op in (CVT_S_W, CVT_S_WU, MV_W_X) else SPECIALS
            cases = [(a, None, None) for a in pool]
        else:
            cases = [(a, b, None) for a in SPECIALS for b in SPECIALS]
        for a, b, c in cases:
            await env.issue(op, a, b, c, rm=rng.randrange(5))
            count += 1
        cocotb.log.info(f"{op_name(op)}: {len(cases)} special-operand cases issued")
    await env.drain()
    assert len(env.completed) == count
    cocotb.log.info(f"All {count} special-operand results matched")


@cocotb.test()
async def test_random_rounding(dut):
    """Every rounding op in every static rounding mode on random operands."""
    env = await start(dut, "test_random_rounding", verbose=False)
    count = 0
    for op in ROUNDING_OPS:
        for rm in range(5):
            for _ in range(RANDOM_PER_CASE):
                a, b, c = env.random_operands(op)
                await env.issue(op, a, b, c, rm=rm)
                count += 1
        cocotb.log.info(f"{op_name(op)}: {5 * RANDOM_PER_CASE} random cases issued")
    await env.drain()
    assert len(env.completed) == count
    names = ["NX", "UF", "OF", "DZ", "NV"]
    seen = ", ".join(f"{names[b]} {env.flag_counts[b]}" for b in range(5))
    cocotb.log.info(f"All {count} results matched. Expected flag counts: {seen}")
    for bit in (0, 1, 2, 4):  # NX, UF, OF, NV must all have been exercised
        assert env.flag_counts[bit], f"No case raised {names[bit]}; the stimulus is too narrow"


@cocotb.test()
async def test_non_rounding_random(dut):
    """Sign injection, min/max, compares, classify and moves on random operands."""
    env = await start(dut, "test_non_rounding_random", verbose=False)
    ops = [SGNJ, SGNJN, SGNJX, MIN, MAX, EQ, LT, LE, CLASS, MV_X_W, MV_W_X]
    count = 0
    for op in ops:
        for _ in range(4 * RANDOM_PER_CASE):
            a, b, c = env.random_operands(op)
            if op in (EQ, LT, LE, MIN, MAX) and env.chance(25):
                b = a if env.chance(50) else a ^ SIGN  # equal values and +-0 pairs
            # rm is not a rounding mode for these ops; any value must work
            await env.issue(op, a, b, c, rm=env.rng.randrange(8))
            count += 1
    await env.drain()
    assert len(env.completed) == count
    cocotb.log.info(f"All {count} non-rounding results matched")


@cocotb.test()
async def test_dynamic_rm(dut):
    """rm = DYN takes the mode from frm, sampled in the cycle the uop is accepted."""
    env = await start(dut, "test_dynamic_rm", verbose=False)
    count = 0
    for frm in range(5):
        env.frm = frm
        for op in ROUNDING_OPS:
            for _ in range(10):
                a, b, c = env.random_operands(op)
                await env.issue(op, a, b, c, rm=DYN)
                count += 1
    cocotb.log.info("frm now changes every cycle while ADDs issue back to back")
    for _ in range(200):
        env.frm = env.rng.randrange(5)
        a, b, c = env.random_operands(ADD)
        await env.issue(ADD, a, b, c, rm=DYN)
        count += 1
    await env.drain()
    assert len(env.completed) == count
    cocotb.log.info(f"All {count} dynamic-rounding results matched")


@cocotb.test()
async def test_illegal_rounding_mode(dut):
    """A reserved rm (static 101/110, or DYN with frm 101/110/111) traps as illegal."""
    env = await start(dut, "test_illegal_rounding_mode")
    count = 0
    for op in ROUNDING_OPS:
        a, b, c = env.random_operands(op)
        for rm in (5, 6):
            env.frm = RNE
            await env.issue(op, a, b, c, rm=rm)
            count += 1
        for frm in (5, 6, 7):
            env.frm = frm
            await env.issue(op, a, b, c, rm=DYN)
            count += 1
    cocotb.log.info("Ops that do not round must ignore a reserved rm or frm")
    env.frm = 7
    for op in (SGNJ, SGNJN, SGNJX, MIN, MAX, EQ, LT, LE, CLASS, MV_X_W, MV_W_X):
        for rm in (5, 6, 7):
            a, b, c = env.random_operands(op)
            await env.issue(op, a, b, c, rm=rm)
            count += 1
    await env.drain()
    assert len(env.completed) == count


@cocotb.test()
async def test_tags_and_rd_en(dut):
    """rob_id and prd come back unchanged; rd_en = 0 gives a wb with no wakeup."""
    env = await start(dut, "test_tags_and_rd_en", verbose=False)
    rng = env.rng
    for rob_id in range(NUM_ROB_IDS):
        op = rng.randrange(NUM_OPS)
        # Walk prd through every value of both files across the rob_ids
        prd = (rob_id * 2 + rng.getrandbits(1)) & 0x7F
        await env.issue(op, *env.random_operands(op), rm=rng.randrange(5),
                        rob_id=rob_id, rd_en=rob_id & 1, prd=prd)
    await env.drain()
    assert len(env.completed) == NUM_ROB_IDS
    no_wakeup = len(env.completed) - env.wakeups
    assert no_wakeup == NUM_ROB_IDS // 2, f"{no_wakeup} uops sent no wakeup; expected {NUM_ROB_IDS // 2}"
    cocotb.log.info(f"All {NUM_ROB_IDS} rob_ids came back; the {no_wakeup} with rd_en = 0 sent no wakeup")


@cocotb.test()
async def test_out_of_order_completion(dut):
    """Short ops issued behind a divide finish first; the divider stalls only DIV/SQRT."""
    env = await start(dut, "test_out_of_order_completion")
    one, three = 0x3F800000, 0x40400000

    div_rob = 0
    await env.issue(DIV, one, three, rob_id=div_rob)
    for _ in range(4):
        waited = await env.issue(ADD, *env.random_operands(ADD)[:2])
        assert waited == 0, "An ADD waited behind the divider; only DIV/SQRT should"
    for _ in range(4):
        # May wait a cycle if it meets an ADD result at the wb arbiter, but not for the divider
        await env.issue(SGNJ, *env.random_operands(SGNJ)[:2])
    await env.drain()
    div_done = next(done for done in env.completed if done.rob_id == div_rob and done.op == DIV)
    later = [done for done in env.completed if done.cycle > div_done.cycle]
    assert not later, f"These uops finished after the DIV that was issued before them: {later}"
    cocotb.log.info(f"The DIV took {div_done.latency} cycles; all 8 younger uops finished first")

    cocotb.log.info("Three DIVs back to back: at least one must wait for the divider")
    waits = []
    for _ in range(3):
        waits.append(await env.issue(DIV, *env.random_operands(DIV)[:2]))
    await env.drain()
    assert sum(waits) > 0, f"Three DIVs issued without any stall ({waits}); the divider is iterative"
    cocotb.log.info(f"Cycles each DIV waited to issue: {waits}")


@cocotb.test()
async def test_back_to_back(dut):
    """One ADD per cycle flows through with no stalls; mixed groups collide safely on wb."""
    env = await start(dut, "test_back_to_back", verbose=False)
    for _ in range(200):
        waited = await env.issue(ADD, *env.random_operands(ADD)[:2])
        assert waited == 0, "A stream of ADDs alone should never stall"
    await env.drain()
    cycles = [done.cycle for done in env.completed]
    gaps = {b - a for a, b in zip(cycles, cycles[1:])}
    assert gaps == {1}, f"200 back-to-back ADDs should give one result per cycle, gaps {sorted(gaps)}"

    cocotb.log.info("Alternating op groups with different latencies, issued every cycle")
    ops = [ADD, SGNJ, CVT_S_W, MADD, MIN, CVT_W_S, MUL, EQ]
    for i in range(400):
        op = ops[i % len(ops)]
        await env.issue(op, *env.random_operands(op), rm=env.rng.randrange(5))
    await env.drain()
    assert len(env.completed) == 600


@cocotb.test()
async def test_flush(dut):
    """flush drops everything in flight, including a divide at any stage, and the FPU recovers."""
    env = await start(dut, "test_flush", verbose=False)
    one, three = 0x3F800000, 0x40400000
    for offset in range(MAX_LATENCY // 2):
        await env.issue(DIV, one, three)
        await env.issue(MADD, *env.random_operands(MADD))
        await env.issue(CVT_S_W, env.random_int())
        await env.idle(offset)
        # A uop presented with flush must not be accepted
        await env.issue(ADD, one, three, flush=True)
        # The divider must be free again straight away
        waited = await env.issue(DIV, three, one)
        assert waited == 0, f"After a flush, a DIV waited {waited} cycles to issue"
        if env.chance(50):
            await env.issue(SQRT, three)
        else:
            await env.issue(ADD, one, three)
        await env.drain()
    assert env.dropped > 0, "No flush caught a uop in flight"
    cocotb.log.info(f"{len(env.completed)} results matched; flushes dropped {env.dropped} uops")


@cocotb.test()
async def test_random_mix(dut):
    """Random ops, rounding modes, gaps, frm changes and flushes, all checked by the scoreboard."""
    env = await start(dut, "test_random_mix", verbose=False)
    rng = env.rng
    issued = 0
    for _ in range(20 * RANDOM_PER_CASE):
        if env.chance(5):
            env.frm = rng.choice([0, 1, 2, 3, 4, 4, 5, 6, 7])
        if env.chance(30):
            await env.idle(rng.randint(1, 3))
        if env.chance(1):
            await env.idle(1, flush=True)
            continue
        op = rng.randrange(NUM_OPS)
        rm = rng.choice([RNE, RTZ, 2, 3, RMM, DYN, DYN] + ([5, 6] if env.chance(3) else []))
        await env.issue(op, *env.random_operands(op), rm=rm)
        issued += 1
    await env.drain()
    cocotb.log.info(f"{issued} uops issued, {len(env.completed)} results matched, "
                    f"{env.dropped} dropped by flushes")
    counts = ", ".join(f"{op_name(op)} {env.op_counts[op]}" for op in range(NUM_OPS))
    cocotb.log.info(f"Results per op: {counts}")
