#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Build the J713 m1n1 stage 2 that shows the notch rows (issue #89).
#
#   build-notch-loader.sh COMPANION_SOURCE_DIR OUT_DIR
#
# Runs in m4-gravity-builder:20261008 (clang 21.1.8, rustc 1.93.1 with rust-src)
# with the job directory mounted at /job, where alpha.1's loaders were built.
# COMPANION_SOURCE_DIR is the source/ directory of the alpha.1 companion. Stage 2
# is first rebuilt unpatched and must match alpha.1's shipped hash, so the notch
# loader differs from alpha.1 only by m1n1-show-notch.patch and its version tag.
set -euo pipefail
if [ $# -ne 2 ]; then
    echo "usage: $0 COMPANION_SOURCE_DIR OUT_DIR" >&2
    exit 2
fi
here=$(dirname "$(realpath "$0")")
src=$1 out=$2 patch=$here/m1n1-show-notch.patch
source_sha=c394517941a5700b227718bb02e3df3dd43c9ad5a8e53c9447a55d3c36846c44
stock_sha=3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4
tag=air-c9ef56f-gpu-stage2-20261008
export PATH=/usr/lib/llvm-21/bin:$PATH
mkdir -p "$out"

# alpha.1 was built at this path. Refuse to reuse it, and remove it on any exit.
tree=/job/bootloader-air-knownbase-stage2
if [ -e "$tree" ]; then
    echo "$tree exists; remove it first" >&2
    exit 1
fi
trap 'rm -rf "$tree"' EXIT

build() {
    local kind=$1 version=$2
    mkdir "$tree"
    tar xzf "$src/m1n1-stage2-source.tar.gz" -C "$tree" --strip-components=1
    # The archive keeps the generated headers; alpha.1 was built without them.
    rm -rf "$tree/build"
    if [ "$kind" = notch ]; then
        (cd "$tree" && git apply --verbose "$patch")
    fi
    (
        cd "$tree"
        export M1N1_VERSION_TAG=$version
        # alpha.1 ran make twice; do the same.
        for pass in 1 2; do
            make BUILDSTD=1 USE_CLANG=1 TOOLCHAIN=/usr/lib/llvm-21/bin/ LLDDIR=/usr/lib/llvm-21/bin/ \
                RELEASE=1 CHAINLOADING=0 NO_DISPLAY=1 -j8
        done
        cat build/build_cfg.h build/build_tag.h
    ) > "$out/stage2-$kind-build.log" 2>&1
    cp "$tree/build/m1n1.bin" "$out/stage2-$kind-m1n1.bin"
    rm -rf "$tree"
}

test "$(sha256sum < "$src/m1n1-stage2-source.tar.gz" | cut -d' ' -f1)" = "$source_sha"
build stock "$tag"
test "$(sha256sum < "$out/stage2-stock-m1n1.bin" | cut -d' ' -f1)" = "$stock_sha"
build notch "$tag-notch1"
(cd "$out" && sha256sum ./*.bin)
