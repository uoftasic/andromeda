"""cocotb tests for the integer ALU, port P0 (Spec 07, Sections 2.1, 3.1 and 5).

Run from ALU/sim:

    make

A Python reference model predicts the writeback bus for every uop. AluEnv checks
every output in every cycle against that prediction, so each test only has to
choose the stimulus. Nothing here covers alu_bru: branches, jumps and CSRs have
their own items in the Spec 07 checklist and that module is still a stub.

Every uop field the ALU doesn't use is randomized, and the inputs carry random
values while in_valid is 0, so a dependence on the wrong field shows up as a
mismatch.

Set COCOTB_RANDOM_SEED=<n> to repeat a run; each test logs the seed it used.
"""

import random
from collections import Counter, namedtuple

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge, Timer

XLEN = 32
MASK = (1 << XLEN) - 1
SIGN_BIT = 1 << (XLEN - 1)

# alu_op_e from pkg/andromeda_pkg.sv, in encoding order.
OP_NAMES = ["ADD", "SUB", "SLL", "SLT", "SLTU", "XOR", "SRL", "SRA", "OR", "AND", "LUI", "AUIPC"]
(
    ALU_ADD,
    ALU_SUB,
    ALU_SLL,
    ALU_SLT,
    ALU_SLTU,
    ALU_XOR,
    ALU_SRL,
    ALU_SRA,
    ALU_OR,
    ALU_AND,
    ALU_LUI,
    ALU_AUIPC,
) = range(len(OP_NAMES))
NUM_OPS = len(OP_NAMES)
NUM_OP_CODES = 1 << 6  # uop_t.op is OP_W = 6 bits wide

# Packed structs from pkg/andromeda_pkg.sv. The first field is the MSB.
UOP_FIELDS = [
    ("pc", 32),
    ("fu_type", 3),
    ("op", 6),
    ("rs1_arch", 5),
    ("rs2_arch", 5),
    ("rs3_arch", 5),
    ("rs1_en", 1),
    ("rs2_en", 1),
    ("rs3_en", 1),
    ("rs1_fp", 1),
    ("rs2_fp", 1),
    ("rd_arch", 5),
    ("rd_en", 1),
    ("rd_fp", 1),
    ("imm", 32),
    ("use_imm", 1),
    ("rm", 3),
    ("mem_size", 2),
    ("mem_signed", 1),
    ("pred_taken", 1),
    ("pred_target", 32),
    ("exc_valid", 1),
    ("exc_cause", 5),
    ("serialize", 1),
    ("prs1", 7),
    ("prs2", 7),
    ("prs3", 7),
    ("prs1_ready", 1),
    ("prs2_ready", 1),
    ("prs3_ready", 1),
    ("prd", 7),
    ("old_prd", 7),
    ("rob_id", 6),
    ("lq_id", 4),
    ("sq_id", 4),
]
WB_FIELDS = [
    ("valid", 1),
    ("rob_id", 6),
    ("rd_en", 1),
    ("prd", 7),
    ("data", 32),
    ("exc_valid", 1),
    ("exc_cause", 5),
    ("exc_tval", 32),
    ("fflags", 5),
    ("br_valid", 1),
    ("br_taken", 1),
    ("br_target", 32),
    ("br_mispredict", 1),
]
WAKEUP_FIELDS = [("valid", 1), ("ptag", 7)]

# wb_t fields the ALU never uses. They must read 0 in every cycle.
WB_TIED_TO_ZERO = [
    "exc_valid",
    "exc_cause",
    "exc_tval",
    "fflags",
    "br_valid",
    "br_taken",
    "br_target",
    "br_mispredict",
]

# Operand values that sit on the edges of the adder, the comparators and the shifter.
CORNERS = [
    0x00000000,
    0x00000001,
    0x00000002,
    0x0000001F,
    0x00000020,
    0x7FFFFFFE,
    0x7FFFFFFF,
    0x80000000,
    0x80000001,
    0xAAAAAAAA,
    0x55555555,
    0xFFFFFFFE,
    0xFFFFFFFF,
]

Expected = namedtuple("Expected", "op rs1 rs2 rob_id rd_en prd data")
Completed = namedtuple("Completed", "cycle op rob_id rd_en")


def to_signed(x):
    """Read a 32-bit value as a two's complement Python int."""
    return x - (1 << XLEN) if x & SIGN_BIT else x


def alu_model(op, a, b):
    """The result the ALU should produce for op with operands a and b.

    Spec 07 Section 2.1: rs2 already holds the immediate when use_imm is set, and
    rs1 already holds the PC for AUIPC, so only the two operands matter here.
    Op codes outside alu_op_e give 0, as the RTL's default arm does.
    """
    shamt = b & (XLEN - 1)
    if op in (ALU_ADD, ALU_AUIPC):
        return (a + b) & MASK
    if op == ALU_SUB:
        return (a - b) & MASK
    if op == ALU_SLL:
        return (a << shamt) & MASK
    if op == ALU_SLT:
        return int(to_signed(a) < to_signed(b))
    if op == ALU_SLTU:
        return int(a < b)
    if op == ALU_XOR:
        return a ^ b
    if op == ALU_SRL:
        return a >> shamt
    if op == ALU_SRA:
        return (to_signed(a) >> shamt) & MASK
    if op == ALU_OR:
        return a | b
    if op == ALU_AND:
        return a & b
    if op == ALU_LUI:
        return b
    return 0


def op_name(op):
    return OP_NAMES[op] if op < NUM_OPS else f"undefined op {op}"


def pack(fields, layout):
    """Build a packed struct value from a dict of field values."""
    value = 0
    for name, width in layout:
        value = (value << width) | (fields[name] & ((1 << width) - 1))
    return value


def split_fields(value, layout):
    """Split a packed struct value into {field: bit string}.

    Fields stay as strings because the payload may legally hold X while valid is
    low (README: unreset datapath registers), so converting has to be per field.
    """
    bits = str(value)
    fields, pos = {}, 0
    for name, width in layout:
        fields[name] = bits[pos : pos + width]
        pos += width
    return fields


class AluEnv:
    """Drives the ALU and checks every output in every cycle.

    Each step drives the inputs just after the rising edge, waits for the outputs
    to settle, compares them with the result due this cycle, then works out which
    result is due next cycle.
    """

    def __init__(self, dut, *, seed, verbose=True):
        self.dut = dut
        self.rng = random.Random(seed)
        self.verbose = verbose  # log every uop that completes or is dropped

        # Reference model: the result the writeback bus should show this cycle.
        self.expected = None

        # Record of what happened, for the tests' own checks.
        self.cycle = 0
        self.completed = []  # one Completed for every uop seen on the writeback bus
        self.dropped = 0  # uops issued in the same cycle as a flush
        self.wakeups = 0
        self.op_counts = Counter()

    def log(self, msg):
        if self.verbose:
            cocotb.log.info(f"Cycle {self.cycle}: {msg}")

    def chance(self, pct):
        return self.rng.randrange(100) < pct

    def bits_to_int(self, name, bits):
        """Convert a bit string to an int, failing clearly if it holds X or Z."""
        assert set(bits) <= {"0", "1"}, (
            f"Cycle {self.cycle}: {name} should be 0 or 1 in every bit, but it is {bits}"
        )
        return int(bits, 2)

    def random_uop(self, **overrides):
        """A uop with every field random except the ones named in overrides."""
        fields = {name: self.rng.getrandbits(width) for name, width in UOP_FIELDS}
        fields["op"] = self.rng.randrange(NUM_OPS)
        fields.update(overrides)
        return fields

    def random_operands(self):
        """Random operands, biased towards values that stress compares and shifts."""
        rng = self.rng
        style = rng.randrange(4)
        if style == 0:
            return rng.choice(CORNERS), rng.choice(CORNERS)
        a = rng.getrandbits(XLEN)
        if style == 1:
            return a, rng.getrandbits(XLEN)
        if style == 2:
            return a, (a + rng.randint(-1, 1)) & MASK  # equal or off by one
        return a, rng.randrange(XLEN)  # small, like a shift amount

    async def reset(self):
        """Hold rst_n low for 3 cycles with every input idle, then release it."""
        dut = self.dut
        dut.in_valid.value = 0
        dut.in_uop.value = 0
        dut.in_rs1.value = 0
        dut.in_rs2.value = 0
        dut.flush.value = 0
        cocotb.log.info("Holding rst_n low for 3 cycles with every input idle")
        dut.rst_n.value = 0
        await ClockCycles(dut.clk, 3)
        await ReadOnly()
        self.expected = None
        self.check_outputs()
        await RisingEdge(dut.clk)
        dut.rst_n.value = 1
        cocotb.log.info("Reset released; the outputs were idle during reset")

    async def step(self, *, uop=None, rs1=0, rs2=0, flush=False, reset=False):
        """Run one cycle. uop=None leaves in_valid low."""
        dut = self.dut
        await RisingEdge(dut.clk)
        self.cycle += 1

        valid = uop is not None
        driven = uop if valid else self.random_uop()
        dut.in_valid.value = int(valid)
        dut.in_uop.value = pack(driven, UOP_FIELDS)
        dut.in_rs1.value = rs1 if valid else self.rng.getrandbits(XLEN)
        dut.in_rs2.value = rs2 if valid else self.rng.getrandbits(XLEN)
        dut.flush.value = int(flush)
        dut.rst_n.value = int(not reset)

        await ReadOnly()
        if reset:
            self.expected = None  # asynchronous: rst_n clears wb.valid straight away
        self.check_outputs()
        self.expected = self.predict(uop, rs1, rs2, flush, reset)

    def predict(self, uop, rs1, rs2, flush, reset):
        """Work out what the writeback bus should show in the next cycle."""
        if uop is None or reset:
            return None
        if flush:
            self.dropped += 1
            self.log(f"flush is high, so {op_name(uop['op'])} (rob_id {uop['rob_id']}) is dropped")
            return None
        return Expected(
            op=uop["op"],
            rs1=rs1,
            rs2=rs2,
            rob_id=uop["rob_id"],
            rd_en=uop["rd_en"],
            prd=uop["prd"],
            data=alu_model(uop["op"], rs1, rs2),
        )

    def check_outputs(self):
        c, want = self.cycle, self.expected

        ready = self.dut.in_ready.value
        assert ready.is_resolvable and int(ready) == 1, (
            f"Cycle {c}: in_ready should always be 1 (the ALU never stalls), but it is {ready}"
        )

        wb = split_fields(self.dut.wb.value, WB_FIELDS)
        for name in WB_TIED_TO_ZERO:
            assert self.bits_to_int(f"wb.{name}", wb[name]) == 0, (
                f"Cycle {c}: wb.{name} is unused by the ALU and should be tied to 0, "
                f"but it is {wb[name]}"
            )

        wakeup = split_fields(self.dut.wakeup.value, WAKEUP_FIELDS)
        wb_valid = self.bits_to_int("wb.valid", wb["valid"])
        wakeup_valid = self.bits_to_int("wakeup.valid", wakeup["valid"])

        if want is None:
            assert not wb_valid, (
                f"Cycle {c}: no uop is due, so wb.valid should be 0, but it is 1 "
                f"(rob_id bits {wb['rob_id']}, data bits {wb['data']})"
            )
            assert not wakeup_valid, (
                f"Cycle {c}: no uop is due, so wakeup.valid should be 0, but it is 1 "
                f"(ptag bits {wakeup['ptag']})"
            )
            return

        name = op_name(want.op)
        what = f"{name} rs1={want.rs1:#010x} rs2={want.rs2:#010x}"
        assert wb_valid, (
            f"Cycle {c}: {what} (rob_id {want.rob_id}) was issued last cycle, so wb.valid "
            f"should be 1, but it is 0"
        )

        rob_id = self.bits_to_int("wb.rob_id", wb["rob_id"])
        assert rob_id == want.rob_id, (
            f"Cycle {c}: {what} should report rob_id {want.rob_id}, but wb.rob_id is {rob_id}"
        )
        rd_en = self.bits_to_int("wb.rd_en", wb["rd_en"])
        assert rd_en == want.rd_en, (
            f"Cycle {c}: {what} should report rd_en {want.rd_en}, but wb.rd_en is {rd_en}"
        )
        prd = self.bits_to_int("wb.prd", wb["prd"])
        assert prd == want.prd, (
            f"Cycle {c}: {what} should report prd {want.prd}, but wb.prd is {prd}"
        )
        data = self.bits_to_int("wb.data", wb["data"])
        assert data == want.data, (
            f"Cycle {c}: {what} should give {want.data:#010x}, but wb.data is {data:#010x}"
        )

        assert wakeup_valid == want.rd_en, (
            f"Cycle {c}: {what} has rd_en {want.rd_en}, so wakeup.valid should be "
            f"{want.rd_en} in the same cycle as wb, but it is {wakeup_valid}"
        )
        if want.rd_en:
            ptag = self.bits_to_int("wakeup.ptag", wakeup["ptag"])
            assert ptag == want.prd, (
                f"Cycle {c}: {what} should wake up tag {want.prd}, but wakeup.ptag is {ptag}"
            )
            self.wakeups += 1

        self.completed.append(Completed(c, want.op, want.rob_id, want.rd_en))
        self.op_counts[want.op] += 1
        self.log(f"{what} = {data:#010x}, rob_id {rob_id}, prd {prd}, rd_en {rd_en}  OK")

    async def issue(self, op, rs1, rs2, *, flush=False, **fields):
        """Issue one uop this cycle."""
        await self.step(uop=self.random_uop(op=op, **fields), rs1=rs1, rs2=rs2, flush=flush)

    async def idle(self, cycles=1, *, flush=False):
        """Run cycles with in_valid low and random values on the inputs."""
        for _ in range(cycles):
            await self.step(flush=flush)

    async def finish(self):
        """Run one idle cycle so the result of the last uop issued gets checked."""
        await self.idle()


def check_struct_widths(dut):
    """Fail early if the structs in the package no longer match the layouts above."""
    for handle, layout, name in (
        (dut.in_uop, UOP_FIELDS, "uop_t"),
        (dut.wb, WB_FIELDS, "wb_t"),
        (dut.wakeup, WAKEUP_FIELDS, "wakeup_t"),
    ):
        bits = sum(width for _, width in layout)
        assert len(handle) == bits, (
            f"This testbench expects {name} to be {bits} bits, but the DUT port is "
            f"{len(handle)}; update the field list in test_alu.py to match the package"
        )


async def start(dut, name, *, verbose=True):
    """Start the clock, build the environment and reset the ALU."""
    seed = cocotb.RANDOM_SEED
    cocotb.log.info(f"Starting {name} with seed {seed} and a 10 ns clock")
    check_struct_widths(dut)
    Clock(dut.clk, 10, unit="ns").start()
    env = AluEnv(dut, seed=seed, verbose=verbose)
    await env.reset()
    return env


@cocotb.test()
async def test_idle_after_reset(dut):
    """With nothing issued, the outputs stay idle even with random values on the inputs."""
    env = await start(dut, "test_idle_after_reset")
    await env.idle(20)
    assert not env.completed, f"Nothing was issued, but {len(env.completed)} results appeared"
    cocotb.log.info("20 idle cycles passed with wb.valid and wakeup.valid low and in_ready high")


@cocotb.test()
async def test_known_answers(dut):
    """Hand-worked results for each op, which also guard the reference model itself."""
    env = await start(dut, "test_known_answers")
    cases = [
        (ALU_ADD, 2, 3, 5),
        (ALU_ADD, 0x7FFFFFFF, 1, 0x80000000),  # signed overflow wraps
        (ALU_ADD, 0xFFFFFFFF, 1, 0),  # carry out is dropped
        (ALU_SUB, 5, 3, 2),
        (ALU_SUB, 0, 1, 0xFFFFFFFF),
        (ALU_SUB, 0x80000000, 1, 0x7FFFFFFF),  # signed overflow wraps
        (ALU_SLL, 1, 31, 0x80000000),
        (ALU_SLL, 0xFFFFFFFF, 4, 0xFFFFFFF0),
        (ALU_SLL, 1, 32, 1),  # only rs2[4:0] counts
        (ALU_SLT, 0x80000000, 1, 1),  # most negative < 1
        (ALU_SLT, 1, 0x80000000, 0),
        (ALU_SLT, 0x7FFFFFFF, 0x80000000, 0),  # rs1 - rs2 overflows
        (ALU_SLT, 0x80000000, 0x7FFFFFFF, 1),  # rs1 - rs2 overflows
        (ALU_SLT, 5, 5, 0),
        (ALU_SLTU, 0x80000000, 1, 0),  # same bits as the SLT case, opposite answer
        (ALU_SLTU, 1, 0x80000000, 1),
        (ALU_SLTU, 0, 0xFFFFFFFF, 1),
        (ALU_SLTU, 5, 5, 0),
        (ALU_XOR, 0xFF00FF00, 0x0FF00FF0, 0xF0F0F0F0),
        (ALU_SRL, 0x80000000, 31, 1),
        (ALU_SRL, 0xF0000000, 4, 0x0F000000),  # zeros shift in
        (ALU_SRA, 0x80000000, 31, 0xFFFFFFFF),
        (ALU_SRA, 0xF0000000, 4, 0xFF000000),  # the sign shifts in
        (ALU_SRA, 0x40000000, 30, 1),  # positive values fill with zeros
        (ALU_OR, 0xFF00FF00, 0x0FF00FF0, 0xFFF0FFF0),
        (ALU_AND, 0xFF00FF00, 0x0FF00FF0, 0x0F000F00),
        (ALU_LUI, 0xDEADBEEF, 0x12345000, 0x12345000),  # rs1 is ignored
        (ALU_AUIPC, 0x00001000, 0x12345000, 0x12346000),
    ]
    for op, a, b, want in cases:
        got = alu_model(op, a, b)
        assert got == want, (
            f"Reference model bug: {op_name(op)} {a:#x}, {b:#x} should be {want:#x}, "
            f"but alu_model gives {got:#x}"
        )
        await env.issue(op, a, b)
    await env.finish()
    assert len(env.completed) == len(cases), (
        f"{len(cases)} uops were issued, but {len(env.completed)} results appeared"
    )
    cocotb.log.info(f"All {len(cases)} hand-worked results matched the ALU and the model")


@cocotb.test()
async def test_corner_operands(dut):
    """Every op with every pair of corner-case operands."""
    env = await start(dut, "test_corner_operands", verbose=False)
    for op in range(NUM_OPS):
        for a in CORNERS:
            for b in CORNERS:
                await env.issue(op, a, b)
        cocotb.log.info(f"{OP_NAMES[op]}: {len(CORNERS) ** 2} corner pairs issued")
    await env.finish()
    assert len(env.completed) == NUM_OPS * len(CORNERS) ** 2, (
        f"{NUM_OPS * len(CORNERS) ** 2} uops were issued, but {len(env.completed)} results appeared"
    )
    cocotb.log.info(f"All {len(env.completed)} corner cases matched the reference model")


@cocotb.test()
async def test_every_op_random(dut):
    """1000 random operand pairs per op, issued back to back."""
    env = await start(dut, "test_every_op_random", verbose=False)
    per_op = 1000
    for op in range(NUM_OPS):
        for _ in range(per_op):
            a, b = env.random_operands()
            await env.issue(op, a, b)
        cocotb.log.info(f"{OP_NAMES[op]}: {per_op} random operand pairs issued")
    await env.finish()
    for op in range(NUM_OPS):
        assert env.op_counts[op] == per_op, (
            f"{OP_NAMES[op]} should have completed {per_op} times, "
            f"but it completed {env.op_counts[op]} times"
        )
    cycles = [done.cycle for done in env.completed]
    gaps = {b - a for a, b in zip(cycles, cycles[1:])}
    assert gaps == {1}, (
        f"With a uop issued every cycle, a result should appear every cycle, "
        f"but the gaps between results were {sorted(gaps)}"
    )
    cocotb.log.info(f"All {len(env.completed)} results matched, one per cycle with no gaps")


@cocotb.test()
async def test_shift_amounts(dut):
    """Every shift amount from 0 to 31 on each shift op, with junk in rs2[31:5]."""
    env = await start(dut, "test_shift_amounts", verbose=False)
    patterns = [0x00000001, 0x80000000, 0x80000001, 0xFFFFFFFF, 0x7FFFFFFF, 0xA5A5A5A5]
    count = 0
    for op in (ALU_SLL, ALU_SRL, ALU_SRA):
        for shamt in range(XLEN):
            for value in patterns:
                await env.issue(op, value, shamt)
                await env.issue(op, value, (env.rng.getrandbits(XLEN - 5) << 5) | shamt)
                count += 2
        cocotb.log.info(f"{OP_NAMES[op]}: shift amounts 0 to 31 issued")
    await env.finish()
    assert len(env.completed) == count, (
        f"{count} shifts were issued, but {len(env.completed)} results appeared"
    )
    cocotb.log.info(f"All {count} shifts matched; only rs2[4:0] affected the result")


@cocotb.test()
async def test_signed_overflow(dut):
    """Operands where the 32-bit sum or difference overflows as a signed number."""
    env = await start(dut, "test_signed_overflow", verbose=False)
    rng = env.rng
    per_case = 250

    def big_positive():
        return rng.randint(0x40000000, 0x7FFFFFFF)

    def big_negative():
        return rng.randint(0x80000000, 0xBFFFFFFF)

    def overflows(exact):
        return not -(2**31) <= exact < 2**31

    count = 0
    for make_a, make_b in ((big_positive, big_positive), (big_negative, big_negative)):
        for _ in range(per_case):
            a, b = make_a(), make_b()
            assert overflows(to_signed(a) + to_signed(b)), (
                f"Test bug: {a:#x} + {b:#x} should overflow but does not"
            )
            await env.issue(ALU_ADD, a, b)
            count += 1
    for make_a, make_b in ((big_positive, big_negative), (big_negative, big_positive)):
        for _ in range(per_case):
            a, b = make_a(), make_b()
            assert overflows(to_signed(a) - to_signed(b)), (
                f"Test bug: {a:#x} - {b:#x} should overflow but does not"
            )
            for op in (ALU_SUB, ALU_SLT, ALU_SLTU):
                await env.issue(op, a, b)
                count += 1
    await env.finish()
    assert len(env.completed) == count, (
        f"{count} uops were issued, but {len(env.completed)} results appeared"
    )
    cocotb.log.info(f"All {count} signed-overflow cases matched the reference model")


@cocotb.test()
async def test_operands_come_from_ports(dut):
    """use_imm, imm and pc are ignored: rr_stage has already placed them on the operands."""
    env = await start(dut, "test_operands_come_from_ports")
    a, b = 0x00001000, 0x00000234

    cocotb.log.info("ADD with use_imm = 1 and a different imm must still add in_rs2")
    await env.issue(ALU_ADD, a, b, use_imm=1, imm=0x7777)
    cocotb.log.info("SUB with use_imm = 1 and a different imm must still subtract in_rs2")
    await env.issue(ALU_SUB, a, b, use_imm=1, imm=0x7777)
    cocotb.log.info("AUIPC with a different uop.pc must add in_rs1, not uop.pc")
    await env.issue(ALU_AUIPC, a, b, pc=0x40000000, imm=0x7777)
    cocotb.log.info("LUI with a different imm must return in_rs2")
    await env.issue(ALU_LUI, a, b, use_imm=1, imm=0x7777)
    await env.finish()
    assert len(env.completed) == 4, f"4 uops were issued, but {len(env.completed)} results appeared"
    cocotb.log.info("All 4 results came from in_rs1 and in_rs2 alone")


@cocotb.test()
async def test_rd_en_controls_wakeup(dut):
    """A uop with rd_en = 0 still completes on wb but must not wake up its tag."""
    env = await start(dut, "test_rd_en_controls_wakeup")
    await env.issue(ALU_ADD, 1, 2, rd_en=1, prd=0x15, rob_id=1)
    await env.issue(ALU_ADD, 3, 4, rd_en=0, prd=0x2A, rob_id=2)
    await env.issue(ALU_ADD, 5, 6, rd_en=1, prd=0x3F, rob_id=3)
    await env.finish()
    assert [done.rob_id for done in env.completed] == [1, 2, 3], (
        f"Rob ids 1, 2, 3 should complete in order, but got "
        f"{[done.rob_id for done in env.completed]}"
    )
    assert env.wakeups == 2, f"2 uops had rd_en = 1, but {env.wakeups} wakeups were seen"
    cocotb.log.info("All 3 uops completed; only the 2 with rd_en = 1 broadcast a wakeup")


@cocotb.test()
async def test_flush_drops_incoming_uop(dut):
    """A uop issued alongside flush never appears; the ones around it are unaffected."""
    env = await start(dut, "test_flush_drops_incoming_uop")
    cocotb.log.info("Issuing uop 1 with no flush")
    await env.issue(ALU_ADD, 5, 7, rob_id=1, rd_en=1)
    cocotb.log.info(
        "Issuing uop 2 with flush high; uop 1 is already in the writeback register, "
        "so it still appears this cycle and uop 2 is dropped"
    )
    await env.issue(ALU_SUB, 9, 4, rob_id=2, rd_en=1, flush=True)
    cocotb.log.info("Issuing uop 3 with no flush, right after the flush")
    await env.issue(ALU_XOR, 0xF0, 0xFF, rob_id=3, rd_en=1)
    await env.idle(2)
    got = [done.rob_id for done in env.completed]
    assert got == [1, 3], f"Rob ids 1 and 3 should complete and 2 should not, but got {got}"
    assert env.dropped == 1, f"Exactly 1 uop should have been dropped, but {env.dropped} were"
    cocotb.log.info("Uop 2 was dropped with no result or wakeup; uops 1 and 3 completed")


@cocotb.test()
async def test_flush_held_for_many_cycles(dut):
    """Holding flush high drops every uop issued, then the ALU works again at once."""
    env = await start(dut, "test_flush_held_for_many_cycles", verbose=False)
    await env.idle(2, flush=True)
    for i in range(16):
        await env.issue(ALU_ADD, i, i, flush=True, rd_en=1)
    await env.idle(2, flush=True)
    assert not env.completed, (
        f"Every uop issued during the flush should be dropped, but "
        f"{len(env.completed)} results appeared"
    )
    assert env.dropped == 16, f"16 uops should have been dropped, but {env.dropped} were"
    cocotb.log.info("All 16 uops issued during the flush were dropped")

    await env.issue(ALU_ADD, 40, 2, rob_id=7, rd_en=1)
    await env.finish()
    assert [done.rob_id for done in env.completed] == [7], (
        f"The uop issued after the flush should complete, but got {env.completed}"
    )
    cocotb.log.info("The first uop after the flush completed normally")


@cocotb.test()
async def test_reset_is_asynchronous(dut):
    """Pulling rst_n low mid-cycle clears wb.valid at once, without a clock edge."""
    env = await start(dut, "test_reset_is_asynchronous")
    await env.issue(ALU_ADD, 1, 2, rd_en=1, rob_id=4)
    await env.idle()  # the result is on the bus in this cycle
    assert len(env.completed) == 1, "The uop should be on the writeback bus before the reset"

    cocotb.log.info("wb.valid is 1; pulling rst_n low 2 ns after the clock edge")
    await Timer(2, unit="ns")
    dut.rst_n.value = 0
    await Timer(1, unit="ns")
    await ReadOnly()
    env.expected = None
    env.check_outputs()
    cocotb.log.info("wb.valid and wakeup.valid went low with no clock edge")

    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await env.issue(ALU_SUB, 9, 4, rd_en=1, rob_id=5)
    await env.finish()
    assert [done.rob_id for done in env.completed] == [4, 5], (
        f"The uop issued after reset should complete, but got {env.completed}"
    )
    cocotb.log.info("The first uop after the reset completed normally")


@cocotb.test()
async def test_reset_overrides_valid(dut):
    """A uop presented while rst_n is low produces no result."""
    env = await start(dut, "test_reset_overrides_valid")
    await env.step(uop=env.random_uop(op=ALU_ADD, rd_en=1), rs1=1, rs2=2, reset=True)
    await env.step(uop=env.random_uop(op=ALU_SUB, rd_en=1), rs1=3, rs2=4, reset=True)
    await env.idle(3)
    assert not env.completed, (
        f"Uops issued in reset should not complete, but {len(env.completed)} results appeared"
    )
    cocotb.log.info("Neither uop issued during reset produced a result or wakeup")


@cocotb.test()
async def test_undefined_op_codes(dut):
    """Op codes outside alu_op_e give 0 rather than X.

    Decode should never send these and the spec doesn't define them, so this pins
    the RTL's default arm. Change it along with the RTL if that arm ever changes.
    """
    env = await start(dut, "test_undefined_op_codes", verbose=False)
    for op in range(NUM_OPS, NUM_OP_CODES):
        await env.issue(op, env.rng.getrandbits(XLEN), env.rng.getrandbits(XLEN))
    await env.finish()
    count = NUM_OP_CODES - NUM_OPS
    assert len(env.completed) == count, (
        f"{count} uops were issued, but {len(env.completed)} results appeared"
    )
    cocotb.log.info(f"All {count} undefined op codes returned 0")


@cocotb.test()
async def test_random_traffic(dut):
    """20000 cycles of random ops mixed with idle cycles and flushes."""
    env = await start(dut, "test_random_traffic", verbose=False)
    for _ in range(4):
        for _ in range(5000):
            if env.chance(75):
                a, b = env.random_operands()
                await env.issue(env.rng.randrange(NUM_OPS), a, b, flush=env.chance(5))
            else:
                await env.idle(flush=env.chance(5))
        cocotb.log.info(
            f"After {env.cycle} cycles: {len(env.completed)} results matched the model, "
            f"{env.dropped} uops dropped by flush, {env.wakeups} wakeups"
        )
    await env.finish()

    missing = [OP_NAMES[op] for op in range(NUM_OPS) if not env.op_counts[op]]
    assert not missing, f"The random run should cover every op, but never completed {missing}"
    assert env.dropped > 0, "The random run should cover flush, but no uop was dropped"
    assert 0 < env.wakeups < len(env.completed), (
        f"The random run should mix rd_en values, but saw {env.wakeups} wakeups "
        f"for {len(env.completed)} results"
    )
    cocotb.log.info(
        f"Random traffic passed: {len(env.completed)} results, {env.dropped} drops, "
        f"{env.wakeups} wakeups"
    )
