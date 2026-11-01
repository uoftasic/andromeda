// Integer ALU, port P0 (Spec 07, Section 3.1). Combinational ALU between
// rr_stage and a registered writeback bus. alu_bru instantiates it for its
// arithmetic so both ports behave identically.
module alu
  import andromeda_pkg::*;
(
  input  logic             clk,
  input  logic             rst_n,

  input  logic             flush,      // be_flush

  // Issue from rr_stage
  input  logic             in_valid,
  input  uop_t             in_uop,
  input  logic [XLEN-1:0]  in_rs1,     // Operand 1
  input  logic [XLEN-1:0]  in_rs2,     // Operand 2, already replaced by imm when use_imm
  output logic             in_ready,   // Always 1

  output wb_t              wb,         // Result next cycle
  output wakeup_t          wakeup      // Destination tag, same cycle as wb in v1
);

  // Single-cycle, never stalls
  assign in_ready = 1'b1;

  alu_op_e alu_op;
  assign alu_op = alu_op_e'(in_uop.op);

  // ---------------------------------------------------------------------------
  // Shared adder: ADD, SUB, SLT, SLTU, AUIPC (rr_stage puts pc in rs1 for AUIPC)
  // ---------------------------------------------------------------------------
  logic            is_sub;
  logic [XLEN-1:0] add_b;
  logic [XLEN:0]   add_sum;    // MSB is carry out
  logic            lt_signed;
  logic            lt_unsigned;

  assign is_sub  = (alu_op == ALU_SUB) || (alu_op == ALU_SLT) || (alu_op == ALU_SLTU);
  assign add_b   = is_sub ? ~in_rs2 : in_rs2;
  assign add_sum = {1'b0, in_rs1} + {1'b0, add_b} + {{XLEN{1'b0}}, is_sub};

  // a - b borrows (no carry out) exactly when a < b unsigned
  assign lt_unsigned = ~add_sum[XLEN];
  // Signs differ: a is less iff a is negative. Same sign: no overflow, use sign of a - b.
  assign lt_signed   = (in_rs1[XLEN-1] ^ in_rs2[XLEN-1]) ? in_rs1[XLEN-1] : add_sum[XLEN-1];

  // ---------------------------------------------------------------------------
  // Shifter: one right shifter; SLL reverses the operand in and the result out
  // ---------------------------------------------------------------------------
  function automatic logic [XLEN-1:0] bit_reverse(input logic [XLEN-1:0] x);
    for (int i = 0; i < XLEN; i++) bit_reverse[i] = x[XLEN-1-i];
  endfunction

  logic [$clog2(XLEN)-1:0] shamt;
  logic [XLEN-1:0]         shift_in;
  logic                    shift_fill;
  logic [XLEN:0]           shift_ext;
  logic [XLEN-1:0]         shift_r;

  assign shamt      = in_rs2[$clog2(XLEN)-1:0];
  assign shift_in   = (alu_op == ALU_SLL) ? bit_reverse(in_rs1) : in_rs1;
  assign shift_fill = (alu_op == ALU_SRA) & in_rs1[XLEN-1];
  assign shift_ext  = $signed({shift_fill, shift_in}) >>> shamt;
  assign shift_r    = shift_ext[XLEN-1:0];

  // ---------------------------------------------------------------------------
  // Result mux
  // ---------------------------------------------------------------------------
  logic [XLEN-1:0] result;

  always_comb begin
    case (alu_op)
      ALU_ADD,
      ALU_SUB,
      ALU_AUIPC: result = add_sum[XLEN-1:0];
      ALU_SLT:   result = {{(XLEN-1){1'b0}}, lt_signed};
      ALU_SLTU:  result = {{(XLEN-1){1'b0}}, lt_unsigned};
      ALU_SLL:   result = bit_reverse(shift_r);
      ALU_SRL,
      ALU_SRA:   result = shift_r;
      ALU_XOR:   result = in_rs1 ^ in_rs2;
      ALU_OR:    result = in_rs1 | in_rs2;
      ALU_AND:   result = in_rs1 & in_rs2;
      ALU_LUI:   result = in_rs2;       // imm, already shifted by decode
      default:   result = '0;
    endcase
  end

  // ---------------------------------------------------------------------------
  // Writeback register. Only valid is reset; flush drops the incoming uop.
  // ---------------------------------------------------------------------------
  logic            wb_valid_q;
  rob_id_t         wb_rob_id_q;
  logic            wb_rd_en_q;
  ptag_t           wb_prd_q;
  logic [XLEN-1:0] wb_data_q;

  always_ff @(posedge clk or negedge rst_n) begin
    if (!rst_n) wb_valid_q <= 1'b0;
    else        wb_valid_q <= in_valid & ~flush;
  end

  always_ff @(posedge clk) begin
    if (in_valid) begin
      wb_rob_id_q <= in_uop.rob_id;
      wb_rd_en_q  <= in_uop.rd_en;
      wb_prd_q    <= in_uop.prd;
      wb_data_q   <= result;
    end
  end

  // Unused wb fields (exceptions, fflags, branch outcome) are tied to zero
  always_comb begin
    wb        = '0;
    wb.valid  = wb_valid_q;
    wb.rob_id = wb_rob_id_q;
    wb.rd_en  = wb_rd_en_q;
    wb.prd    = wb_prd_q;
    wb.data   = wb_data_q;
  end

  assign wakeup.valid = wb_valid_q & wb_rd_en_q;
  assign wakeup.ptag  = wb_prd_q;

endmodule
