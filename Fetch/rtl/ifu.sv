// Fetch unit (Spec 02, Section 3.1). Fetches one 16-byte aligned block at a
// time and predicts not taken (the RAS isn't used yet), so fetch runs
// sequentially until a redirect. When the fetch PC isn't aligned, the slots
// below it are marked invalid. One request is outstanding at a time, and a
// response the fetch buffer can't take yet waits in tmp_buf. A redirect or
// flush drops everything from the old path right away: a request still
// waiting on imem is withdrawn, tmp_buf is emptied, and the response of a
// request already sent is dropped when it returns.
module ifu
  import andromeda_pkg::*;
(
  input  logic                          clk,
  input  logic                          rst_n,

  input  logic [XLEN-1:0]               boot_addr,        // PC after reset
  input  logic                          flush,            // fe_flush from pipe_ctrl

  // Redirect from branch execution
  input  logic                          redirect_valid,   // Restart fetch at redirect_pc
  input  logic [XLEN-1:0]               redirect_pc,

  // Instruction memory request channel
  output logic                          imem_req_valid,   // Instruction fetch request
  input  logic                          imem_req_ready,   // Memory accepts the request
  output logic [XLEN-1:0]               imem_req_addr,    // 16-byte aligned fetch block address

  // Instruction memory response channel (no ready: the ifu always accepts)
  input  logic                          imem_resp_valid,  // Fetch data returned (in request order)
  input  logic [FETCH_W-1:0][ILEN-1:0]  imem_resp_data,   // 4 instructions, lowest address in bits [31:0]
  input  logic                          imem_resp_err,    // Access fault on this fetch

  // Speculative return address stack update
  output logic                          ras_push,         // Call: push ras_push_addr
  output logic                          ras_pop,          // Return: pop the top entry
  output logic [XLEN-1:0]               ras_push_addr,    // Return address of the call

  // Fetch buffer enqueue channel
  output logic                          fb_enq_valid,     // Block ready for the fetch buffer
  input  logic                          fb_enq_ready,     // Fetch buffer has at least 4 free entries
  output fetch_slot_t [FETCH_W-1:0]     fb_enq_slot       // The 4 slots, with per-slot valid; slot 0 is the lowest address
);
    localparam IF_IDLE = 0,
               IF_SEND = 1;
    localparam OFF_W = $clog2(FETCH_BYTES);   // byte offset bits within a fetch block

    logic               if_state_reg;
    logic [XLEN-1:0]    pc;
    logic [XLEN-1:0]    pc_next;

    // One fetched block, held while the fetch buffer isn't ready
    fetch_slot_t [FETCH_W-1:0] tmp_buf;
    logic               tmp_buf_valid;

    // The outstanding imem request
    logic               inflight;
    logic               drop_resp;
    logic [XLEN-1:0]    resp_pc;

    logic               kill;
    logic               req_fire;
    logic               resp_done;
    logic               can_send;
    fetch_slot_t [FETCH_W-1:0] resp_slot;

    assign kill      = flush || redirect_valid;
    assign req_fire  = imem_req_valid && imem_req_ready;
    assign resp_done = imem_resp_valid && (drop_resp || fb_enq_ready);
    // Only send when the response is sure to have somewhere to go: nothing
    // else outstanding and tmp_buf empty.
    assign can_send  = ~tmp_buf_valid && (~inflight || resp_done);

    always_ff@(posedge clk or negedge rst_n)begin
        if(~rst_n)begin
            pc <= boot_addr;
            if_state_reg <= IF_IDLE;
            inflight <= 0;
            drop_resp <= 0;
            tmp_buf_valid <= 0;
        end
        else begin
            // No request goes out in a redirect cycle, so the new pc is only
            // presented to imem from the next cycle on.
            if(redirect_valid || req_fire)begin
                pc <= pc_next;
            end

            case(if_state_reg)
                IF_IDLE: begin
                    if(fb_enq_ready)begin
                        if_state_reg <= IF_SEND;
                    end
                end

                IF_SEND: begin
                    if(req_fire && ~fb_enq_ready)begin
                        if_state_reg <= IF_IDLE;
                    end
                end

                default: begin
                    if_state_reg <= IF_IDLE;
                end

            endcase

            if(req_fire)begin
                inflight <= 1;
                drop_resp <= 0;
            end
            else begin
                if(imem_resp_valid)begin
                    inflight <= 0;
                end
                if(kill)begin
                    drop_resp <= 1;     // already sent from the old path
                end
            end

            if(kill)begin
                tmp_buf_valid <= 0;
            end
            else if(tmp_buf_valid)begin
                if(fb_enq_ready)begin
                    tmp_buf_valid <= 0;
                end
            end
            else if(imem_resp_valid && ~drop_resp && ~fb_enq_ready)begin
                tmp_buf_valid <= 1;
            end
        end
    end

    // Datapath: not reset; only read while inflight / tmp_buf_valid is set.
    always_ff@(posedge clk)begin
        if(req_fire)begin
            resp_pc <= pc;
        end
        if(~tmp_buf_valid && imem_resp_valid && ~fb_enq_ready)begin
            tmp_buf <= resp_slot;
        end
    end

    always_comb begin
        if(redirect_valid)begin
            pc_next = redirect_pc;
        end
        else begin
            pc_next = {pc[XLEN-1:OFF_W], OFF_W'(0)} + FETCH_BYTES;   // next sequential block
        end
    end

    always_comb begin
        imem_req_addr = boot_addr;
        imem_req_valid = 0;
        case(if_state_reg)
            IF_SEND: begin
                imem_req_addr = {pc[XLEN-1:OFF_W], OFF_W'(0)};
                imem_req_valid = can_send && ~kill;     // a redirect or flush withdraws the request
            end

            default: begin
                imem_req_addr = boot_addr;
                imem_req_valid = 0;
            end
        endcase
    end

    // Slots below the fetch PC are invalid. Every slot is predicted not taken.
    for(genvar i = 0; i < FETCH_W; i++)begin : gen_resp_slot
        assign resp_slot[i].valid       = i >= int'(resp_pc[OFF_W-1:2]);
        assign resp_slot[i].pc          = {resp_pc[XLEN-1:OFF_W], OFF_W'(i * 4)};
        assign resp_slot[i].instr       = imem_resp_data[i];
        assign resp_slot[i].pred_taken  = 0;
        assign resp_slot[i].pred_target = '0;     // unused while pred_taken = 0
        assign resp_slot[i].fetch_err   = imem_resp_err;
    end

    // tmp_buf goes first: it holds the older block.
    assign fb_enq_valid = ~kill && (tmp_buf_valid || (imem_resp_valid && ~drop_resp));
    assign fb_enq_slot  = tmp_buf_valid ? tmp_buf : resp_slot;

    // Return address stack: not used yet.
    assign ras_push      = 0;
    assign ras_pop       = 0;
    assign ras_push_addr = '0;

`ifdef ANDROMEDA_DEBUG
    // Simulation-only trace (make DEBUG=1 in Fetch/sim). Each rising edge prints
    // what happened in the cycle that just ended. dbg_cycle numbers cycles from
    // reset release, the same way the cocotb tests do. Strings that change with
    // a condition get their own $display: Icarus pads a %s ternary with spaces.
    int unsigned        dbg_cycle;
    logic [XLEN-1:0]    dbg_resp_blk;

    assign dbg_resp_blk = {resp_pc[XLEN-1:OFF_W], OFF_W'(0)};

    always@(posedge clk or negedge rst_n)begin
        if(~rst_n)begin
            dbg_cycle <= 0;
        end
        else begin
            dbg_cycle <= dbg_cycle + 1;

            if(redirect_valid)begin
                $display("[ifu] Cycle %0d: redirect to 0x%08h", dbg_cycle, redirect_pc);
            end
            if(flush)begin
                $display("[ifu] Cycle %0d: flush", dbg_cycle);
            end
            if(req_fire)begin
                $display("[ifu] Cycle %0d: imem accepted request for block 0x%08h", dbg_cycle,
                         imem_req_addr);
            end
            if(imem_resp_valid)begin
                if(drop_resp || kill)begin
                    $display("[ifu] Cycle %0d: imem returned block 0x%08h err %0d, dropped (old path)",
                             dbg_cycle, dbg_resp_blk, imem_resp_err);
                end
                else if(fb_enq_ready)begin
                    $display("[ifu] Cycle %0d: imem returned block 0x%08h err %0d, sent to fetch buffer",
                             dbg_cycle, dbg_resp_blk, imem_resp_err);
                end
                else begin
                    $display("[ifu] Cycle %0d: imem returned block 0x%08h err %0d, held in tmp_buf",
                             dbg_cycle, dbg_resp_blk, imem_resp_err);
                end
            end
            if(fb_enq_valid && fb_enq_ready)begin
                if(tmp_buf_valid)begin
                    $display("[ifu] Cycle %0d: tmp_buf sent to fetch buffer", dbg_cycle);
                end
                for(int i = 0; i < FETCH_W; i++)begin
                    if(fb_enq_slot[i].valid)begin
                        $display("[ifu] Cycle %0d: enqueued slot %0d pc 0x%08h instr 0x%08h err %0d",
                                 dbg_cycle, i, fb_enq_slot[i].pc, fb_enq_slot[i].instr,
                                 fb_enq_slot[i].fetch_err);
                    end
                end
            end
            $fflush;    // keeps the trace in order with the cocotb log when piped
        end
    end
`endif
endmodule
