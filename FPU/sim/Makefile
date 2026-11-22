# cocotb testbench for fpu_top (the FPnew wrapper for Andromeda port P2)
#
# Usage (cocotb lives in a venv: source ~/cocotb-venv/bin/activate first):
#   make                                run all cocotb tests
#   make WAVES=1                        also dump sim_build/fpu_top.fst, or .vcd if
#                                       liblz4-dev is missing (open with gtkwave)
#   make COCOTB_TEST_FILTER=<regex>     run only tests whose name matches
#   make FPU_RANDOM_PER_CASE=1000       more random operands per (op, rm) pair
#   make clean                          remove sim_build/ and results.xml
#
# Needs Verilator >= 5.036 (cocotb 2.0's minimum). Icarus cannot be used:
# FPnew relies on type parameters and packed-struct features it does not parse.
#
# Layout (relative to this Makefile):
#   ../rtl/fpu_top.sv                FPnew wrapper (DUT)
#   ../rtl/cvfpu/                    copy of CVFPU / FPnew
#   ../rtl/cvfpu/src/common_cells/   common_cells (bundled in the copy)
#   ../../pkg/andromeda_pkg.sv       Andromeda package

SIM           ?= verilator
TOPLEVEL_LANG ?= verilog
WAVES         = 1
# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
TB_DIR           := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
FPU_RTL_DIR      := $(abspath $(TB_DIR)/../rtl)
CVFPU_DIR        ?= $(FPU_RTL_DIR)/cvfpu
ANDROMEDA_DIR    ?= $(abspath $(TB_DIR)/../..)
COMMON_CELLS_DIR ?= $(CVFPU_DIR)/src/common_cells

ifeq ($(wildcard $(CVFPU_DIR)/src/fpnew_top.sv),)
$(error cvfpu not found in $(CVFPU_DIR). Set CVFPU_DIR=<path to cvfpu>)
endif
ifeq ($(wildcard $(COMMON_CELLS_DIR)/src/rr_arb_tree.sv),)
$(error common_cells not found in $(COMMON_CELLS_DIR). Set COMMON_CELLS_DIR=<path>)
endif
ifeq ($(wildcard $(ANDROMEDA_DIR)/pkg/andromeda_pkg.sv),)
$(error andromeda_pkg.sv not found. Set ANDROMEDA_DIR=<path to the andromeda repo>)
endif

# ---------------------------------------------------------------------------
# HDL sources, packages first (order follows cvfpu/Bender.yml)
# ---------------------------------------------------------------------------
E906 := $(CVFPU_DIR)/vendor/opene906/E906_RTL_FACTORY/gen_rtl
C910 := $(CVFPU_DIR)/vendor/openc910/C910_RTL_FACTORY/gen_rtl

VERILOG_SOURCES += \
  $(ANDROMEDA_DIR)/pkg/andromeda_pkg.sv \
  $(COMMON_CELLS_DIR)/src/cf_math_pkg.sv \
  $(COMMON_CELLS_DIR)/src/lzc.sv \
  $(COMMON_CELLS_DIR)/src/rr_arb_tree.sv \
  $(CVFPU_DIR)/src/fpnew_pkg.sv \
  $(CVFPU_DIR)/src/fpnew_cast_multi.sv \
  $(CVFPU_DIR)/src/fpnew_classifier.sv \
  $(E906)/clk/rtl/gated_clk_cell.v \
  $(E906)/fdsu/rtl/pa_fdsu_ctrl.v \
  $(E906)/fdsu/rtl/pa_fdsu_ff1.v \
  $(E906)/fdsu/rtl/pa_fdsu_pack_single.v \
  $(E906)/fdsu/rtl/pa_fdsu_prepare.v \
  $(E906)/fdsu/rtl/pa_fdsu_round_single.v \
  $(E906)/fdsu/rtl/pa_fdsu_special.v \
  $(E906)/fdsu/rtl/pa_fdsu_srt_single.v \
  $(E906)/fdsu/rtl/pa_fdsu_top.v \
  $(E906)/fpu/rtl/pa_fpu_dp.v \
  $(E906)/fpu/rtl/pa_fpu_frbus.v \
  $(E906)/fpu/rtl/pa_fpu_src_type.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_ctrl.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_double.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_ff1.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_pack.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_prepare.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_round.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_scalar_dp.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_srt_radix16_bound_table.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_srt_radix16_with_sqrt.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_srt.v \
  $(C910)/vfdsu/rtl/ct_vfdsu_top.v \
  $(CVFPU_DIR)/src/fpnew_divsqrt_th_32.sv \
  $(CVFPU_DIR)/src/fpnew_divsqrt_th_64_multi.sv \
  $(CVFPU_DIR)/vendor/cvw/fma/fmalza.sv \
  $(CVFPU_DIR)/src/fpnew_fma.sv \
  $(CVFPU_DIR)/src/fpnew_fma_multi.sv \
  $(CVFPU_DIR)/src/fpnew_noncomp.sv \
  $(CVFPU_DIR)/src/fpnew_opgroup_block.sv \
  $(CVFPU_DIR)/src/fpnew_opgroup_fmt_slice.sv \
  $(CVFPU_DIR)/src/fpnew_opgroup_multifmt_slice.sv \
  $(CVFPU_DIR)/src/fpnew_rounding.sv \
  $(CVFPU_DIR)/src/fpnew_top.sv \
  $(FPU_RTL_DIR)/fpu_top.sv

VERILOG_INCLUDE_DIRS += $(COMMON_CELLS_DIR)/include

# Third-party RTL is noisy under Verilator's lint; keep warnings non-fatal.
COMPILE_ARGS += -Wno-fatal -Wno-lint -Wno-style -Wno-UNOPTFLAT -Wno-MULTIDRIVEN -Wno-TIMESCALEMOD

# ---------------------------------------------------------------------------
# DUT and tests
# ---------------------------------------------------------------------------
COCOTB_TOPLEVEL     ?= fpu_top
COCOTB_TEST_MODULES ?= test_fpu_top
export FPU_RANDOM_PER_CASE

# ---------------------------------------------------------------------------
# Waveforms. cocotb's Verilator makefile ignores WAVES, so map it here.
# FST needs the LZ4 headers (sudo apt install liblz4-dev); without them the
# dump falls back to VCD. Override with WAVES_FORMAT=fst or WAVES_FORMAT=vcd.
# The stamp file changes whenever these settings do, which forces a Verilator
# rebuild (cocotb otherwise rebuilds only when an HDL source changes).
# ---------------------------------------------------------------------------
WAVES_FORMAT ?= $(if $(wildcard /usr/include/lz4.h),fst,vcd)
ifeq ($(WAVES),1)
  ifeq ($(WAVES_FORMAT),fst)
    COMPILE_ARGS += --trace-fst --trace-structs
  else
    COMPILE_ARGS += --trace-vcd --trace-structs
  endif
  SIM_ARGS += --trace --trace-file sim_build/fpu_top.$(WAVES_FORMAT)
  $(info Dumping waveforms to sim_build/fpu_top.$(WAVES_FORMAT))
endif
WAVES_STAMP := sim_build/waves_setting
WAVES_CFG   := $(WAVES) $(WAVES_FORMAT)
$(shell mkdir -p sim_build; echo '$(WAVES_CFG)' | cmp -s - $(WAVES_STAMP) || echo '$(WAVES_CFG)' > $(WAVES_STAMP))
CUSTOM_COMPILE_DEPS += $(WAVES_STAMP)

include $(shell cocotb-config --makefiles)/Makefile.sim
