#!/bin/bash
# SPDX-License-Identifier: MIT
# Build m1n1 test loaders from alpha.1's bootloader source plus patches (#82).
#
#   build-test-loaders.sh COMPANION_SOURCE_DIR OUT_DIR TAG_SUFFIX STAGES PATCH...
#
#   dockchannel TX bound (test 1):
#     build-test-loaders.sh source out dctx1 "stage1 stage2" dockchannel-tx-bound.patch
#   #82 fix, stage 1 only:
#     build-test-loaders.sh source out dctx1-ans1 stage1 dockchannel-tx-bound.patch stage1-ans-no-reset.patch
#
# Runs in m4-gravity-builder:20261008 (clang 21.1.8, rustc 1.93.1 with rust-src)
# with the job directory mounted at /job, where alpha.1's loaders were built.
# COMPANION_SOURCE_DIR is the source/ directory of the alpha.1 companion. Each
# stage is first rebuilt unpatched and must match alpha.1's shipped hash, so a
# test loader differs from alpha.1 only by the patches, applied in order, and
# its version tag (alpha.1's tag plus -TAG_SUFFIX).
set -euo pipefail
if [ $# -lt 5 ]; then
    echo "usage: $0 COMPANION_SOURCE_DIR OUT_DIR TAG_SUFFIX STAGES PATCH..." >&2
    exit 2
fi
src=$1 out=$2 suffix=$3 stages=$4
shift 4
patches=()
for patch in "$@"; do
    patches+=("$(realpath "$patch")")
done
for stage in $stages; do
    case $stage in
    stage1 | stage2) ;;
    *)
        echo "unknown stage: $stage" >&2
        exit 2
        ;;
    esac
done
declare -A source_sha=(
    [stage1]=6c14369619d19612c68f67ed4467ac27158138c6b4b2ec2bc831075945eef4af
    [stage2]=c394517941a5700b227718bb02e3df3dd43c9ad5a8e53c9447a55d3c36846c44
)
declare -A stock_sha=(
    [stage1]=18cc4cd1ba07928078d2cb52a2c1c5f40a4661fd3db7ee6af0e6923fd237be10
    [stage2]=3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4
)
export PATH=/usr/lib/llvm-21/bin:$PATH
mkdir -p "$out"

# alpha.1 was built at these paths. Refuse to reuse one, and remove them on any exit.
trees=(/job/bootloader-air-knownbase-stage1 /job/bootloader-air-knownbase-stage2)
for tree in "${trees[@]}"; do
    if [ -e "$tree" ]; then
        echo "$tree exists; remove it first" >&2
        exit 1
    fi
done
trap 'rm -rf "${trees[@]}"' EXIT

build() {
    local stage=$1 kind=$2 tag=$3 chain=0
    local tree=/job/bootloader-air-knownbase-$stage
    test "$stage" != stage1 || chain=1
    mkdir "$tree"
    tar xzf "$src/m1n1-$stage-source.tar.gz" -C "$tree" --strip-components=1
    # The archive keeps the generated headers; alpha.1 was built without them.
    rm -rf "$tree/build"
    if [ "$kind" = test ]; then
        for patch in "${patches[@]}"; do
            (cd "$tree" && git apply --verbose "$patch")
        done
    fi
    (
        cd "$tree"
        export M1N1_VERSION_TAG=$tag
        # alpha.1 ran make twice; do the same.
        for pass in 1 2; do
            make BUILDSTD=1 USE_CLANG=1 TOOLCHAIN=/usr/lib/llvm-21/bin/ LLDDIR=/usr/lib/llvm-21/bin/ \
                RELEASE=1 CHAINLOADING="$chain" NO_DISPLAY=1 -j8
        done
        cat build/build_cfg.h build/build_tag.h
    ) > "$out/$stage-$kind-build.log" 2>&1
    cp "$tree/build/m1n1.bin" "$out/$stage-$kind-m1n1.bin"
    rm -rf "$tree"
}

for stage in $stages; do
    test "$(sha256sum < "$src/m1n1-$stage-source.tar.gz" | cut -d' ' -f1)" = "${source_sha[$stage]}"
    build "$stage" stock "air-c9ef56f-gpu-$stage-20261008"
    test "$(sha256sum < "$out/$stage-stock-m1n1.bin" | cut -d' ' -f1)" = "${stock_sha[$stage]}"
    build "$stage" test "air-c9ef56f-gpu-$stage-20261008-$suffix"
done
(cd "$out" && sha256sum ./*.bin)
