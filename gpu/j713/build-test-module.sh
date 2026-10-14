#!/bin/bash
# SPDX-License-Identifier: MIT
# Build the Air GPU test module inside the alpha.1 builder container.
#
#   build-test-module.sh KERNEL_CONTROLLER_TARBALL SOURCE_PACKET_DIR G16_RS OUT_DIR
#
# Runs in m4-gravity-builder:20261008 (clang 21.1.8, rustc 1.93.1, bindgen
# 0.72.1) with the job directory mounted at /job, the layout the shipped module
# was built in. It first rebuilds alpha.1 and requires the shipped hash, so the
# test module differs from alpha.1 only by G16_RS.
set -euo pipefail
tarball=$1 packet=$2 g16=$3 out=$4
stock=80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578
tree=/job/kernel-air-bootstrap
asahi=drivers/gpu/drm/asahi

test "$(sha256sum < "$tarball" | cut -d' ' -f1)" = 810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57
(cd "$packet" && sha256sum --check --strict SHA256SUMS)
test ! -e "$tree"
mkdir -p "$tree" "$out"
tar xzf "$tarball" -C "$tree" --strip-components=1
cd "$tree"
# The packet carries the four patched files whole; SHA256SUMS checked them.
cp "$packet"/$asahi/{asahi_m4.rs,g16.rs,g16_platform.rs,g16_power.rs} $asahi/

build() {
    make ARCH=arm64 LLVM=1 LOCALVERSION= M=$asahi -j8 modules > "$out/$1-build.log" 2>&1
    cp $asahi/asahi_m4.ko "$out/$1-asahi_m4.ko"
}
build alpha1
test "$(sha256sum < "$out/alpha1-asahi_m4.ko" | cut -d' ' -f1)" = "$stock"
cp "$g16" $asahi/g16.rs
build test
modinfo -F vermagic "$out/test-asahi_m4.ko"
(cd "$out" && sha256sum alpha1-asahi_m4.ko test-asahi_m4.ko)
