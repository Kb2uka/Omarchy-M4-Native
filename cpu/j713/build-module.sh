#!/bin/bash
# SPDX-License-Identifier: MIT
# Build the J713 CPU idle module (#65) and P-cluster guard (#66) on the
# v0.1.0-alpha.1 kernel tree.
#
#   build-module.sh KERNEL_CONTROLLER_TARBALL SOURCE_DIR OUT_DIR
#
# Runs in m4-gravity-builder:20261008 (clang 21.1.8) with the job directory
# mounted at /job, the layout gpu/j713/build-test-module.sh uses. SOURCE_DIR is
# this directory (Kbuild, j713_cpuidle.c, j713_pguard.c and j713_pguard_policy.h).
set -euo pipefail
tarball=$1 src=$2 out=$3
tree=/job/kernel-air-bootstrap
mod=/job/j713-cpuidle

test "$(sha256sum < "$tarball" | cut -d' ' -f1)" = 810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57
test ! -e "$tree"
test ! -e "$mod"
mkdir -p "$tree" "$mod" "$out"
tar xzf "$tarball" -C "$tree" --strip-components=1
cp "$src"/Kbuild "$src"/j713_cpuidle.c "$src"/j713_pguard.c "$src"/j713_pguard_policy.h "$mod"/

make -C "$tree" ARCH=arm64 LLVM=1 LOCALVERSION= M="$mod" -j8 modules > "$out/build.log" 2>&1
cp "$mod"/j713_cpuidle.ko "$mod"/j713_pguard.ko "$out"/
modinfo -F vermagic "$out/j713_cpuidle.ko" "$out/j713_pguard.ko"
(cd "$out" && sha256sum j713_cpuidle.ko j713_pguard.ko)
