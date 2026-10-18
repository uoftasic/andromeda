// Rename stage (Spec 04, Section 3.1). Renames a group of 4 uops per cycle
// through the rat, free lists and busy tables, resolving dependences inside
// the group, and updates the rrat and free lists at commit.
module rename_stage
  import andromeda_pkg::*;
(
  input  logic                              clk,
  input  logic                              rst_n,

  input  logic                              flush,        // be_flush

  // Group from decode
  input  logic [DISPATCH_W-1:0]             in_valid,
  input  uop_t [DISPATCH_W-1:0]             in_uop,
  output logic                              in_ready,     // Group accepted (enough free registers in both files)

  // Renamed group to dispatch
  output logic [DISPATCH_W-1:0]             out_valid,
  output uop_t [DISPATCH_W-1:0]             out_uop,      // prs1/2/3, prd, old_prd and ready bits filled
  input  logic                              out_ready,    // Dispatch took the group

  input  wakeup_t [NUM_PORTS-1:0]           wakeup,       // Clears busy bits; also checked against sources renamed this cycle

  // Commit from the ROB: updates the rrat; old_prd returns to the free list
  input  logic [COMMIT_W-1:0]               cm_valid,     // Retiring uops this cycle
  input  logic [COMMIT_W-1:0]               cm_rd_en,
  input  logic [COMMIT_W-1:0]               cm_rd_fp,
  input  logic [COMMIT_W-1:0][AREG_W-1:0]   cm_rd_arch,
  input  ptag_t [COMMIT_W-1:0]              cm_prd,
  input  ptag_t [COMMIT_W-1:0]              cm_old_prd
);

endmodule
