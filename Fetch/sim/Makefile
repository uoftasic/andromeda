# cocotb + Icarus Verilog testbench Makefile for the Fetch unit
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
UNIT_DIR := $(abspath $(SIM_DIR)/..)
PROJ_DIR := $(abspath $(UNIT_DIR)/..)
RTL_DIR  := $(UNIT_DIR)/rtl
IP_DIR   := $(PROJ_DIR)/IP

# ---------------------------------------------------------------------------
# HDL sources
# Packages (*_pkg.sv) are listed first so they compile before their users.
# ---------------------------------------------------------------------------
RTL_PKGS := $(wildcard $(RTL_DIR)/*_pkg.sv)
RTL_SRCS := $(filter-out $(RTL_PKGS),$(wildcard $(RTL_DIR)/*.sv $(RTL_DIR)/*.v))

VERILOG_SOURCES      += $(RTL_PKGS) $(RTL_SRCS)
VERILOG_INCLUDE_DIRS += $(RTL_DIR)

# Pull in shared IP / other units' RTL here if this unit depends on them, e.g.
# VERILOG_SOURCES += $(IP_DIR)/<ip_name>/<file>.sv

# ---------------------------------------------------------------------------
# DUT and tests
# ---------------------------------------------------------------------------
COCOTB_TOPLEVEL     ?= fetch
COCOTB_TEST_MODULES ?= test_fetch

# Extra iverilog compile flags (cocotb already passes -g2012), e.g. -DSIM
COMPILE_ARGS +=

include $(shell cocotb-config --makefiles)/Makefile.sim
