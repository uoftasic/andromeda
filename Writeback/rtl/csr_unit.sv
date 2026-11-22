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
  // ---------------------------------------------------------------------------
  // CSR storage: only the bits that are actually writable.
  // misa and mhartid are constants, fcsr is a view of {frm, fflags},
  // and mip comes straight from the interrupt inputs.
  // ---------------------------------------------------------------------------
  logic [FFLAGS_W-1:0] fflags_q;
  logic [RM_W-1:0]     frm_q;
  logic                mstatus_mie_q;
  logic                mstatus_mpie_q;
  logic [1:0]          mstatus_fs_q;
  logic                mie_mtie_q;
  logic                mie_meie_q;
  logic [XLEN-1:0]     mtvec_q;
  logic [XLEN-1:0]     mscratch_q;
  logic [XLEN-1:0]     mepc_q;
  logic [XLEN-1:0]     mcause_q;
  logic [XLEN-1:0]     mtval_q;
  logic [63:0]         mcycle_q;
  logic [63:0]         minstret_q;

  // Full 32-bit views of the CSRs built from fields
  logic [XLEN-1:0] mstatus_rd, mie_rd, mip_rd;

  always_comb begin
    mstatus_rd = '0;
    mstatus_rd[MSTATUS_MIE_BIT]                    = mstatus_mie_q;
    mstatus_rd[MSTATUS_MPIE_BIT]                   = mstatus_mpie_q;
    mstatus_rd[MSTATUS_MPP_LSB +: 2]               = 2'b11;          // M-mode only
    mstatus_rd[MSTATUS_FS_LSB  +: 2]               = mstatus_fs_q;
    mstatus_rd[MSTATUS_SD_BIT]                     = &mstatus_fs_q;  // FS == dirty

    mie_rd = '0;
    mie_rd[IRQ_M_TIMER]    = mie_mtie_q;
    mie_rd[IRQ_M_EXTERNAL] = mie_meie_q;

    mip_rd = '0;
    mip_rd[IRQ_M_TIMER]    = irq_timer;
    mip_rd[IRQ_M_EXTERNAL] = irq_external;
  end

  // ---------------------------------------------------------------------------
  // req_addr: read mux. addr_hit = 0 for any unimplemented address.
  // ---------------------------------------------------------------------------
  logic [XLEN-1:0] rdata;
  logic            addr_hit;

  always_comb begin
    rdata    = '0;
    addr_hit = 1'b1;
    case (req_addr)
      CSR_FFLAGS:    rdata = XLEN'(fflags_q);
      CSR_FRM:       rdata = XLEN'(frm_q);
      CSR_FCSR:      rdata = XLEN'({frm_q, fflags_q});
      CSR_MSTATUS:   rdata = mstatus_rd;
      CSR_MISA:      rdata = MISA_VALUE;
      CSR_MIE:       rdata = mie_rd;
      CSR_MTVEC:     rdata = mtvec_q;
      CSR_MSCRATCH:  rdata = mscratch_q;
      CSR_MEPC:      rdata = mepc_q;
      CSR_MCAUSE:    rdata = mcause_q;
      CSR_MTVAL:     rdata = mtval_q;
      CSR_MIP:       rdata = mip_rd;
      CSR_MCYCLE:    rdata = mcycle_q[31:0];
      CSR_MCYCLEH:   rdata = mcycle_q[63:32];
      CSR_MINSTRET:  rdata = minstret_q[31:0];
      CSR_MINSTRETH: rdata = minstret_q[63:32];
      CSR_MHARTID:   rdata = '0;
      default:       addr_hit = 1'b0;
    endcase
  end

  // ---------------------------------------------------------------------------
  // req_op: new value to write. Encoding (PROPOSED, must match alu_bru):
  // req_op = {is_imm, funct3[1:0]}, so req_op[1:0] is 01 RW / 10 RS / 11 RC.
  // For immediate forms alu_bru puts zero-extended zimm on req_wdata.
  // ---------------------------------------------------------------------------
  localparam logic [1:0] REQ_RW = 2'b01;
  localparam logic [1:0] REQ_RS = 2'b10;
  localparam logic [1:0] REQ_RC = 2'b11;

  logic [XLEN-1:0] wdata;
  logic            do_write;   // the instruction writes the CSR at all
  logic            illegal;
  logic            csr_we;     // qualified write enable for the per-CSR logic

  always_comb begin
    case (req_op[1:0])
      REQ_RW:  wdata = req_wdata;
      REQ_RS:  wdata = rdata |  req_wdata;
      REQ_RC:  wdata = rdata & ~req_wdata;
      default: wdata = rdata;
    endcase
  end

  assign do_write = (req_op[1:0] == REQ_RW) || (req_wdata != '0);

  // Unimplemented address, or a write to a read-only CSR (addr[11:10] == 11)
  assign illegal  = !addr_hit || (do_write && req_addr[11:10] == 2'b11);
  assign csr_we   = req_valid && do_write && !illegal;

  // Response: old value, one cycle after the request
  always_ff @(posedge clk) begin
    if (!rst_n) begin
      resp_valid   <= 1'b0;
      resp_rdata   <= '0;
      resp_illegal <= 1'b0;
    end else begin
      resp_valid   <= req_valid;
      resp_rdata   <= rdata;
      resp_illegal <= req_valid && illegal;
    end
  end

  // write data
  always_ff @(posedge clk)begin
    if(!rst_n)begin
      fflags_q       <= '0;
      frm_q          <= '0;
      mstatus_mie_q  <= 1'b0;
      mstatus_mpie_q <= 1'b0;
      mstatus_fs_q   <= 2'b01;   // Initial
      mie_mtie_q     <= 1'b0;
      mie_meie_q     <= 1'b0;
      mtvec_q        <= '0;
      mscratch_q     <= '0;
      mepc_q         <= '0;
      mcause_q       <= '0;
      mtval_q        <= '0;
      mcycle_q       <= '0;
      minstret_q     <= '0;
    end
    else begin
      if(csr_we)begin
        case (req_addr)
          CSR_FFLAGS:    fflags_q <= wdata[FFLAGS_W-1:0];
          CSR_FRM:       frm_q    <= wdata[RM_W-1:0];
          CSR_FCSR:      begin
            frm_q    <= wdata[7:5];
            fflags_q <= wdata[4:0];
          end
          CSR_MSTATUS:   begin
            mstatus_mie_q  <= wdata[MSTATUS_MIE_BIT];
            mstatus_mpie_q <= wdata[MSTATUS_MPIE_BIT];
            mstatus_fs_q   <= wdata[MSTATUS_FS_LSB +: 2];
          end
          CSR_MIE:       begin
            mie_mtie_q <= wdata[IRQ_M_TIMER];
            mie_meie_q <= wdata[IRQ_M_EXTERNAL];
          end
          CSR_MTVEC:     mtvec_q    <= {wdata[XLEN-1:2], 2'b00};  // direct mode only
          CSR_MSCRATCH:  mscratch_q <= wdata;
          CSR_MEPC:      mepc_q     <= {wdata[XLEN-1:2], 2'b00};
          CSR_MCAUSE:    mcause_q   <= wdata;
          CSR_MTVAL:     mtval_q    <= wdata;
          CSR_MCYCLE:    mcycle_q[31:0]    <= wdata;
          CSR_MCYCLEH:   mcycle_q[63:32]   <= wdata;
          CSR_MINSTRET:  minstret_q[31:0]  <= wdata;
          CSR_MINSTRETH: minstret_q[63:32] <= wdata;
          default: ;     // misa: WARL, writes ignored; mhartid caught by illegal
        endcase
      end
    end
  end



endmodule
