# SPDX-License-Identifier: GPL-2.0-only
# Private J713 ISP firmware-view experiment.
# Default target compiles the kernel module only. It does not run dtc,
# the userspace mock, or any test.
#
# j713_isp_fwview_main.c is present in this tree. Kernel link:
#   j713_isp_fwview.o = j713_isp_fwview_main.o +
#                       j713_isp_probe_alg.o +
#                       j713_isp_fwview_life.o
#   -> j713_isp_fwview.ko
#
# The userspace mock includes alg.c and life.c itself. Link neither again.
# Homebase dtc, overridable: /job/kernel-controller/scripts/dtc/dtc
# `make dtbo` is explicit and is not part of the default module build.
# dtc is invoked without -@. -@ exports DTS labels into /__symbols__ and
# /__fixups__, and of_overlay_fdt_apply then requires a live root /__symbols__.
# This Air tree has none, so the overlay is rejected. /plugin/ still emits
# local phandle fixups. Labels stay in the DTS; they are not exported.
# `make dtbo-contract-test` depends on dtbo and is not part of all or modules.
# dtbo and that recipe use the absolute DTC_PATH. A slash-free DTC= name is
# the file in make's working directory, not a PATH lookup. The mutant test
# never falls back to a system dtc.
# The checked-in j713_isp_fwview_dtbo.inc is an #error placeholder.
# `make embed-test` is explicit and remote-only. It is not part of all,
# modules, or dtbo.

ifneq ($(KERNELRELEASE),)
obj-m += j713_isp_fwview.o
j713_isp_fwview-y := j713_isp_fwview_main.o j713_isp_probe_alg.o j713_isp_fwview_life.o
ccflags-y += -I$(src)
else
CC ?= cc
PYTHON ?= python3
DTC ?= /job/kernel-controller/scripts/dtc/dtc
DTC_PATH = $(abspath $(DTC))
MOCK_CFLAGS ?= -O2 -Wall -Wextra -Werror -std=c11

.PHONY: all modules mock launcher-test layout-test embed-test dtbo dtbo-contract-test clean

all: modules

modules:
	test -n "$(KDIR)"
	test -f "$(CURDIR)/j713_isp_fwview_main.c"
	$(MAKE) -C "$(KDIR)" M="$(CURDIR)" ARCH=arm64 LLVM=1 modules

mock: j713_isp_probe_mock

j713_isp_probe_mock: j713_isp_probe_mock.c j713_isp_probe_alg.c j713_isp_probe_alg.h \
		j713_isp_fwview_life.c j713_isp_fwview_life.h \
		j713_isp_fwview_life_test.c j713_isp_fwview_layout.h
	$(CC) $(MOCK_CFLAGS) -I. -o $@ j713_isp_probe_mock.c

launcher-test:
	$(PYTHON) "$(CURDIR)/test_run_probe.py"
	$(PYTHON) "$(CURDIR)/test_real_launcher_io.py"

layout-test: j713_isp_fwview_layout_test
	./j713_isp_fwview_layout_test

embed-test:
	$(PYTHON) "$(CURDIR)/test_embed_dtbo.py"

j713_isp_fwview_layout_test: j713_isp_fwview_layout_test.c \
		j713_isp_fwview_layout.h j713_isp_probe_alg.h
	$(CC) $(MOCK_CFLAGS) -I. -o $@ j713_isp_fwview_layout_test.c

# No -@ on the dtc line below. Exported labels need a live /__symbols__.
# /plugin/ still supplies local phandle fixups for iommus.
dtbo:
	@test -f "$(DTC_PATH)" && test -x "$(DTC_PATH)" || { echo "DTC=$(DTC) resolves to $(DTC_PATH), which is not an executable file (no PATH lookup)" >&2; exit 1; }
	rm -f "$(CURDIR)/j713-isp-fwview-overlay.dtbo"
	"$(DTC_PATH)" -I dts -O dtb -o "$(CURDIR)/j713-isp-fwview-overlay.dtbo" \
		"$(CURDIR)/j713-isp-fwview-overlay.dts"
	test -s "$(CURDIR)/j713-isp-fwview-overlay.dtbo"
	$(PYTHON) "$(CURDIR)/embed_dtbo.py" \
		"$(CURDIR)/j713-isp-fwview-overlay.dtbo" \
		"$(CURDIR)/j713_isp_fwview_dtbo.inc"

dtbo-contract-test: dtbo
	$(PYTHON) "$(CURDIR)/test_dtbo_contract.py" \
		"$(CURDIR)/j713-isp-fwview-overlay.dtbo" \
		"$(CURDIR)/j713_isp_fwview_dtbo.inc"
	cd "$(CURDIR)" && DTC="$(DTC_PATH)" $(PYTHON) -m unittest -v test_dtbo_contract_mutants

clean:
	rm -f j713_isp_probe_mock j713_isp_fwview_layout_test
	rm -f *.o *.ko *.mod *.mod.c .*.cmd Module.symvers modules.order
	rm -f j713-isp-fwview-overlay.dtbo
	if [ -n "$(KDIR)" ]; then $(MAKE) -C "$(KDIR)" M="$(CURDIR)" clean; fi
endif
