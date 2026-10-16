# Andromeda

SystemVerilog RTL for the Andromeda CPU core. Every unit is verified on its own
with [cocotb](https://www.cocotb.org/) 2.0 and Icarus Verilog 12, then the whole
core is verified from `top/`.

## Directory structure

```
andromeda/
├── ALU/              Integer ALU
│   ├── rtl/          Synthesizable SystemVerilog for this unit
│   └── sim/          cocotb testbench: Makefile + test_alu.py
├── FPU/              Floating-point unit       (rtl/, sim/)
├── LSU/              Load/store unit           (rtl/, sim/)
├── Fetch/            Instruction fetch         (rtl/, sim/)
├── Dispatch/         Dispatch                  (rtl/, sim/)
├── Writeback/        Writeback                 (rtl/, sim/)
├── top/              Core top level: instantiates and connects the units
│   ├── rtl/
│   └── sim/          Full-core testbench
├── IP/               Third-party or shared IP (no rtl/sim split)
│   ├── ram.v         Sim-only models of mixed-width dual-port RAM IP
│   └── sync_fifo.v   Byte-addressable synchronous FIFO
├── .gitignore
└── README.md
```

A unit's `sim/Makefile` compiles only that unit's `rtl/`. `top/sim/Makefile`
compiles every unit's `rtl/` plus `IP/` (top level and one directory deep) into
one design.

| Directory    | Top module  | Test module                      |
|--------------|-------------|----------------------------------|
| `ALU/`       | `alu`       | `ALU/sim/test_alu.py`            |
| `FPU/`       | `fpu`       | `FPU/sim/test_fpu.py`            |
| `LSU/`       | `lsu`       | `LSU/sim/test_lsu.py`            |
| `Fetch/`     | `fetch`     | `Fetch/sim/test_fetch.py`        |
| `Dispatch/`  | `dispatch`  | `Dispatch/sim/test_dispatch.py`  |
| `Writeback/` | `writeback` | `Writeback/sim/test_writeback.py`|
| `top/`       | `top`       | `top/sim/test_top.py`            |

## Running a testbench

```sh
cd ALU/sim
make                              # run all tests; exits non-zero if any test fails
make WAVES=1                      # also write sim_build/alu.fst (open with gtkwave)
make COCOTB_TEST_FILTER=test_add  # run only tests whose name matches the regex
make clean                        # delete sim_build/ and results.xml
```

The Makefiles use the cocotb 2.0 names `COCOTB_TOPLEVEL` and
`COCOTB_TEST_MODULES`. The older `TOPLEVEL` and `MODULE` are deprecated. Files
named `*_pkg.sv` are compiled before all other sources.

## Design rules

### 1. Clock and reset

- Every unit's top module, and every submodule that holds state, has a clock
  port named exactly `clk` and a reset port named exactly `rst_n`. Don't use
  `clock`, `clk_i`, `rst`, `reset` or `rstn`. Purely combinational submodules
  leave both out.
- There is one clock domain. Every flop triggers on `posedge clk`. Don't use
  negedge flops, gated clocks, divided clocks or logic on the clock path.
- `rst_n` is **active-low and asynchronous**: it clears flops right away,
  without waiting for a clock edge. This matches `IP/sync_fifo.v`.

  ```systemverilog
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      count_q <= '0;
    end else begin
      count_q <= count_d;
    end
  end
  ```

- Release `rst_n` in step with `clk`. Testbenches already do this. When
  `rst_n` comes from off-chip, `top` must pass it through a reset synchronizer.

- Reset all control state: valid bits, FSM state, pointers and counters.
  Datapath registers that are only read while a reset valid bit is set may stay
  unreset. Put those in their own `always_ff` block.

### 2. Files and naming

- New code is SystemVerilog (`.sv`). Put one module per file, and name the file
  after the module (`alu_shifter.sv` holds `module alu_shifter`).
- A unit's top module is named after its directory in lowercase (`alu`, `lsu`,
  `fetch`, …, `top`). The sim Makefiles depend on this.
- Every other module in a unit starts with the unit's name: `alu_shifter`,
  `lsu_agu`, `fetch_pc_gen`. `top/sim` compiles all units into one namespace,
  so two units that each define an `adder` would collide.
- Packages end in `_pkg` and live in `<name>_pkg.sv`. The Makefiles rely on that
  suffix to compile packages first.

| Kind                                | Convention                  | Example                   |
|-------------------------------------|-----------------------------|---------------------------|
| Modules, signals, ports             | `lower_snake_case`          | `rd_addr`                 |
| Parameters, localparams             | `UPPER_SNAKE_CASE`          | `XLEN`, `FIFO_DEPTH`      |
| Types                               | `_t` suffix (`_e` for enums)| `uop_t`, `alu_op_e`       |
| Active-low signals                  | `_n` suffix                 | `rst_n`                   |
| Register output / next-state value  | `_q` / `_d` suffix          | `pc_q`, `pc_d`            |
| Instances                           | `u_` prefix                 | `u_alu`                   |
| Generate blocks (always named)      | `gen_` prefix               | `gen_lanes`               |
| Handshake signals                   | `<chan>_valid`, `<chan>_ready`, `<chan>_<field>` | `req_valid`, `req_a` |

### 3. RTL coding style


- `rtl/` holds no simulation-only code: no `initial` blocks, `#` delays or
  `$display`. Testbench behaviour lives in Python.
- Connect instance ports by name (`.clk(clk)`), never by position.
- Don't hard-code architectural widths. Use a parameter or package localparam
  such as `XLEN`.
- Every signal has exactly one driver.
- Don't put `` `timescale `` in `rtl/` files. The cocotb Makefiles set 1ns/1ps
  for any file without one.
- These rules apply to `rtl/`. Files in `IP/` keep their original conventions.
  For example, the RAM models in `IP/ram.v` use a `clock` port to match the
  Intel-generated IP they stand in for. IP must still compile under Icarus 11.

### 4. Interfaces between units and stages

Every channel between units or pipeline stages uses a valid/ready handshake:

- A transfer happens on a rising `clk` edge where `valid && ready` is true.
- Once the sender raises `valid`, it keeps `valid` high and the payload
  unchanged until the transfer happens.
- `valid` must not depend combinationally on `ready`. `ready` may depend on
  `valid`. This rule prevents combinational loops between units.


### 5. Example module

This module follows every rule above:

```systemverilog
// One-stage adder with valid/ready handshakes on both sides.
module alu_add_pipe #(
  parameter int WIDTH = 32
) (
  input  logic             clk,
  input  logic             rst_n,

  // Request channel
  input  logic             req_valid,
  output logic             req_ready,
  input  logic [WIDTH-1:0] req_a,
  input  logic [WIDTH-1:0] req_b,

  // Response channel
  output logic             rsp_valid,
  input  logic             rsp_ready,
  output logic [WIDTH-1:0] rsp_sum
);

  logic             valid_q;
  logic [WIDTH-1:0] sum_q;

  // Accept a new request when the output register is empty or being drained.
  assign req_ready = !valid_q || rsp_ready;
  assign rsp_valid = valid_q;
  assign rsp_sum   = sum_q;

  // Control state: reset.
  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      valid_q <= 1'b0;
    end else if (req_ready) begin
      valid_q <= req_valid;
    end
  end

  // Datapath: not reset; only meaningful while valid_q is high.
  always_ff @(posedge clk) begin
    if (req_valid && req_ready) begin
      sum_q <= req_a + req_b;
    end
  end

endmodule

```

## Verification rules

- Every unit has `sim/test_<unit>.py`, which is the Makefile's default
  `COCOTB_TEST_MODULES`.
- Tests check themselves with `assert` and print clear, understandable output.
  Log each step and each check as a plain sentence with `cocotb.log.info(...)`,
  and give every `assert` a message that says what was expected and what
  actually happened. A test that needs someone to read a waveform doesn't
  count.
- Where you can, check against a Python reference model instead of hand-typed
  expected values.

A test for the example module above. It logs every step, and each `assert`
explains itself if it fails:

```python
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge

WIDTH = 32


async def reset(dut, cycles=5):
    """Hold rst_n low for `cycles` clock cycles, then release it."""
    cocotb.log.info(f"Holding rst_n low for {cycles} cycles")
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, cycles)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    cocotb.log.info("Reset released")


@cocotb.test()
async def test_add(dut):
    """Send a few additions through the adder and check every sum."""
    cocotb.log.info("Starting a 10 ns clock on clk")
    Clock(dut.clk, 10, unit="ns").start()
    dut.req_valid.value = 0
    dut.rsp_ready.value = 1
    await reset(dut)

    # (a, b) pairs to add. The last one overflows 32 bits and wraps to 0.
    cases = [(3, 4), (0, 0), (100, 23), (2**WIDTH - 1, 1)]

    for n, (a, b) in enumerate(cases, start=1):
        expected = (a + b) % 2**WIDTH  # Python reference model

        dut.req_a.value = a
        dut.req_b.value = b
        dut.req_valid.value = 1
        await RisingEdge(dut.clk)  # request is accepted on this edge
        dut.req_valid.value = 0

        await ReadOnly()  # wait for the flops to update before reading outputs
        assert dut.rsp_valid.value == 1, (
            f"Case {n}: rsp_valid should be 1 one cycle after the request "
            f"was accepted, but it is {dut.rsp_valid.value}"
        )
        got = int(dut.rsp_sum.value)
        assert got == expected, (
            f"Case {n}: {a} + {b} should be {expected}, but rsp_sum is {got}"
        )
        cocotb.log.info(f"Case {n}/{len(cases)}: {a} + {b} = {got}  OK")

        await RisingEdge(dut.clk)  # leave the read-only phase before driving again

    cocotb.log.info(f"All {len(cases)} additions matched the reference model")
```

A passing run prints:

```
     0.00ns INFO     test                               Starting a 10 ns clock on clk
     0.00ns INFO     test                               Holding rst_n low for 5 cycles
    50.00ns INFO     test                               Reset released
    60.00ns INFO     test                               Case 1/4: 3 + 4 = 7  OK
    80.00ns INFO     test                               Case 2/4: 0 + 0 = 0  OK
   100.00ns INFO     test                               Case 3/4: 100 + 23 = 123  OK
   120.00ns INFO     test                               Case 4/4: 4294967295 + 1 = 0  OK
   130.00ns INFO     test                               All 4 additions matched the reference model
                                                        ** TESTS=1 PASS=1 FAIL=0 SKIP=0 ...
```

If the adder is broken (here it subtracts instead of adding), the failure says
exactly what went wrong:

```
     0.00ns INFO     test                               Starting a 10 ns clock on clk
     0.00ns INFO     test                               Holding rst_n low for 5 cycles
    50.00ns INFO     test                               Reset released
    60.00ns WARNING  cocotb.Test test_add.test_add      Case 1: 3 + 4 should be 7, but rsp_sum is 4294967295
                                                        AssertionError: Case 1: 3 + 4 should be 7, but rsp_sum is 4294967295
                                                        ** TESTS=1 PASS=0 FAIL=1 SKIP=0 ...
```

Linting is optional for now but recommended. Verilator 4.038 is installed:

```sh
verilator --lint-only -Wall ALU/rtl/*.sv
```

## Git workflow

- `main` only ever holds code whose testbenches pass. Never commit to `main`
  directly.
- Do all work on your own branch, named `<owner>/<topic>`, e.g.
  `jerry/alu-shifter` or `claude/lsu-agu`.
- Before merging into `main`:
  1. `make` passes in the `sim/` directory of every unit you changed. Tests
     don't need to be exhaustive, but every test, new and existing, must pass.
  2. If you changed ports that another unit or `top` uses, run those
     testbenches too.
  3. Merge with `git merge --no-ff <branch>` so the branch shows up in history.
- If a testbench fails, don't merge. Fix it, or leave the work on the branch
  with a note explaining what's broken.
- Don't commit generated files. `.gitignore` already excludes `sim_build/`,
  `results.xml`, waveforms and `__pycache__/`.

## Adding a new unit

1. Create `<Unit>/rtl/` and `<Unit>/sim/`, and copy an existing unit's
   `sim/Makefile`. Change the comment, `COCOTB_TOPLEVEL` and
   `COCOTB_TEST_MODULES`.
2. Add the directory name to `UNITS` in `top/sim/Makefile`.
3. Add the unit to the tables in this README.

