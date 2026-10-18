// Integer ALU plus branch resolution, port P1 (Spec 07, Section 3.2).
// Resolves branches and jumps against the fetch prediction, and forwards CSR
// instructions to csr_unit when they reach the ROB head.
module alu_bru
  import andromeda_pkg::*;
(
  input  logic                   clk,
  input  logic                   rst_n,

  input  logic                   flush,             // be_flush

  // Issue from rr_stage
  input  logic                   in_valid,
  input  uop_t                   in_uop,
  input  logic [XLEN-1:0]        in_rs1,            // Operand 1
  input  logic [XLEN-1:0]        in_rs2,            // Operand 2, already replaced by imm when use_imm
  output logic                   in_ready,          // Low while csr_unit is busy

  // CSR request to csr_unit, only ever at the ROB head
  output logic                   csr_req_valid,
  output logic [CSR_OP_W-1:0]    csr_req_op,        // CSRRW/S/C and immediate forms
  output logic [CSR_ADDR_W-1:0]  csr_req_addr,
  output logic [XLEN-1:0]        csr_req_wdata,

  // CSR response from csr_unit
  input  logic                   csr_resp_valid,
  input  logic [XLEN-1:0]        csr_resp_rdata,    // Old CSR value
  input  logic                   csr_resp_illegal,  // Illegal CSR or access

  output wb_t                    wb,                // Result and branch outcome (br_valid, br_taken, br_target, br_mispredict)
  output wakeup_t                wakeup             // Destination tag
);

endmodule
