// Physical register free list, one per register file (Spec 04, Section 3.3).
// A circular FIFO of free register indices with an allocation head, a
// committed head and a tail. Flush rewinds the allocation head to the
// committed head.
module free_list
  import andromeda_pkg::*;
(
  input  logic                                  clk,
  input  logic                                  rst_n,

  // Allocation at rename
  input  logic [DISPATCH_W-1:0]                 alloc_req,          // Slots needing a register in this file
  output logic [DISPATCH_W-1:0][PREG_IDX_W-1:0] alloc_ptag,         // Allocated indices
  output logic                                  alloc_ok,           // At least as many free registers as requested

  // Release at commit
  input  logic [COMMIT_W-1:0]                   free_valid,         // Old registers released at commit
  input  logic [COMMIT_W-1:0][PREG_IDX_W-1:0]   free_ptag,
  input  logic [$clog2(COMMIT_W+1)-1:0]         commit_alloc_cnt,   // Allocations that committed this cycle;
                                                                    //   advances the committed head

  input  logic                                  flush               // Rewind allocation head to committed head
);

endmodule
