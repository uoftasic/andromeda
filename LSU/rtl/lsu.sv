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

    logic [OP_W-1:0]     in_op;
    logic [1:0]          in_size;
    logic                in_signed;
    logic                in_rd_en;
    logic [XLEN-1:0]     in_imm;
    logic [ROB_IDX_W:0]  in_rob_id;
    logic [PREG_IDX_W:0] in_prd;
    logic [LQ_ID_W-1:0]  in_lq_id;
    logic [SQ_ID_W-1:0]  in_sq_id;

    assign in_op = in_uop.op;
    assign in_size = in_uop.mem_size;
    assign in_signed = in_uop.mem_signed;
    assign in_imm = in_uop.imm;
    assign in_rd_en = in_uop.rd_en;
    assign in_rob_id = in_uop.rob_id;
    assign in_prd = in_uop.prd;
    assign in_lq_id = in_uop.lq_id;
    assign in_sq_id = in_uop.sq_id;

    //Replace below with corresponding typedefs
    logic [LQ_ID_W-1:0] lq_head_q; 
    logic [LQ_ID_W-1:0] lq_tail_q;
    logic [SQ_ID_W-1:0] sq_head_q;
    logic [SQ_ID_W-1:0] sq_cmt_q;
    logic [SQ_ID_W-1:0] sq_tail_q;
    

    logic [LQ_ID_W-1:0] num_alloc_ld;
    logic [SQ_ID_W-1:0] num_alloc_st;

    // Calculate number of allocated loads/stores
    always_comb begin
      // Keep as is (avoiding loops for now)
      num_alloc_ld = lq_alloc_valid[0] + lq_alloc_valid[1] + lq_alloc_valid[2] + lq_alloc_valid[3];
      num_alloc_st = sq_alloc_valid[0] + sq_alloc_valid[1] + sq_alloc_valid[2] + sq_alloc_valid[3];
    end

    //typedef
    logic [LQ_ID_W-1:0] lq_head_d; 
    logic [LQ_ID_W-1:0] lq_tail_d;
    logic [SQ_ID_W-1:0] sq_head_d;
    logic [SQ_ID_W-1:0] sq_cmt_d;
    logic [SQ_ID_W-1:0] sq_tail_d;

    always_comb begin
      lq_head_d = lq_head_q + cm_load_cnt;
      lq_tail_d = lq_tail_q + num_alloc_ld;
      sq_cmt_d = sq_cmt_q + cm_store_cnt;
      sq_head_d = sq_cmt_d;
      sq_tail_d = sq_tail_q + num_alloc_st; 
    end

    always_ff @(posedge clk or negedge rst_n) begin
      if (!rst_n) begin
        lq_head_q <= 'b0;
        lq_tail_q <= 'b0;
        sq_head_q <= 'b0;
        sq_cmt_q  <= 'b0;
        sq_tail_q <= 'b0;
      end else begin
        lq_head_q <= lq_head_d;
        lq_tail_q <= lq_tail_d;
        sq_head_q <= sq_head_d;
        sq_cmt_q  <= sq_cmt_d;
        sq_tail_q <= sq_tail_d;
      end
    end


    assign lq_alloc_id = {lq_tail_q + lq_alloc_valid[0] + lq_alloc_valid[1] + lq_alloc_valid[2],
                          lq_tail_q + lq_alloc_valid[0] + lq_alloc_valid[1],
                          lq_tail_q + lq_alloc_valid[0],
                          lq_tail_q};
    assign sq_alloc_id = {sq_tail_q + sq_alloc_valid[0] + sq_alloc_valid[1] + sq_alloc_valid[2],
                          sq_tail_q + sq_alloc_valid[0] + sq_alloc_valid[1],
                          sq_tail_q + sq_alloc_valid[0],
                          sq_tail_q};

    assign in_ready       = 1'b1;
    assign sq_tail        = '0;
    assign lq_free_cnt    = '0;  
    assign sq_free_cnt    ='0;         
    assign sq_empty       = 1'b1;            
    assign wb             = '0;
    assign wakeup         = '0;              
    assign dmem_req_valid = 1'b0;
    assign dmem_req_we    = 1'b0;   
    assign dmem_req_addr  = '0; 
    assign dmem_req_wdata = '0;
    assign dmem_req_wstrb = '0;
    assign dmem_req_id    = '0;

endmodule
