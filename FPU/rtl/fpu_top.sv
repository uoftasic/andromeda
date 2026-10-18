// Floating-point unit, port P2 (Spec 08, Section 3). Executes every RV32F
// instruction except FLW and FSW. Sub-units (fma, fdivsqrt, fcvt, fmisc)
// finish out of order into a 4-entry result buffer that drains one result
// per cycle onto the writeback bus.
module fpu_top
  import andromeda_pkg::*;
(
  input  logic             clk,
  input  logic             rst_n,

  input  logic             flush,      // be_flush: drops all in-flight operations, including divides

  // Issue from rr_stage
  input  logic             in_valid,
  input  uop_t             in_uop,     // op, rm, prd, rob_id
  input  logic [XLEN-1:0]  in_rs1,     // From the integer file for int-to-FP ops
  input  logic [XLEN-1:0]  in_rs2,
  input  logic [XLEN-1:0]  in_rs3,     // FMA forms only
  output logic             in_ready,   // Can accept this uop (divider and result buffer checked)

  input  logic [RM_W-1:0]  frm,        // Dynamic rounding mode from csr_unit

  output wb_t              wb,         // Result, destination tag and fflags
  output wakeup_t          wakeup      // Destination tag
);

endmodule
