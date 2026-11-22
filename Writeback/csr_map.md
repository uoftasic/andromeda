# CSR Address Map

`csr_unit` implements the machine-mode CSRs listed in Spec 10 §3.2
(`Andromeda_10_rob_csr.pdf`), at the standard addresses from the RISC-V
privileged spec. The CSR address is 12 bits (`CSR_ADDR_W`), taken from
instr[31:20].

Address bits [11:10] = `11` mark a CSR read-only; writing one raises an
illegal-instruction exception. Bits [9:8] give the lowest privilege level that
can access the CSR (`00` = user, `11` = machine).

## Implemented CSRs

| Address | Name      | Access    | Description                                          |
| ------- | --------- | --------- | ---------------------------------------------------- |
| 0x001   | fflags    | RW        | FP accrued exception flags {NV, DZ, OF, UF, NX}      |
| 0x002   | frm       | RW        | FP dynamic rounding mode, sent to `fpu_top`          |
| 0x003   | fcsr      | RW        | View of {frm, fflags}: frm = [7:5], fflags = [4:0]   |
| 0x300   | mstatus   | RW        | Machine status (MIE, MPIE, MPP, FS)                  |
| 0x301   | misa      | RW (WARL) | ISA: MXL = 1 (RV32), I and F bits set                |
| 0x304   | mie       | RW        | Interrupt enable (MTIE bit 7, MEIE bit 11)           |
| 0x305   | mtvec     | RW        | Trap vector base and mode                            |
| 0x340   | mscratch  | RW        | Scratch register for trap handlers                   |
| 0x341   | mepc      | RW        | Exception PC                                         |
| 0x342   | mcause    | RW        | Trap cause; bit 31 = interrupt                       |
| 0x343   | mtval     | RW        | Trap value (faulting address or instruction)         |
| 0x344   | mip       | RW        | Interrupt pending (MTIP bit 7, MEIP bit 11)          |
| 0xB00   | mcycle    | RW        | Cycle counter, low 32 bits                           |
| 0xB02   | minstret  | RW        | Retired instruction counter, low 32 bits             |
| 0xB80   | mcycleh   | RW        | Cycle counter, high 32 bits                          |
| 0xB82   | minstreth | RW        | Retired instruction counter, high 32 bits            |
| 0xF14   | mhartid   | RO        | Hart ID, reads 0                                     |

## Notes from the spec

- mstatus.MPP can be hardwired to `11` (machine mode), since there are no
  other privilege modes.
- mstatus.FS exists because the F extension is implemented; follow the
  privileged spec's rules for it.
- fcsr is a view of frm and fflags together, not separate storage.
- mcycle increments every cycle; minstret increments by `instret_inc`.
- On a trap, `csr_unit` writes mepc, mcause and mtval, copies MIE to MPIE and
  clears MIE. `mret` restores MIE from MPIE.

## Open decisions

- **Unlisted standard CSRs.** The spec doesn't list these, so an access to them
  is currently illegal. Compliance tests often read them, so decide whether
  they read as zero or trap:
  - mvendorid (0xF11), marchid (0xF12), mimpid (0xF13)
  - mstatush (0x310)
  - cycle / cycleh (0xC00 / 0xC80), instret / instreth (0xC02 / 0xC82)
- **`req_op` encoding.** `req_op` is `CSR_OP_W` = 3 bits, but `csr_op_e` is
  6 bits wide. How the six CSRRW/S/C and immediate forms map onto `req_op`
  is not yet defined.

## Package constants

`andromeda_pkg.sv` defines each address as `CSR_<NAME>` (e.g. `CSR_MSTATUS`),
plus the mstatus field positions (`MSTATUS_*`), the interrupt bit positions
and cause codes (`IRQ_M_TIMER`, `IRQ_M_EXTERNAL`) and `MISA_VALUE`.
