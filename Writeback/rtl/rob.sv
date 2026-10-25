// Reorder buffer (Spec 10, Section 3.1). A 32-entry circular buffer that
// allocates in program order at dispatch, completes out of order from the
// writeback buses, and retires up to 4 instructions per cycle from its head.
// Decides every trap and flush.
module rob
  import andromeda_pkg::*;
(
  input  logic                              clk,
  input  logic                              rst_n,

  input  logic                              flush,          // be_flush (the ROB empties itself after raising it)

  // Allocation from dispatch
  input  logic [DISPATCH_W-1:0]             alloc_valid,
  input  uop_t [DISPATCH_W-1:0]             alloc_uop,
  output rob_id_t [DISPATCH_W-1:0]          alloc_id,       // Tail + 0..3
  output logic [$clog2(ROB_DEPTH+1)-1:0]    free_cnt,       // Free entries
  output rob_id_t                           head_id,        // Oldest entry, for serializing uops and age checks

  input  wb_t [NUM_PORTS-1:0]               wb,             // Completion, exceptions, branch outcomes, fflags

  // Retirement to rename (rrat, free lists)
  output logic [COMMIT_W-1:0]               cm_valid,
  output logic [COMMIT_W-1:0]               cm_rd_en,
  output logic [COMMIT_W-1:0]               cm_rd_fp,
  output logic [COMMIT_W-1:0][AREG_W-1:0]   cm_rd_arch,
  output ptag_t [COMMIT_W-1:0]              cm_prd,
  output ptag_t [COMMIT_W-1:0]              cm_old_prd,

  // Retirement to lsu
  output logic [$clog2(COMMIT_W+1)-1:0]     cm_load_cnt,    // Retired loads
  output logic [$clog2(COMMIT_W+1)-1:0]     cm_store_cnt,   // Retired stores

  // To csr_unit
  output logic [FFLAGS_W-1:0]               fflags_accrue,  // OR of fflags of retired FP instructions
  output logic [$clog2(COMMIT_W+1)-1:0]     instret_inc,    // Instructions retired this cycle
  output logic                              trap_valid,     // Exception or interrupt being taken
  output logic [EXC_CAUSE_W-1:0]            trap_cause,
  output logic [XLEN-1:0]                   trap_pc,
  output logic [XLEN-1:0]                   trap_tval,
  output logic                              trap_is_irq,
  output logic                              mret_valid,     // MRET retired

  // From csr_unit
  input  logic                              irq_pending,    // Enabled interrupt pending
  input  logic [XLEN-1:0]                   mtvec,          // Trap restart address
  input  logic [XLEN-1:0]                   mepc,           // MRET restart address

  // To pipe_ctrl
  output logic                              flush_valid,
  output logic [XLEN-1:0]                   flush_pc,

  // Commit trace, one lane per retiring instruction; lane 0 is in the low bits.
  // Fields follow andromeda_top (file 01 lists them but isn't in the repo).
  output logic [COMMIT_W-1:0]               trace_valid,    // One bit per retiring instruction this cycle
  output logic [COMMIT_W-1:0][XLEN-1:0]     trace_pc,       // PC of each retiring instruction
  output logic [COMMIT_W-1:0][ILEN-1:0]     trace_instr,    // Encoding of each retiring instruction
  output logic [COMMIT_W-1:0]               trace_rd_we,    // Architectural register write of each
  output logic [COMMIT_W-1:0]               trace_rd_fp,    //   retiring instruction: write enable,
  output logic [COMMIT_W-1:0][AREG_W-1:0]   trace_rd_addr,  //   FP file, register number
  output logic [COMMIT_W-1:0][XLEN-1:0]     trace_rd_data,  //   and value written
  output logic                              trace_trap      // An exception or interrupt was taken this cycle
);

  // --storage--

  // circular buffer
  rob_id_t head_q, tail_q; // note {wrap, 0-31}
  logic [ROB_DEPTH-1:0] valid_instructions_q;
  logic [ROB_DEPTH-1:0] completed_instructions_q;

  // --allocate--

  // keep track of wrap around
  logic [ROB_IDX_W-1:0] alloc_idx [DISPATCH_W]; // {5 bits by 32 lines}
  logic [$clog2(DISPATCH_W+1)-1:0] alloc_cnt;

  // Combination logic
  always_comb begin

    alloc_cnt = '0;

    // tell dispatch which ROB slots to populate
    for (int i = 0; i < DISPATCH_W; i++) begin
      alloc_id[i] = tail_q + ROB_ID_W'(i); // which ROB slots to populate
      alloc_idx[i] = ROB_IDX_W'(tail_q + ROB_ID_W'(i)); // casts to lower 5 bits
      alloc_cnt += alloc_valid[i];
    end

    head_id = head_q;
    free_cnt = ROB_ID_W'(ROB_DEPTH) - (tail_q - head_q);
  end

// keep track of valid instructions and move tail
  always_ff @(posedge clk or negedge rst_n) begin
    // set everything to zero upon reset
    if (!rst_n) begin

      head_q <= '0;
      tail_q <= '0;
      valid_instructions_q <= '0;
      completed_instructions_q <= '0;

    end

    else begin

      // find valid instructions and move tail
      for (int i = 0; i < DISPATCH_W; i++) begin 
        if (alloc_valid[i]) begin
          valid_instructions_q[alloc_idx[i]] <= 1;
          completed_instructions_q[alloc_idx[i]] <= 0;
        end
      end

      tail_q <= tail_q + ROB_ID_W'(alloc_cnt);

    end

  end

  // --retire--




endmodule
