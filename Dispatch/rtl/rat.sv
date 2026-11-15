// Speculative register alias table (Spec 04, Section 3.2). Maps every
// architectural register in both files to its newest physical register.
// Updated at every rename; restored from the rrat on a flush.
module rat
  import andromeda_pkg::*;
(
  input  logic                                    clk,
  input  logic                                    rst_n,

  // Lookups: 12 sources and 4 old destinations per cycle
  input  logic [4*DISPATCH_W-1:0]                 rd_fp,
  input  logic [4*DISPATCH_W-1:0][AREG_W-1:0]     rd_arch,
  output ptag_t [4*DISPATCH_W-1:0]                rd_ptag,        // Current mapping

  // New mappings from rename; slot 3 has priority on conflicts
  input  logic [DISPATCH_W-1:0]                   we,
  input  logic [DISPATCH_W-1:0]                   wr_fp,
  input  logic [DISPATCH_W-1:0][AREG_W-1:0]       wr_arch,
  input  ptag_t [DISPATCH_W-1:0]                  wr_ptag,

  input  logic                                    restore,        // Copy all 64 entries from the rrat (driven by flush)
  input  ptag_t [2*NUM_AREG-1:0]                  committed_map   // rrat contents, indexed by {fp, arch}
);

endmodule
