// Dispatch stage (Spec 05, Section 3.1). The last in-order step: gives each
// renamed uop a ROB entry, an issue queue entry by fu_type, and an LQ or SQ
// entry for loads and stores. The whole group waits unless every structure
// it needs has room.
module dispatch
  import andromeda_pkg::*;
(
  input  logic                              clk,
  input  logic                              rst_n,

  input  logic                              flush,              // be_flush

  // Group from rename
  input  logic [DISPATCH_W-1:0]             in_valid,
  input  uop_t [DISPATCH_W-1:0]             in_uop,
  output logic                              in_ready,

  // ROB allocation
  output logic [DISPATCH_W-1:0]             rob_alloc_valid,    // New ROB entries
  output uop_t [DISPATCH_W-1:0]             rob_alloc_uop,
  input  rob_id_t [DISPATCH_W-1:0]          rob_alloc_id,       // ROB ids for the 4 slots (tail + i)
  input  logic [$clog2(ROB_DEPTH+1)-1:0]    rob_free_cnt,       // Free ROB entries

  // Issue queue enqueue, per slot to each queue; the uops are shared
  output logic [DISPATCH_W-1:0]             iq_int_enq_valid,   // ALU, BRU and CSR uops
  output logic [DISPATCH_W-1:0]             iq_fp_enq_valid,    // FPU uops
  output logic [DISPATCH_W-1:0]             iq_mem_enq_valid,   // LSU uops
  output uop_t [DISPATCH_W-1:0]             iq_enq_uop,         // Same uops, rob_id and lq/sq ids filled in
  input  logic [$clog2(IQ_INT+1)-1:0]       iq_int_free_cnt,    // Free entries per queue
  input  logic [$clog2(IQ_FP+1)-1:0]        iq_fp_free_cnt,
  input  logic [$clog2(IQ_MEM+1)-1:0]       iq_mem_free_cnt,

  // Load and store queue allocation
  output logic [DISPATCH_W-1:0]             lq_alloc_valid,
  output logic [DISPATCH_W-1:0]             sq_alloc_valid,
  input  lq_id_t [DISPATCH_W-1:0]           lq_alloc_id,        // Allocated indices with wrap bit
  input  sq_id_t [DISPATCH_W-1:0]           sq_alloc_id,
  input  sq_id_t                            sq_tail,            // A load's sq_id = sq_tail + stores ahead of it
                                                                //   in the same group (its older-store marker)
  input  logic [$clog2(LQ_DEPTH+1)-1:0]     lq_free_cnt,        // Free load and store queue entries
  input  logic [$clog2(SQ_DEPTH+1)-1:0]     sq_free_cnt
);

endmodule
