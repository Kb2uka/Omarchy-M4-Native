# SPDX-License-Identifier: GPL-2.0-only
# Private J713 ISP register-inspection variant.
# Identity power sequence plus optional read-only COPROC/GPIO/DART reads
# and, only for --inspect, one 8-byte RVBAR readq.
# This RVBAR extension is not built or run by the coding dispatch.
# Orchestrator builds the kernel module on Homebase against
# 7.1.9-m4-gpu-backport-20261008 (m4-gravity-builder:20261008,
# tarball sha256 810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57):
#   make KDIR="$tree" modules
# Userspace mock is remote-only. Do not build or test on XPS.

ifneq ($(KERNELRELEASE),)
obj-m += j713_isp_probe.o
ccflags-y += -I$(src)
$(obj)/j713_isp_probe.o: $(src)/j713_isp_probe_alg.c $(src)/j713_isp_probe_alg.h
else
CC ?= cc
MOCK_CFLAGS ?= -O2 -Wall -Wextra -Werror -std=c11

.PHONY: mock modules clean launcher-test

mock: j713_isp_probe_mock

j713_isp_probe_mock: j713_isp_probe_mock.c j713_isp_probe_alg.c j713_isp_probe_alg.h
	$(CC) $(MOCK_CFLAGS) -I. -o $@ j713_isp_probe_mock.c j713_isp_probe_alg.c

launcher-test:
	python3 $(CURDIR)/test_run_probe.py

modules:
	test -n "$(KDIR)"
	$(MAKE) -C $(KDIR) M=$(CURDIR) ARCH=arm64 LLVM=1 modules

clean:
	rm -f j713_isp_probe_mock
	rm -f *.o *.ko *.mod *.mod.c .*.cmd Module.symvers modules.order
	if [ -n "$(KDIR)" ]; then $(MAKE) -C $(KDIR) M=$(CURDIR) clean; fi
endif
