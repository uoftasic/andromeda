# cocotb + Icarus Verilog testbench Makefile for the Fetch unit
#
# Usage:
#   make                                run all cocotb tests
#   make WAVES=1                        also dump an FST waveform into sim_build/
#   make COCOTB_TEST_FILTER=<regex>     run only tests whose name matches
#   make DEBUG=1                        print the RTL debug trace and the slots
#                                       the tests expect (not `make debug`, which
#                                       is cocotb's gdb target)
#   make clean                          remove sim_build/ and results.xml
#
#   make COCOTB_TOPLEVEL=ifu COCOTB_TEST_MODULES=test_ifu
#                                       test the ifu on its own (make clean first
#                                       when switching between top levels)

SIM           ?= icarus
TOPLEVEL_LANG ?= verilog
WAVES = 1

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SIM_DIR  := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UNIT_DIR := $(abspath $(SIM_DIR)/..)
PROJ_DIR := $(abspath $(UNIT_DIR)/..)
RTL_DIR  := $(UNIT_DIR)/rtl
PKG_DIR  := $(PROJ_DIR)/pkg
IP_DIR   := $(PROJ_DIR)/IP

# ---------------------------------------------------------------------------
# HDL sources
# Packages (*_pkg.sv) are listed first so they compile before their users.
# ---------------------------------------------------------------------------
RTL_PKGS := $(wildcard $(PKG_DIR)/*_pkg.sv $(RTL_DIR)/*_pkg.sv)
RTL_SRCS := $(filter-out $(RTL_PKGS),$(wildcard $(RTL_DIR)/*.sv $(RTL_DIR)/*.v))

VERILOG_SOURCES      += $(RTL_PKGS) $(RTL_SRCS)
VERILOG_INCLUDE_DIRS += $(RTL_DIR)

# Pull in shared IP / other units' RTL here if this unit depends on them, e.g.
# VERILOG_SOURCES += $(IP_DIR)/<ip_name>/<file>.sv

# ---------------------------------------------------------------------------
# DUT and tests
# ---------------------------------------------------------------------------
COCOTB_TOPLEVEL     ?= ifu
COCOTB_TEST_MODULES ?= test_ifu

# Extra iverilog compile flags (cocotb already passes -g2012), e.g. -DSIM
COMPILE_ARGS +=

# DEBUG=1 compiles in the RTL debug prints (`ifdef ANDROMEDA_DEBUG) and has the
# tests print every slot they expect, so the two can be compared line by line.
DEBUG     ?= 0
SIM_BUILD ?= sim_build
ifeq ($(DEBUG),1)
    COMPILE_ARGS += -DANDROMEDA_DEBUG
    export ANDROMEDA_DEBUG := 1
endif
# Recompile whenever DEBUG changes (the stamp rule is below the include so it
# doesn't become the default goal).
CUSTOM_COMPILE_DEPS += $(SIM_BUILD)/debug_$(DEBUG)

include $(shell cocotb-config --makefiles)/Makefile.sim

$(SIM_BUILD)/debug_$(DEBUG): | $(SIM_BUILD)
	@rm -f $(SIM_BUILD)/debug_*
	@touch $@
