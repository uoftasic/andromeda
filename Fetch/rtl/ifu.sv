// Fetch unit (Spec 02, Section 3.1). F1 picks the next 16-byte aligned fetch
// block and sends it to instruction memory; F2 takes the returned block, marks
// the slots outside the valid range invalid, attaches the prediction fields
// and hands the 4 slots to the fetch buffer. Up to 2 requests are in flight,
// each tagged with an epoch bit that flips on every redirect.
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

endmodule
