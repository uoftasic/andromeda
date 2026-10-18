// Machine-mode CSRs and trap handling (Spec 10, Section 3.2). Holds mstatus,
// misa, mie, mip, mtvec, mscratch, mepc, mcause, mtval, mhartid, mcycle(h),
// minstret(h), fflags, frm and fcsr. CSR instructions reach it from alu_bru
// only at the ROB head, so they always see fully committed state.
module csr_unit
  import andromeda_pkg::*;
(
  input  logic                           clk,
  input  logic                           rst_n,

  // CSR access from alu_bru
  input  logic                           req_valid,
  input  logic [CSR_OP_W-1:0]            req_op,         // CSRRW/S/C and immediate forms
  input  logic [CSR_ADDR_W-1:0]          req_addr,
  input  logic [XLEN-1:0]                req_wdata,
  output logic                           resp_valid,
  output logic [XLEN-1:0]                resp_rdata,     // Old CSR value
  output logic                           resp_illegal,   // Illegal CSR or access

  // Trap from the ROB: writes mepc, mcause and mtval and updates mstatus
  input  logic                           trap_valid,
  input  logic [EXC_CAUSE_W-1:0]         trap_cause,
  input  logic [XLEN-1:0]                trap_pc,
  input  logic [XLEN-1:0]                trap_tval,
  input  logic                           trap_is_irq,
  input  logic                           mret_valid,     // Restores mstatus.MIE from MPIE

  // Commit updates from the ROB
  input  logic [FFLAGS_W-1:0]            fflags_accrue,  // ORed into fflags
  input  logic [$clog2(COMMIT_W+1)-1:0]  instret_inc,    // Added to minstret

  // Interrupts
  input  logic                           irq_timer,      // Set mip bits
  input  logic                           irq_external,
  output logic                           irq_pending,    // Enabled interrupt pending, to the ROB

  output logic [XLEN-1:0]                mtvec,          // To the ROB
  output logic [XLEN-1:0]                mepc,
  output logic [RM_W-1:0]                frm             // To fpu_top
);

endmodule
