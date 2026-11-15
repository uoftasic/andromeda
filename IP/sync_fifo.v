// byte addressable
`timescale 1ps/1ps

module sync_fifo #(
    parameter FIFO_WR_WIDTH = 32,
    parameter FIFO_WR_DEPTH = 8,
    parameter FIFO_RD_WIDTH = 8,
    parameter FIFO_RD_DEPTH = 32
)(
    input                   clk, 
    input                   rst_n, 
    input [FIFO_WR_WIDTH-1:0]  data_in, 
    input                   wr_en, 
    input                   rd_en, 
    output wire             full, 
    output wire             empty,
    output reg [FIFO_RD_WIDTH-1:0] data_out
);

localparam max_fifo_addr = $clog2(FIFO_WR_DEPTH * FIFO_WR_WIDTH / 8);
localparam num_bytes = FIFO_WR_DEPTH * FIFO_WR_WIDTH / 8;
localparam num_bytes_per_wr = FIFO_WR_WIDTH / 8;
localparam num_bytes_per_rd = FIFO_RD_WIDTH / 8;

reg [7:0] mem [0:num_bytes-1];

reg [max_fifo_addr-1:0] wr_ptr, rd_ptr;
reg [max_fifo_addr:0] count;

reg [max_fifo_addr-1:0] i;
always @(posedge clk or negedge rst_n) begin
	if (!rst_n) begin
		wr_ptr <= 0;
	end
	else if (wr_en && ~full) begin
        for(i = 0; i < num_bytes_per_wr; i = i+1)begin
            mem[wr_ptr + i] <= data_in[8*i +: 8];
        end
        wr_ptr <= wr_ptr + num_bytes_per_wr;
	end
end

reg [max_fifo_addr-1:0] j;
always @(posedge clk or negedge rst_n) begin
	if (!rst_n) begin
		rd_ptr <= 0;
	end
	else if (rd_en && ~empty) begin
        for(j = 0; j < num_bytes_per_rd; j = j+1)begin
            data_out[j*8 +: 8] <= mem[rd_ptr + j];
        end
		rd_ptr <= rd_ptr + num_bytes_per_rd;
	end
end

always @(posedge clk or negedge rst_n) begin
	if (!rst_n) begin
		count <= 0;
	end
	else begin
		if	( wr_en && !full) 
			count <= count + num_bytes_per_wr;
		
		if ( rd_en && !empty)
			count <= count - num_bytes_per_rd;
		
		if(wr_en && !full && rd_en && !empty)
			count <= count;
	end
end

assign full = (count > num_bytes - num_bytes_per_wr)? 1 : 0;
assign empty = (count < num_bytes_per_rd)? 1 : 0;

// linting
always@(*)begin
	if(|FIFO_RD_DEPTH);
end

endmodule
