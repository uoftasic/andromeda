// =============================================================================
// fpu_top.sv
//
// Andromeda P2 execution unit: wraps FPnew (fpnew_top) behind the Andromeda
// issue / writeback / wakeup interface.
//
//  - Configured as scalar RV32F: FP32 only, INT32 only, no SIMD, TH32 divsqrt.
//  - rob_id / prd / rd_en ride through FPnew as the tag, so results may
//    complete out of order (e.g. an ADD overtaking a DIV).
//  - wb has no back-pressure, so FPnew's out_ready is tied high. When two
//    op groups finish together, FPnew's internal arbiter picks one and stalls
//    the other in its pipeline, which in turn drops in_ready for that group.
//  - in_ready is FPnew's: in_valid & (target op group can accept). It depends
//    combinationally on in_valid and in_uop.op, so in_valid must not be
//    derived combinationally from in_ready.
//  - flush clears every FPnew pipeline and the iterative divider. The flush
//    cycle also suppresses issue, wb and wakeup.
//  - A dynamic rounding mode whose frm value is invalid (101, 110, 111) is
//    reported as EXC_ILLEGAL_INSTR on wb, as the RISC-V spec requires.
// =============================================================================

module fpu_top
  import andromeda_pkg::*;
#(
  // Pipeline registers per op group (all formats). Latency of each group is
  // PipeRegs + 1 cycle into wb for the pipelined groups; DIVSQRT is iterative.
  parameter fpnew_pkg::fpu_implementation_t Implementation = '{
    PipeRegs:   '{'{default: 2},   // ADDMUL : add, sub, mul, fma
                  '{default: 1},   // DIVSQRT: div, sqrt (iterative, ~11-16 cycles)
                  '{default: 1},   // NONCOMP: sgnj, min/max, cmp, class, fmv
                  '{default: 1}},  // CONV   : fcvt
    UnitTypes:  '{'{default: fpnew_pkg::PARALLEL},  // ADDMUL
                  '{default: fpnew_pkg::MERGED},    // DIVSQRT (TH32 needs MERGED)
                  '{default: fpnew_pkg::PARALLEL},  // NONCOMP
                  '{default: fpnew_pkg::MERGED}},   // CONV
    PipeConfig: fpnew_pkg::DISTRIBUTED
  }
) (
  input  logic             clk,
  input  logic             rst_n,

  input  logic             flush,      // be_flush: drops all in-flight operations, including divides

  // Issue from rr_stage
  input  logic             in_valid,
  input  uop_t             in_uop,     // op, rm, prd, rob_id
  input  logic [XLEN-1:0]  in_rs1,     // From the integer file for int-to-FP ops
  input  logic [XLEN-1:0]  in_rs2,
  input  logic [XLEN-1:0]  in_rs3,     // FMA forms only
  output logic             in_ready,   // Can accept this uop (divider and result buffer checked)

  input  logic [RM_W-1:0]  frm,        // Dynamic rounding mode from csr_unit

  output wb_t              wb,         // Result, destination tag and fflags
  output wakeup_t          wakeup      // Destination tag
);

  // ---------------------------------------------------------------------------
  // Configuration checks
  // ---------------------------------------------------------------------------
  localparam fpnew_pkg::fpu_features_t Features = fpnew_pkg::RV32F;

  // synopsys translate_off
  initial begin
    if (XLEN != 32)            $fatal(1, "fpu_top: wrapper assumes XLEN = 32 (RV32F)");
    if (FFLAGS_W != 5)         $fatal(1, "fpu_top: fflags must be 5 bits");
  end
  // synopsys translate_on

  // ---------------------------------------------------------------------------
  // Tag carried through FPnew alongside each operation
  // ---------------------------------------------------------------------------
  typedef struct packed {
    rob_id_t rob_id;
    logic    rd_en;
    ptag_t   prd;
    logic    illegal_rm;  // dynamic rm resolved to a reserved value
  } fpu_tag_t;

  // ---------------------------------------------------------------------------
  // Decode: Andromeda fpu_op_e -> FPnew operation
  // ---------------------------------------------------------------------------
  fpu_op_e                    fop;
  logic [RM_W-1:0]            rm_resolved;    // static rm, or frm when RM_DYN
  logic                       uses_rm;        // op actually rounds
  logic                       illegal_rm;

  logic [2:0][XLEN-1:0]       fpn_operands;
  fpnew_pkg::roundmode_e      fpn_rnd_mode_dec, fpn_rnd_mode;
  fpnew_pkg::operation_e      fpn_op;
  logic                       fpn_op_mod;
  fpu_tag_t                   fpn_tag_in, fpn_tag_out;

  assign fop         = fpu_op_e'(in_uop.op);
  assign rm_resolved = (in_uop.rm == RM_DYN) ? frm : RM_W'(in_uop.rm);
  // Spec: 101 and 110 are reserved, and 111 in frm is invalid.
  assign illegal_rm  = uses_rm && (rm_resolved inside {3'b101, 3'b110, 3'b111});
  // Never hand FPnew a reserved rounding mode; the uop traps at commit.
  assign fpn_rnd_mode = illegal_rm ? fpnew_pkg::RNE : fpn_rnd_mode_dec;

  always_comb begin
    // Defaults: operands in natural order, rounding from the instruction
    fpn_operands     = {in_rs3, in_rs2, in_rs1};   // [0]=rs1, [1]=rs2, [2]=rs3
    fpn_rnd_mode_dec = fpnew_pkg::roundmode_e'(rm_resolved);
    fpn_op       = fpnew_pkg::ADD;
    fpn_op_mod   = 1'b0;
    uses_rm      = 1'b1;

    unique case (fop)
      // ---- ADDMUL ----------------------------------------------------------
      // FPnew ADD computes op[1] + op[2] (op[0] is forced to 1.0 internally);
      // op_mod negates op[2].
      FPU_ADD:   begin fpn_op = fpnew_pkg::ADD;    fpn_operands = {in_rs2, in_rs1, in_rs1}; end
      FPU_SUB:   begin fpn_op = fpnew_pkg::ADD;    fpn_operands = {in_rs2, in_rs1, in_rs1}; fpn_op_mod = 1'b1; end
      FPU_MUL:   fpn_op = fpnew_pkg::MUL;
      // FMADD: a*b + c; op_mod negates c.  FNMSUB: -(a*b) + c; op_mod negates c.
      FPU_MADD:  fpn_op = fpnew_pkg::FMADD;
      FPU_MSUB:  begin fpn_op = fpnew_pkg::FMADD;  fpn_op_mod = 1'b1; end
      FPU_NMSUB: fpn_op = fpnew_pkg::FNMSUB;
      FPU_NMADD: begin fpn_op = fpnew_pkg::FNMSUB; fpn_op_mod = 1'b1; end

      // ---- DIVSQRT ---------------------------------------------------------
      FPU_DIV:   fpn_op = fpnew_pkg::DIV;
      FPU_SQRT:  fpn_op = fpnew_pkg::SQRT;

      // ---- NONCOMP: rm selects the variant ---------------------------------
      FPU_SGNJ:  begin fpn_op = fpnew_pkg::SGNJ;   fpn_rnd_mode_dec = fpnew_pkg::RNE; uses_rm = 1'b0; end
      FPU_SGNJN: begin fpn_op = fpnew_pkg::SGNJ;   fpn_rnd_mode_dec = fpnew_pkg::RTZ; uses_rm = 1'b0; end
      FPU_SGNJX: begin fpn_op = fpnew_pkg::SGNJ;   fpn_rnd_mode_dec = fpnew_pkg::RDN; uses_rm = 1'b0; end
      FPU_MIN:   begin fpn_op = fpnew_pkg::MINMAX; fpn_rnd_mode_dec = fpnew_pkg::RNE; uses_rm = 1'b0; end
      FPU_MAX:   begin fpn_op = fpnew_pkg::MINMAX; fpn_rnd_mode_dec = fpnew_pkg::RTZ; uses_rm = 1'b0; end
      FPU_LE:    begin fpn_op = fpnew_pkg::CMP;    fpn_rnd_mode_dec = fpnew_pkg::RNE; uses_rm = 1'b0; end
      FPU_LT:    begin fpn_op = fpnew_pkg::CMP;    fpn_rnd_mode_dec = fpnew_pkg::RTZ; uses_rm = 1'b0; end
      FPU_EQ:    begin fpn_op = fpnew_pkg::CMP;    fpn_rnd_mode_dec = fpnew_pkg::RDN; uses_rm = 1'b0; end
      FPU_CLASS: begin fpn_op = fpnew_pkg::CLASSIFY;                              uses_rm = 1'b0; end
      // Raw bit moves: SGNJ with RUP is a passthrough of op[0], no flags.
      FPU_MV_X_W: begin fpn_op = fpnew_pkg::SGNJ;  fpn_rnd_mode_dec = fpnew_pkg::RUP; fpn_op_mod = 1'b1; uses_rm = 1'b0; end
      FPU_MV_W_X: begin fpn_op = fpnew_pkg::SGNJ;  fpn_rnd_mode_dec = fpnew_pkg::RUP;                    uses_rm = 1'b0; end

      // ---- CONV: op_mod = unsigned integer ---------------------------------
      FPU_CVT_W_S:  fpn_op = fpnew_pkg::F2I;
      FPU_CVT_WU_S: begin fpn_op = fpnew_pkg::F2I; fpn_op_mod = 1'b1; end
      FPU_CVT_S_W:  fpn_op = fpnew_pkg::I2F;
      FPU_CVT_S_WU: begin fpn_op = fpnew_pkg::I2F; fpn_op_mod = 1'b1; end

      default: ;  // decode never issues other encodings to P2
    endcase

    // Never hand FPnew a reserved rounding mode; the uop traps at commit.
  end

  assign fpn_tag_in = '{
    rob_id:     in_uop.rob_id,
    rd_en:      in_uop.rd_en,
    prd:        in_uop.prd,
    illegal_rm: illegal_rm
  };

  // ---------------------------------------------------------------------------
  // FPnew
  // ---------------------------------------------------------------------------
  logic [XLEN-1:0]     fpn_result;
  fpnew_pkg::status_t  fpn_status;
  logic                fpn_out_valid;

  fpnew_top #(
    .Features       ( Features            ),
    .Implementation ( Implementation      ),
    .DivSqrtSel     ( fpnew_pkg::TH32     ),
    .TagType        ( fpu_tag_t           ),
    .TrueSIMDClass  ( 0                   ),
    .EnableSIMDMask ( 0                   )
  ) i_fpnew (
    .clk_i          ( clk                 ),
    .rst_ni         ( rst_n               ),
    .operands_i     ( fpn_operands        ),
    .rnd_mode_i     ( fpn_rnd_mode        ),
    .op_i           ( fpn_op              ),
    .op_mod_i       ( fpn_op_mod          ),
    .src_fmt_i      ( fpnew_pkg::FP32     ),
    .dst_fmt_i      ( fpnew_pkg::FP32     ),
    .int_fmt_i      ( fpnew_pkg::INT32    ),
    .vectorial_op_i ( 1'b0                ),
    .tag_i          ( fpn_tag_in          ),
    .simd_mask_i    ( '1                  ),
    .in_valid_i     ( in_valid & ~flush   ),
    .in_ready_o     ( in_ready            ),
    .flush_i        ( flush               ),
    .result_o       ( fpn_result          ),
    .status_o       ( fpn_status          ),
    .tag_o          ( fpn_tag_out         ),
    .out_valid_o    ( fpn_out_valid       ),
    .out_ready_i    ( 1'b1                ),  // wb cannot stall
    .busy_o         ( /* unused */        ),
    .early_valid_o  ( /* unused */        )
  );

  // ---------------------------------------------------------------------------
  // Writeback and wakeup
  // ---------------------------------------------------------------------------
  logic done;
  assign done = fpn_out_valid & ~flush;

  always_comb begin
    wb               = '0;   // br_* and unused fields tied to zero
    wb.valid         = done;
    wb.rob_id        = fpn_tag_out.rob_id;
    wb.rd_en         = fpn_tag_out.rd_en;
    wb.prd           = fpn_tag_out.prd;
    wb.data          = fpn_result;
    wb.exc_valid     = fpn_tag_out.illegal_rm;
    wb.exc_cause     = EXC_ILLEGAL_INSTR;
    wb.exc_tval      = '0;
    // status_t is {NV, DZ, OF, UF, NX}, the same bit order as fflags
    wb.fflags        = fpn_tag_out.illegal_rm ? '0 : FFLAGS_W'(fpn_status);
  end

  assign wakeup.valid = done & fpn_tag_out.rd_en;
  assign wakeup.ptag  = fpn_tag_out.prd;

endmodule : fpu_top
