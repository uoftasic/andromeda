// Load/store unit, port P3 (Spec 09, Section 3). Executes every load and
// store, keeps them ordered through an 8-entry load queue and an 8-entry
// store queue, and owns the data memory port. Stores write memory only after
// they commit.
module lsu
  import andromeda_pkg::*;
(
  input  logic                              clk,
  input  logic                              rst_n,

  input  logic                              flush,            // be_flush

  // Issue from rr_stage
  input  logic                              in_valid,
  input  uop_t                              in_uop,           // op, size, signed, imm, rob_id, prd; lq_id and the
                                                              //   sq_id marker for loads, sq_id for stores
  input  logic [XLEN-1:0]                   in_base,          // Base address register
  input  logic [XLEN-1:0]                   in_sdata,         // Store data
  output logic                              in_ready,         // Can accept a uop this cycle

  // Queue allocation from dispatch
  input  logic [DISPATCH_W-1:0]             lq_alloc_valid,
  input  logic [DISPATCH_W-1:0]             sq_alloc_valid,
  output lq_id_t [DISPATCH_W-1:0]           lq_alloc_id,      // Allocated indices with wrap bit (tail + n)
  output sq_id_t [DISPATCH_W-1:0]           sq_alloc_id,
  output sq_id_t                            sq_tail,          // Current SQ tail, for load markers
  output logic [$clog2(LQ_DEPTH+1)-1:0]     lq_free_cnt,      // Free entries
  output logic [$clog2(SQ_DEPTH+1)-1:0]     sq_free_cnt,

  // Commit from the ROB
  input  logic [$clog2(COMMIT_W+1)-1:0]     cm_load_cnt,      // Loads retired this cycle: frees LQ entries
  input  logic [$clog2(COMMIT_W+1)-1:0]     cm_store_cnt,     // Stores retired this cycle: marks SQ entries committed
  output logic                              sq_empty,         // No committed stores left to drain (used by FENCE)

  output wb_t                               wb,               // Load result or store completion, including exceptions
  output wakeup_t                           wakeup,           // Load destination tag

  // Data memory request channel
  output logic                              dmem_req_valid,
  input  logic                              dmem_req_ready,
  output logic                              dmem_req_we,      // 1 = store, 0 = load
  output logic [XLEN-1:0]                   dmem_req_addr,    // Byte address
  output logic [XLEN-1:0]                   dmem_req_wdata,   // Store data, aligned to the word
  output logic [XLEN/8-1:0]                 dmem_req_wstrb,   // Byte enables for stores
  output logic [LQ_IDX_W-1:0]               dmem_req_id,      // LQ index without its wrap bit

  // Data memory response channel (loads only; no ready: the LSU always accepts)
  input  logic                              dmem_resp_valid,
  input  logic [XLEN-1:0]                   dmem_resp_rdata,  // Whole aligned word
  input  logic [LQ_IDX_W-1:0]               dmem_resp_id,     // Matches dmem_req_id
  input  logic                              dmem_resp_err     // Access fault
);

endmodule
