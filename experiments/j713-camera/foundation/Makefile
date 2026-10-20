# SPDX-License-Identifier: GPL-2.0-only
# Kernel module: exact target 7.1.9, ARCH=arm64 LLVM=1.
# Layout tests run on the designated remote validation host.

ifneq ($(KERNELRELEASE),)
apple-isp-y := isp-cam.o isp-cmd.o isp-drv.o isp-fw.o isp-iommu.o isp-ipc.o isp-v4l2.o
obj-$(CONFIG_VIDEO_APPLE_ISP) += apple-isp.o
obj-$(CONFIG_VIDEO_APPLE_ISP_IPC_LAYOUT_KUNIT_TEST) += isp-ipc-layout-test.o
else
CC ?= cc
LAYOUT_CFLAGS ?= -O2 -Wall -Wextra -Werror -std=c11

.PHONY: all layout-test clean

all: isp-m4-layout-test

layout-test: isp-m4-layout-test
	./isp-m4-layout-test

isp-m4-layout-test: isp-m4-layout-test.c isp-m4-layout.h
	$(CC) $(LAYOUT_CFLAGS) -I. -o $@ isp-m4-layout-test.c

clean:
	rm -f isp-m4-layout-test
endif
