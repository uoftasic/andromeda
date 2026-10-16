// Simulation-only behavioral stand-ins for the buffer-RAM IP referenced by
// rtl/accel_top.sv, rtl/input_buffer_32_bank.v, and rtl/output_buffer_32_bank.v
// (weight_buffer, input_buffer, output_buffer).
//
// All three are MIXED-WIDTH simple dual-port RAMs: the Avalon-facing port is
// AVS_DATA_WIDTH (128) wide so a host access moves a full word and the byte
// address advances by 16, while the core-facing port stays byte-wide because
// weight_loader/input_dispatch read one byte per cycle and output_loader
// writes one byte per cycle.
//
//   weight_buffer : wide WRITE  (Avalon)      / narrow READ  (weight_loader)
//   input_buffer  : wide WRITE  (Avalon)      / narrow READ  (input_dispatch)
//   output_buffer : narrow WRITE (output_loader) / wide READ (Avalon)
//
// Byte ordering matches Altera's mixed-width altsyncram convention (and
// Avalon byte lanes): narrow element i of a wide word sits at bit
// [i*NARROW_WIDTH +: NARROW_WIDTH], so narrow address 16n+0 is the wide
// word's least significant byte.
//
// These are NOT meant for synthesis. Swap this file out of the compile
// fileset for the real generated IP (same module names/ports) once it
// exists - the generated On-Chip Memory must be configured with matching
// mixed port widths.
`timescale 1ps/1ps

// Wide write port, narrow read port.
module mixed_ram_wr_wide #(
    parameter NARROW_WIDTH = 8,
    parameter WIDE_WIDTH   = 32,
    parameter NARROW_DEPTH = 256
)(
    input                                       clock,
    input      [WIDE_WIDTH-1:0]                 data,
    input      [$clog2(NARROW_DEPTH)-1:0]       rdaddress,
    input                                       rden,
    input      [$clog2(NARROW_DEPTH*NARROW_WIDTH/WIDE_WIDTH)-1:0] wraddress,
    input                                       wren,
    output reg [NARROW_WIDTH-1:0]               q
);
    localparam RATIO = WIDE_WIDTH / NARROW_WIDTH;

    reg [NARROW_WIDTH-1:0] mem [0:NARROW_DEPTH-1];

    integer i;
    always @(posedge clock) begin
        if (wren)
            for (i = 0; i < RATIO; i = i + 1)
                mem[wraddress*RATIO + i] <= data[i*NARROW_WIDTH +: NARROW_WIDTH];
        if (rden)
            q <= mem[rdaddress];
    end
endmodule

// Narrow write port, wide read port.
module mixed_ram_rd_wide #(
    parameter NARROW_WIDTH = 8,
    parameter WIDE_WIDTH   = 32,
    parameter NARROW_DEPTH = 128
)(
    input                                       clock,
    input      [NARROW_WIDTH-1:0]               data,
    input      [$clog2(NARROW_DEPTH*NARROW_WIDTH/WIDE_WIDTH)-1:0] rdaddress,
    input                                       rden,
    input      [$clog2(NARROW_DEPTH)-1:0]       wraddress,
    input                                       wren,
    output reg [WIDE_WIDTH-1:0]                 q
);
    localparam RATIO = WIDE_WIDTH / NARROW_WIDTH;

    reg [NARROW_WIDTH-1:0] mem [0:NARROW_DEPTH-1];

    integer i;
    always @(posedge clock) begin
        if (wren)
            mem[wraddress] <= data;
        if (rden)
            for (i = 0; i < RATIO; i = i + 1)
                q[i*NARROW_WIDTH +: NARROW_WIDTH] <= mem[rdaddress*RATIO + i];
    end
endmodule
