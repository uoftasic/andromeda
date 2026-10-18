// Integer ALU, port P0 (Spec 07, Section 3.1). Combinational ALU between
// rr_stage and a registered writeback bus. alu_bru instantiates it for its
// arithmetic so both ports behave identically.
module alu
  import andromeda_pkg::*;
(
  input  logic             clk,
  input  logic             rst_n,

  input  logic             flush,      // be_flush

  // Issue from rr_stage
  input  logic             in_valid,
  input  uop_t             in_uop,
  input  logic [XLEN-1:0]  in_rs1,     // Operand 1
  input  logic [XLEN-1:0]  in_rs2,     // Operand 2, already replaced by imm when use_imm
  output logic             in_ready,   // Always 1

  output wb_t              wb,         // Result next cycle
  output wakeup_t          wakeup      // Destination tag, same cycle as wb in v1
);

endmodule
