// Andromeda core top level: instruction and data memory ports, interrupts,
// and a per-instruction commit trace for the testbench.
module andromeda_top
  import andromeda_pkg::*;
(
  input  logic                           clk,
  input  logic                           rst_n,

  input  logic [XLEN-1:0]                iboot_addr,       // PC loaded at reset

  // Instruction memory request channel
  output logic                           imem_req_valid,   // Instruction fetch request
  input  logic                           imem_req_ready,   // Memory accepts the request
  output logic [XLEN-1:0]                imem_req_addr,    // 16-byte aligned fetch block address

  // Instruction memory response channel (no ready: the core always accepts)
  input  logic                           imem_resp_valid,  // Fetch data returned (in request order)
  input  logic [FETCH_W-1:0][ILEN-1:0]   imem_resp_data,   // 4 instructions, lowest address in bits [31:0]
  input  logic                           imem_resp_err,    // Access fault on this fetch

  // Data memory request channel
  output logic                           dmem_req_valid,   // Data request
  input  logic                           dmem_req_ready,   // Memory accepts the request
  output logic                           dmem_req_we,      // 1 = store, 0 = load
  output logic [XLEN-1:0]                dmem_req_addr,    // Byte address
  output logic [XLEN-1:0]                dmem_req_wdata,   // Store data, aligned to the word
  output logic [XLEN/8-1:0]              dmem_req_wstrb,   // Byte enables for stores
  output logic [LQ_IDX_W-1:0]            dmem_req_id,      // Load queue index, returned with the response

  // Data memory response channel (no ready: the core always accepts)
  input  logic                           dmem_resp_valid,  // Load data returned
  input  logic [XLEN-1:0]                dmem_resp_rdata,  // Whole aligned word
  input  logic [LQ_IDX_W-1:0]            dmem_resp_id,     // Matches dmem_req_id
  input  logic                           dmem_resp_err,    // Access fault

  // Interrupts (can be tied to 0 in v1)
  input  logic                           irq_timer,        // Machine timer interrupt
  input  logic                           irq_external,     // Machine external interrupt

  // Commit trace, one lane per retiring instruction; lane 0 is in the low bits
  output logic [COMMIT_W-1:0]            trace_valid,      // One bit per retiring instruction this cycle
  output logic [COMMIT_W-1:0][XLEN-1:0]  trace_pc,         // PC of each retiring instruction
  output logic [COMMIT_W-1:0][ILEN-1:0]  trace_instr,      // Encoding of each retiring instruction
  output logic [COMMIT_W-1:0]            trace_rd_we,      // Architectural register write of each
  output logic [COMMIT_W-1:0]            trace_rd_fp,      //   retiring instruction: write enable,
  output logic [COMMIT_W-1:0][AREG_W-1:0] trace_rd_addr,   //   FP file, register number
  output logic [COMMIT_W-1:0][XLEN-1:0]  trace_rd_data,    //   and value written
  output logic                           trace_trap        // An exception or interrupt was taken this cycle
);

endmodule
