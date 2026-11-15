# cocotb + Icarus Verilog testbench Makefile for the top-level core
#
# Usage:
#   make                                run all cocotb tests
#   make WAVES=1                        also dump an FST waveform into sim_build/
#   make COCOTB_TEST_FILTER=<regex>     run only tests whose name matches
#   make clean                          remove sim_build/ and results.xml

SIM           ?= icarus
TOPLEVEL_LANG ?= verilog

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SIM_DIR  := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
PROJ_DIR := $(abspath $(SIM_DIR)/../..)
IP_DIR   := $(PROJ_DIR)/IP

UNITS    := ALU FPU LSU Fetch Dispatch Writeback top
RTL_DIRS := $(foreach u,$(UNITS),$(PROJ_DIR)/$(u)/rtl)

# ---------------------------------------------------------------------------
# HDL sources: every unit's rtl/ plus IP/ (top level and one directory deep)
# Packages (*_pkg.sv) are listed first so they compile before their users.
# ---------------------------------------------------------------------------
SRC_GLOBS := $(foreach d,$(RTL_DIRS) $(IP_DIR) $(IP_DIR)/*,$(d)/*.sv $(d)/*.v)

ALL_SRCS := $(wildcard $(SRC_GLOBS))
ALL_PKGS := $(filter %_pkg.sv,$(ALL_SRCS))

VERILOG_SOURCES      += $(ALL_PKGS) $(filter-out $(ALL_PKGS),$(ALL_SRCS))
VERILOG_INCLUDE_DIRS += $(RTL_DIRS)

# ---------------------------------------------------------------------------
# DUT and tests
# ---------------------------------------------------------------------------
COCOTB_TOPLEVEL     ?= top
COCOTB_TEST_MODULES ?= test_top

# Extra iverilog compile flags (cocotb already passes -g2012), e.g. -DSIM
COMPILE_ARGS +=

include $(shell cocotb-config --makefiles)/Makefile.sim
