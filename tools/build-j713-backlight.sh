#!/bin/bash
# SPDX-License-Identifier: MIT
# Build inside the J713 clang 21.1.8 builder against a prepared kernel tree.
# No installation, kernel-private DRM headers, or extra sound exports.
# Usage: build-j713-backlight.sh MODULE_SOURCE KERNEL_BUILD
set -euo pipefail
if [ "$#" != 2 ]; then
    echo "usage: $0 MODULE_SOURCE KERNEL_BUILD" >&2
    exit 2
fi
source_tree=$(realpath "$1")
kernel_tree=$(realpath "$2")
llvm=/usr/lib/llvm-21/bin
[ -f "$source_tree/Kbuild" ]
[ -f "$source_tree/client.c" ]
[ -f "$source_tree/afk.c" ]
[ -f "$kernel_tree/Module.symvers" ]
[ -f "$kernel_tree/include/generated/utsrelease.h" ]
"$llvm/clang" --version | grep -Fxq 'Ubuntu clang version 21.1.8 (6ubuntu1)'
grep -Fxq 'CONFIG_CC_VERSION_TEXT="Ubuntu clang version 21.1.8 (6ubuntu1)"' "$kernel_tree/.config"
export PATH="$llvm:$PATH"
make -C "$kernel_tree" ARCH=arm64 LLVM="$llvm/" LOCALVERSION= \
    KCFLAGS=-Werror M="$source_tree" -j2 modules
modinfo -F vermagic "$source_tree/j713_dcp_backlight.ko"
sha256sum "$source_tree/j713_dcp_backlight.ko"
