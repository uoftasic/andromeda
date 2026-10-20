// =============================================================================
// andromeda_pkg.sv
//
// Shared parameters, types and enums for Andromeda, the 4-wide RV32IF
// out-of-order core. Every module imports this package:
//
//     import andromeda_pkg::*;
//
// Source: Andromeda file 00 (Overview), Section 3, Oct 10 2026.
//
// The five interface structs (ptag_t, fetch_slot_t, uop_t, wb_t, wakeup_t)
// are the contract between unit groups. Change them only by agreement.
// Sections marked PROPOSED are baselines the doc asks for but does not
// spell out (op encodings, branch types); confirm them with the owning groups.
//
// Packed-struct note: the first field listed is the MSB.
// =============================================================================

package andromeda_pkg;

  // ===========================================================================
  // 1. Core parameters (Section 3 table)
  //    Change sizes here only. Derived widths below follow automatically.
  // ===========================================================================

  localparam int unsigned RST_VEC    = 32'h0;

  localparam int unsigned XLEN       = 32;  // data and address width

  localparam int unsigned FETCH_W    = 4;   // instructions per cycle, in-order stages
  localparam int unsigned DISPATCH_W = 4;
  localparam int unsigned COMMIT_W   = 4;

  localparam int unsigned NUM_PORTS  = 4;   // issue ports = writeback buses = wakeup buses

  localparam int unsigned NUM_PREG   = 64;  // physical registers per file (int and FP each)

  localparam int unsigned ROB_DEPTH  = 32;  // reorder buffer entries (power of 2)

  localparam int unsigned IQ_INT     = 16;  // integer issue queue, feeds P0 and P1
  localparam int unsigned IQ_FP      = 8;   // FP issue queue, feeds P2
  localparam int unsigned IQ_MEM     = 8;   // memory issue queue, feeds P3

  localparam int unsigned LQ_DEPTH   = 8;   // load queue entries (power of 2)
  localparam int unsigned SQ_DEPTH   = 8;   // store queue entries (power of 2)

  localparam int unsigned FB_DEPTH   = 16;  // fetch buffer entries

  localparam int unsigned RAS_DEPTH  = 8;   // PLACEHOLDER: return address stack depth

  // ===========================================================================
  // 2. Derived widths (do not edit; they follow the sizes above)
  // ===========================================================================

  // Physical tag = {is_fp, idx}
  localparam int unsigned PREG_IDX_W = $clog2(NUM_PREG);   // 6
  localparam int unsigned PTAG_W     = 1 + PREG_IDX_W;     // 7

  // ROB id = {wrap, idx}. The wrap bit makes age comparison work across wraparound.
  localparam int unsigned ROB_IDX_W  = $clog2(ROB_DEPTH);  // 5
  localparam int unsigned ROB_ID_W   = ROB_IDX_W + 1;      // 6

  // Load / store queue ids = {wrap, idx}
  localparam int unsigned LQ_IDX_W   = $clog2(LQ_DEPTH);   // 3
  localparam int unsigned LQ_ID_W    = LQ_IDX_W + 1;       // 4
  localparam int unsigned SQ_IDX_W   = $clog2(SQ_DEPTH);   // 3
  localparam int unsigned SQ_ID_W    = SQ_IDX_W + 1;       // 4

  // ===========================================================================
  // 3. ISA and field widths (fixed by RV32IF; listed in the struct tables)
  // ===========================================================================

  localparam int unsigned ILEN        = 32;               // instruction width
  localparam int unsigned FETCH_BYTES = FETCH_W * 4;      // 16-byte fetch block

  localparam int unsigned NUM_AREG    = 32;               // x0-x31, f0-f31
  localparam int unsigned AREG_W      = $clog2(NUM_AREG); // 5

  localparam int unsigned FU_TYPE_W   = 3;   // uop_t.fu_type
  localparam int unsigned OP_W        = 6;   // uop_t.op integer op index
  localparam int unsigned RM_W        = 3;   // FP rounding mode
  localparam int unsigned MEM_SIZE_W  = 2;   // byte 00 / half 01 / word 10
  localparam int unsigned EXC_CAUSE_W = 5;   // exception cause (mcause code)
  localparam int unsigned FFLAGS_W    = 5;   // FP exception flags {NV, DZ, OF, UF, NX}
  localparam int unsigned CSR_ADDR_W  = 12;  // CSR address (instr[31:20])
  localparam int unsigned CSR_OP_W    = 3;   // csr_unit request op: CSRRW/S/C and immediate forms

  // ===========================================================================
  // 4. Execution port indices (Section 1.2)
  //    Use these to index the wb[NUM_PORTS] and wakeup[NUM_PORTS] arrays.
  // ===========================================================================

  localparam int unsigned PORT_ALU     = 0;  // P0: alu      (integer ALU, 1 cycle)
  localparam int unsigned PORT_ALU_BRU = 1;  // P1: alu_bru  (ALU + branch + CSR, 1 cycle)
  localparam int unsigned PORT_FPU     = 2;  // P2: fpu_top  (1 to ~16 cycles)
  localparam int unsigned PORT_LSU     = 3;  // P3: lsu      (2+ cycles)

  // ===========================================================================
  // 5. Enumerations
  // ===========================================================================

  // Which unit a uop needs. Picks the issue queue and port. (Given in Section 3.)
  typedef enum logic [FU_TYPE_W-1:0] {
    FU_ALU,   // P0 or P1
    FU_BRU,   // P1
    FU_CSR,   // P1
    FU_FPU,   // P2
    FU_LSU    // P3
  } fu_type_e;

  // Load/store size. Values match RISC-V funct3[1:0].
  typedef enum logic [MEM_SIZE_W-1:0] {
    MEM_BYTE = 2'b00,
    MEM_HALF = 2'b01,
    MEM_WORD = 2'b10
  } mem_size_e;

  // FP rounding mode. Values match the RISC-V rm field.
  // 3'b101 and 3'b110 are reserved: decode flags them as illegal.
  typedef enum logic [RM_W-1:0] {
    RM_RNE = 3'b000,  // round to nearest, ties to even
    RM_RTZ = 3'b001,  // round toward zero
    RM_RDN = 3'b010,  // round down (toward -inf)
    RM_RUP = 3'b011,  // round up (toward +inf)
    RM_RMM = 3'b100,  // round to nearest, ties to max magnitude
    RM_DYN = 3'b111   // dynamic: use frm CSR
  } rm_e;

  // Exception causes. Values match the RISC-V mcause exception codes
  // that can occur on a machine-mode-only RV32IF core.
  typedef enum logic [EXC_CAUSE_W-1:0] {
    EXC_INSTR_MISALIGNED   = 5'd0,
    EXC_INSTR_ACCESS_FAULT = 5'd1,   // from fetch_slot_t.fetch_err
    EXC_ILLEGAL_INSTR      = 5'd2,
    EXC_BREAKPOINT         = 5'd3,   // EBREAK
    EXC_LOAD_MISALIGNED    = 5'd4,
    EXC_LOAD_ACCESS_FAULT  = 5'd5,
    EXC_STORE_MISALIGNED   = 5'd6,
    EXC_STORE_ACCESS_FAULT = 5'd7,
    EXC_ECALL_M            = 5'd11   // ECALL from machine mode
  } exc_cause_e;

  // PROPOSED: branch types used by the predictor (Section 3, "Enumerations").
  // Call / return follow the RISC-V hint convention (rd or rs1 = x1/x5).
  typedef enum logic [2:0] {
    BR_NONE,  // not a control-flow instruction
    BR_COND,  // BEQ, BNE, BLT, BGE, BLTU, BGEU
    BR_JAL,   // JAL, not a call
    BR_JALR,  // JALR, not a call or return
    BR_CALL,  // JAL/JALR with rd = x1/x5: push RAS
    BR_RET    // JALR with rs1 = x1/x5, rd = x0: pop RAS
  } br_type_e;

  // ---------------------------------------------------------------------------
  // PROPOSED: op encodings, one enum per unit (Section 3, "Enumerations").
  // uop_t.op is a plain OP_W-bit field shared by all units; each unit casts it:
  //
  //     alu_op_e alu_op;
  //     assign alu_op = alu_op_e'(uop.op);
  //
  // Together these cover RV32I + F + Zicsr. If the M extension goes in,
  // add MUL/DIV ops (to alu_op_e or a new unit enum) here.
  // ---------------------------------------------------------------------------

  // FU_ALU (P0, P1). Operand B is imm when use_imm = 1, else rs2.
  typedef enum logic [OP_W-1:0] {
    ALU_ADD,    // ADD, ADDI
    ALU_SUB,    // SUB
    ALU_SLL,    // SLL, SLLI
    ALU_SLT,    // SLT, SLTI
    ALU_SLTU,   // SLTU, SLTIU
    ALU_XOR,    // XOR, XORI
    ALU_SRL,    // SRL, SRLI
    ALU_SRA,    // SRA, SRAI
    ALU_OR,     // OR, ORI
    ALU_AND,    // AND, ANDI
    ALU_LUI,    // rd = imm
    ALU_AUIPC   // rd = pc + imm
  } alu_op_e;

  // FU_BRU (P1). JAL/JALR write pc + 4 to rd.
  typedef enum logic [OP_W-1:0] {
    BRU_BEQ,
    BRU_BNE,
    BRU_BLT,
    BRU_BGE,
    BRU_BLTU,
    BRU_BGEU,
    BRU_JAL,
    BRU_JALR
  } bru_op_e;

  // FU_CSR (P1). All of these set uop_t.serialize.
  // The *I forms take the 5-bit zimm from uop_t.imm instead of rs1.
  typedef enum logic [OP_W-1:0] {
    CSR_RW,       // CSRRW
    CSR_RS,       // CSRRS
    CSR_RC,       // CSRRC
    CSR_RWI,      // CSRRWI
    CSR_RSI,      // CSRRSI
    CSR_RCI,      // CSRRCI
    CSR_ECALL,    // raises EXC_ECALL_M
    CSR_EBREAK,   // raises EXC_BREAKPOINT
    CSR_MRET,
    CSR_WFI,
    CSR_FENCE,
    CSR_FENCE_I
  } csr_op_e;

  // FU_FPU (P2). FMA forms read rs3. rm comes from uop_t.rm.
  typedef enum logic [OP_W-1:0] {
    FPU_ADD,       // FADD.S
    FPU_SUB,       // FSUB.S
    FPU_MUL,       // FMUL.S
    FPU_DIV,       // FDIV.S
    FPU_SQRT,      // FSQRT.S
    FPU_MADD,      // FMADD.S   rs1*rs2 + rs3
    FPU_MSUB,      // FMSUB.S   rs1*rs2 - rs3
    FPU_NMSUB,     // FNMSUB.S -(rs1*rs2) + rs3
    FPU_NMADD,     // FNMADD.S -(rs1*rs2) - rs3
    FPU_SGNJ,      // FSGNJ.S
    FPU_SGNJN,     // FSGNJN.S
    FPU_SGNJX,     // FSGNJX.S
    FPU_MIN,       // FMIN.S
    FPU_MAX,       // FMAX.S
    FPU_EQ,        // FEQ.S    (writes int rd)
    FPU_LT,        // FLT.S    (writes int rd)
    FPU_LE,        // FLE.S    (writes int rd)
    FPU_CLASS,     // FCLASS.S (writes int rd)
    FPU_CVT_W_S,   // FCVT.W.S  float -> int32
    FPU_CVT_WU_S,  // FCVT.WU.S float -> uint32
    FPU_CVT_S_W,   // FCVT.S.W  int32  -> float
    FPU_CVT_S_WU,  // FCVT.S.WU uint32 -> float
    FPU_MV_X_W,    // FMV.X.W  FP bits -> int rd
    FPU_MV_W_X     // FMV.W.X  int bits -> FP rd
  } fpu_op_e;

  // FU_LSU (P3). Size and sign come from uop_t.mem_size / mem_signed.
  // FLW / FSW are LSU_LOAD / LSU_STORE with rd_fp / rs2_fp = 1.
  typedef enum logic [OP_W-1:0] {
    LSU_LOAD,      // LB, LH, LW, LBU, LHU, FLW
    LSU_STORE      // SB, SH, SW, FSW
  } lsu_op_e;

  // ===========================================================================
  // 6. Id types
  // ===========================================================================

  typedef logic [ROB_ID_W-1:0] rob_id_t;  // {wrap, idx}; also the uop's age
  typedef logic [LQ_ID_W-1:0]  lq_id_t;   // {wrap, idx}
  typedef logic [SQ_ID_W-1:0]  sq_id_t;   // {wrap, idx}

  // ===========================================================================
  // 7. Shared structs (Section 3)
  // ===========================================================================

  // ---------------------------------------------------------------------------
  // ptag_t (7 bits): physical register tag
  // One 7-bit compare checks "same file and same register", so integer p12
  // never wakes a uop waiting on FP p12.
  // ---------------------------------------------------------------------------
  typedef struct packed {
    logic                  is_fp;  // 0 = integer file, 1 = FP file
    logic [PREG_IDX_W-1:0] idx;    // register number within that file, 0-63
  } ptag_t;

  // Integer p0 is hardwired to zero and x0 always maps to it.
  localparam ptag_t PTAG_ZERO = '0;

  // ---------------------------------------------------------------------------
  // fetch_slot_t: one fetched instruction
  // Created by ifu, carried through fetch_buffer, consumed by decode_stage.
  // ---------------------------------------------------------------------------
  typedef struct packed {
    logic                 valid;        // slot holds a real instruction
    logic [XLEN-1:0]      pc;           // address of the instruction
    logic [ILEN-1:0]      instr;        // raw 32-bit encoding
    logic                 pred_taken;   // predictor said taken branch / jump 
    logic [XLEN-1:0]      pred_target;  // where the predictor said it goes 
    logic                 fetch_err;    // imem reported a fault for this block could happen when timeout occurs or the addr exceed i ram address range
  } fetch_slot_t;

  // ---------------------------------------------------------------------------
  // uop_t: one instruction in flight (~215 bits with BP_META_W = 16)
  // One RISC-V instruction = exactly one uop. Filled in stage by stage:
  // decode, then rename, then dispatch. Pass the full struct between stages;
  // storage structures (ROB, issue queues) should keep only what they need.
  // ---------------------------------------------------------------------------
  typedef struct packed {
    // ---- Filled by decode ----------------------------------------------------
    logic [XLEN-1:0]      pc;           // AUIPC, branches, exceptions, trace
    fu_type_e             fu_type;      // which unit; picks issue queue and port
    logic [OP_W-1:0]      op;           // op within the unit (cast to *_op_e)

    logic [AREG_W-1:0]    rs1_arch;     // architectural sources
    logic [AREG_W-1:0]    rs2_arch;
    logic [AREG_W-1:0]    rs3_arch;     // FMA only

    logic                 rs1_en;       // source is actually read
    logic                 rs2_en;       // (reads of x0 count as not read)
    logic                 rs3_en;

    logic                 rs1_fp;       // source is an FP register
    logic                 rs2_fp;       // (rs3 is FMA only and always FP)

    logic [AREG_W-1:0]    rd_arch;      // architectural destination
    logic                 rd_en;        // writes a register (0 for stores,
                                        // branches and writes to x0)
    logic                 rd_fp;        // destination is an FP register

    logic [XLEN-1:0]      imm;          // sign-extended immediate
    logic                 use_imm;      // ALU takes imm instead of rs2
    rm_e                  rm;           // FP rounding mode (RM_DYN = use frm)

    mem_size_e            mem_size;     // load/store size
    logic                 mem_signed;   // sign-extend loaded value

    logic                 pred_taken;   // copied from the fetch slot 
    logic [XLEN-1:0]      pred_target;

    logic                 exc_valid;    // exception known at decode
    exc_cause_e           exc_cause;    // (illegal instruction, fetch fault)

    logic                 serialize;    // wait for the ROB head before issuing

    // ---- Filled by rename ----------------------------------------------------
    ptag_t                prs1;         // physical sources
    ptag_t                prs2;
    ptag_t                prs3;

    logic                 prs1_ready;   // source value already produced;
    logic                 prs2_ready;   // updated by wakeups until issue
    logic                 prs3_ready;

    ptag_t                prd;          // newly allocated destination
    ptag_t                old_prd;      // previous mapping of rd_arch,
                                        // freed when this uop commits

    // ---- Filled by dispatch --------------------------------------------------
    rob_id_t              rob_id;       // ROB index + wrap bit; also the age
    lq_id_t               lq_id;        // loads only: LQ index + wrap bit
    sq_id_t               sq_id;        // stores: their SQ entry
                                        // loads: SQ tail at dispatch (older stores)
  } uop_t;

  // ---------------------------------------------------------------------------
  // wb_t: one writeback bus (125 bits)
  // Each of the 4 ports drives one every cycle it finishes a uop.
  // Read by the register files, the ROB and the rr_stage bypass.
  // Fields a port never uses are tied to zero.
  // ---------------------------------------------------------------------------
  typedef struct packed {
    logic                valid;          // a uop finished this cycle
    rob_id_t             rob_id;         // ROB entry to mark complete
    logic                rd_en;          // result goes to a register
    ptag_t               prd;            // which physical register (file = prd.is_fp)
    logic [XLEN-1:0]     data;           // the result

    logic                exc_valid;      // exception found while executing
    exc_cause_e          exc_cause;
    logic [XLEN-1:0]     exc_tval;       // e.g. the faulting address

    logic [FFLAGS_W-1:0] fflags;         // FP flags, ORed into fflags at commit

    logic                br_valid;       // uop was a branch or jump
    logic                br_taken;       // real outcome
    logic [XLEN-1:0]     br_target;
    logic                br_mispredict;  // differs from prediction;
                                         // ROB flushes when this uop commits
  } wb_t;

  // ---------------------------------------------------------------------------
  // wakeup_t: tag broadcast (8 bits)
  // Each port drives one. Kept separate from wb_t so a later version can wake
  // dependents early without changing any ports. Fans out to every source
  // comparator in every issue queue, so keep it small.
  // ---------------------------------------------------------------------------
  typedef struct packed {
    logic  valid;  // a tag is being broadcast this cycle
    ptag_t ptag;   // physical register that is now (or about to be) ready
  } wakeup_t;

  // Widths with the default sizes: ptag_t 7, wakeup_t 8, fetch_slot_t 115,
  // wb_t 125, uop_t 215. To size storage, use $bits() on a signal of the
  // struct type; Yosys does not accept $bits() on a type name.

endpackage : andromeda_pkg