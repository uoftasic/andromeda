// Decode stage (Spec 03, Section 3.1). Decodes a group of 4 fetch slots into
// uops with one decoder per slot, holds the group while rename stalls, and
// redirects fetch early on the first unpredicted JAL in the group.
module decode_stage
  import andromeda_pkg::*;
(
  input  logic                          clk,
  input  logic                          rst_n,

  input  logic                          flush,               // be_flush or fe_flush: drop the held group

  // Group from fetch_buffer
  input  logic [DISPATCH_W-1:0]         in_valid,
  input  fetch_slot_t [DISPATCH_W-1:0]  in_slot,
  output logic                          in_ready,            // Group accepted

  // Decoded group to rename
  output logic [DISPATCH_W-1:0]         out_valid,           // Valid per uop
  output uop_t [DISPATCH_W-1:0]         out_uop,             // Decode fields filled
  input  logic                          out_ready,           // Rename took the group

  // Early redirect for an unpredicted JAL; later slots in the group are invalidated
  output logic                          dec_redirect_valid,
  output logic [XLEN-1:0]               dec_redirect_pc      // The JAL's target
);

endmodule
