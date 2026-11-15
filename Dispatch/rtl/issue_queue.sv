// Generic issue queue (Spec 05, Section 3.2). Holds uops until every source
// is ready, then sends the oldest ready uop to each port. Instances:
//   integer  DEPTH = IQ_INT (16), NUM_ISSUE = 2: iss[0] = P0, iss[1] = P1
//   FP       DEPTH = IQ_FP  (8),  NUM_ISSUE = 1: P2
//   memory   DEPTH = IQ_MEM (8),  NUM_ISSUE = 1: P3
module issue_queue
  import andromeda_pkg::*;
#(
  parameter int unsigned DEPTH     = IQ_INT,  // Entries
  parameter int unsigned NUM_ISSUE = 2        // Ports this queue feeds
) (
  input  logic                          clk,
  input  logic                          rst_n,

  input  logic                          flush,        // Empties the queue

  // Enqueue from dispatch
  input  logic [DISPATCH_W-1:0]         enq_valid,
  input  uop_t [DISPATCH_W-1:0]         enq_uop,
  output logic [$clog2(DEPTH):0]        free_cnt,     // Free entries

  input  wakeup_t [NUM_PORTS-1:0]       wakeup,       // Tags produced this cycle
  input  rob_id_t                       rob_head_id,  // For serializing uops and age comparison

  // Issue to each port's register read stage
  output logic [NUM_ISSUE-1:0]          iss_valid,    // Selected uop per port
  output uop_t [NUM_ISSUE-1:0]          iss_uop,
  input  logic [NUM_ISSUE-1:0]          iss_ready     // The port's unit can accept a uop this cycle
);

endmodule
