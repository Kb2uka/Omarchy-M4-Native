#!/bin/bash
# SPDX-License-Identifier: MIT
# Reproduce stock first; build separate hidden/shown J715 handoff payloads.
# Requires the pinned native arm64 builder; preserves the working SEP handoff.
set -euo pipefail
if [ "$#" != 3 ]; then
    echo "usage: $0 COMPANION_SOURCE NEW_OUTPUT QUALIFIED_DISPLAY_PROFILE" >&2
    exit 2
fi
: "${TMPDIR:?}" "${NODE_COMPILE_CACHE:?}" "${BUN_RUNTIME_TRANSPILER_CACHE_PATH:?}"
export TMPDIR NODE_COMPILE_CACHE BUN_RUNTIME_TRANSPILER_CACHE_PATH PYTHONDONTWRITEBYTECODE=1 GIT_MASTER=1
here=$(dirname "$(realpath "$0")")
tools=$here/../../tools
src=$(realpath "$1") out=$(realpath -m "$2") profile=$(realpath "$3")
python3 -B "$tools/j715_display_profile.py" "$profile"
: "${J715_BUILD_IMAGE_ID:?immutable builder image digest required}"
[ ! -e "$out" ] && [ ! -L "$out" ]
[ "$(uname -m)" = aarch64 ]
source_sha=c394517941a5700b227718bb02e3df3dd43c9ad5a8e53c9447a55d3c36846c44
stock_sha=7431196da2fd48681b262f6f605984f82d46ce66bf7ba56cd64118358684a2c7
[ "$(sha256sum < "$src/m1n1-stage2-source.tar.gz" | cut -d' ' -f1)" = "$source_sha" ]
export PATH=/usr/lib/llvm-21/bin:$PATH CARGO_NET_OFFLINE=true
clang --version | grep -Fxq 'Ubuntu clang version 21.1.8 (6ubuntu1)'
rustc --version | grep -Eq '^rustc 1\.93\.1 '
scratch=$(mktemp -d "$TMPDIR/j715-loader.XXXXXXXX")
tree=/job/bootloader-air-knownbase-stage2
# The stock pin depends on this absolute build path. Never reuse someone else's.
[ ! -e "$tree" ] && [ ! -L "$tree" ]
mkdir "$tree"
trap 'rm -rf -- "$tree" "$scratch"' EXIT
python3 -B "$tools/j715_display_profile.py" "$profile" --stage "$scratch/verified"
mkdir "$scratch/output"
build() {
    local kind=$1 version=$2
    tar xzf "$src/m1n1-stage2-source.tar.gz" -C "$tree" --strip-components=1 --no-same-owner
    rm -rf -- "$tree/build"
    if [ "$kind" != stock ]; then
        cp "$scratch/verified/display/j713/m1n1-base/src/"{kboot.c,dart.c,dart.h} "$tree/src/"
        cp "$scratch/verified/j715-profile-generated.h" "$tree/src/"
        (cd "$tree" && GIT_MASTER=1 git apply --check "$here/m1n1-j715-handoff.patch")
        (cd "$tree" && GIT_MASTER=1 git apply "$here/m1n1-j715-handoff.patch")
    fi
    # Apply after the donor source overlay: that overlay predates these fixes.
    for sep_patch in m1n1-sep-handoff.patch m1n1-preserve-sep-rng.patch; do
        (cd "$tree" && GIT_MASTER=1 git apply --check "$here/$sep_patch")
        (cd "$tree" && GIT_MASTER=1 git apply "$here/$sep_patch")
    done
    if [ "$kind" = j715-shown ]; then
        (cd "$tree" && GIT_MASTER=1 git apply "$scratch/verified/display/j713/m1n1-show-notch.patch")
    fi
    (
        cd "$tree"
        export M1N1_VERSION_TAG=$version
        for pass in 1 2; do
            make ARCH=aarch64-linux-gnu- BUILDSTD=1 USE_CLANG=1 TOOLCHAIN=/usr/lib/llvm-21/bin/ LLDDIR=/usr/lib/llvm-21/bin/ \
                RELEASE=1 CHAINLOADING=0 NO_DISPLAY=1 -j8
        done
    ) > "$scratch/output/stage2-$kind-build.log" 2>&1
    cp "$tree/build/m1n1.bin" "$scratch/output/stage2-$kind-m1n1.bin"
    rm -rf -- "$tree"
    mkdir "$tree"
}
tag=air-c9ef56f-gpu-stage2-20261008
build stock "$tag"
[ "$(sha256sum < "$scratch/output/stage2-stock-m1n1.bin" | cut -d' ' -f1)" = "$stock_sha" ]
build j715-hidden "$tag-j715-backlight1"
build j715-shown "$tag-j715-backlight1-notch1"
cp "$scratch/verified/"{profile.json,source-receipt.json} "$scratch/output/"
cp -r "$scratch/verified/evidence" "$scratch/output/"
python3 -B "$tools/j715_display_profile.py" "$profile" --receipt "$scratch/output"
sha256sum "$here/m1n1-j715-handoff.patch" "$scratch/output/"*.bin
mv "$scratch/output" "$out"
