// Single-instruction decoder (Spec 03, Section 3.2). Combinational; one
// instance per decode slot. Fills only the decode fields of the uop and flags
// anything outside the RV32I, F and Zicsr whitelist as illegal.
module decoder
  import andromeda_pkg::*;
(
  input  fetch_slot_t      slot,        // Instruction, PC and prediction

  output uop_t             uop,         // Decoded fields

  output logic             is_jal,      // Used by decode_stage for the redirect
  output logic [XLEN-1:0]  jal_target   // PC + J-type immediate
);

endmodule
