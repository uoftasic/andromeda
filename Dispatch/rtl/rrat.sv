// Committed register alias table (Spec 04, Section 3.2). Same mapping as the
// rat, but only as of the last committed instruction. Updated at every
// commit; its full contents restore the rat on a flush.
module rrat
  import andromeda_pkg::*;
(
  input  logic                                    clk,
  input  logic                                    rst_n,

  // Lookups: 12 sources and 4 old destinations per cycle
  input  logic [4*DISPATCH_W-1:0]                 rd_fp,
  input  logic [4*DISPATCH_W-1:0][AREG_W-1:0]     rd_arch,
  output ptag_t [4*DISPATCH_W-1:0]                rd_ptag,        // Current mapping

  // New mappings from commit; slot 3 has priority on conflicts
  input  logic [COMMIT_W-1:0]                     we,
  input  logic [COMMIT_W-1:0]                     wr_fp,
  input  logic [COMMIT_W-1:0][AREG_W-1:0]         wr_arch,
  input  ptag_t [COMMIT_W-1:0]                    wr_ptag,

  output ptag_t [2*NUM_AREG-1:0]                  committed_map   // Full contents for the rat, indexed by {fp, arch}
);

endmodule
