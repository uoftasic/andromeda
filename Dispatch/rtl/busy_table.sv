// Busy table, one per register file (Spec 04, Section 3.4). One bit per
// physical register: set when the register is allocated at rename, cleared
// when its value is written back.
module busy_table
  import andromeda_pkg::*;
(
  input  logic                                    clk,
  input  logic                                    rst_n,

  input  logic [DISPATCH_W-1:0]                   set_valid,  // Mark newly allocated registers busy
  input  logic [DISPATCH_W-1:0][PREG_IDX_W-1:0]   set_idx,

  input  logic [NUM_PORTS-1:0]                    clr_valid,  // From wakeup buses
  input  logic [NUM_PORTS-1:0][PREG_IDX_W-1:0]    clr_idx,

  input  logic [3*DISPATCH_W-1:0][PREG_IDX_W-1:0] rd_idx,     // Source lookups
  output logic [3*DISPATCH_W-1:0]                 rd_busy,    // 1 = value not yet produced

  input  logic                                    flush       // Clear all busy bits
);

  

endmodule
