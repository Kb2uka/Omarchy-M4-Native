#!/bin/bash
# SPDX-License-Identifier: MIT
# Build DisplayLink's evdi 1.15.1 for the J713 kernel: the evdi.ko module on
# the v0.1.0-alpha.1 kernel tree, or the aarch64 libevdi.so.1.15.1 library.
#
#   build.sh module  KERNEL_CONTROLLER_TARBALL EVDI_TARBALL OUT_DIR
#   build.sh library EVDI_TARBALL OUT_DIR
#
# module runs in m4-gravity-builder:20261008 (clang 21.1.8) with the job
# directory mounted at /job, the layout cpu/j713/build-module.sh uses.
# library runs in m4-gravity-mesa-arm-builder:20261008 (aarch64-linux-gnu-gcc
# and the arm64 libdrm headers). Both refuse inputs other than the pinned ones.
set -euo pipefail
evdi_sha=b120df303d14be09062157aab17a8598746c0caf2e076ff3388b4091186cf2d9
tree_sha=810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57

check() {
  test "$(sha256sum < "$1" | cut -d' ' -f1)" = "$2" ||
    { echo "build.sh: refusing $1: not the pinned input" >&2; exit 1; }
}

case ${1:-} in
module)
  tarball=$2 evdi=$3 out=$4
  tree=/job/kernel-air-bootstrap
  src=/job/evdi-1.15.1
  check "$tarball" "$tree_sha"
  check "$evdi" "$evdi_sha"
  test ! -e "$tree"
  test ! -e "$src"
  mkdir -p "$tree" "$src" "$out"
  tar xzf "$tarball" -C "$tree" --strip-components=1
  tar xzf "$evdi" -C "$src" --strip-components=1
  # The tree's Module.symvers lists only vmlinux exports. evdi also calls two
  # drm_ttm_helper.ko exports (the kernel package ships that module); naming
  # them here records the dependency in evdi.ko so modprobe loads it first.
  printf '0x00000000\t%s\tdrivers/gpu/drm/drm_ttm_helper\tEXPORT_SYMBOL\t\n' \
    drm_gem_ttm_vmap drm_gem_ttm_vunmap > /job/drm_ttm_helper.symvers
  make -C "$tree" ARCH=arm64 LLVM=1 LOCALVERSION= M="$src/module" \
    KBUILD_EXTRA_SYMBOLS=/job/drm_ttm_helper.symvers -j8 modules \
    > "$out/build-module.log" 2>&1
  cp "$src/module/evdi.ko" "$out/"
  modinfo -F vermagic "$out/evdi.ko"
  modinfo -F depends "$out/evdi.ko"
  (cd "$out" && sha256sum evdi.ko)
  ;;
library)
  evdi=$2 out=$3
  src=/job/evdi-1.15.1
  check "$evdi" "$evdi_sha"
  test ! -e "$src"
  mkdir -p "$src" "$out"
  tar xzf "$evdi" -C "$src" --strip-components=1
  # CFLAGS and LDFLAGS go in the environment: the library Makefile appends
  # them to its own include path, which a command-line value would replace.
  CFLAGS='-O2 -ffile-prefix-map=/job=.' LDFLAGS='-Wl,--build-id=sha1' \
    make -C "$src/library" CC=aarch64-linux-gnu-gcc \
    PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig \
    > "$out/build-library.log" 2>&1
  cp "$src/library/libevdi.so.1.15.1" "$out/"
  aarch64-linux-gnu-objdump -p "$out/libevdi.so.1.15.1" | grep -E 'SONAME|NEEDED'
  (cd "$out" && sha256sum libevdi.so.1.15.1)
  ;;
*)
  echo "usage: $0 module TREE_TARBALL EVDI_TARBALL OUT | library EVDI_TARBALL OUT" >&2
  exit 2
  ;;
esac
