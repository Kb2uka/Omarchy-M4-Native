#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Build in disposable containers on a leased build host. No target installation.
set -euo pipefail
if [ "$#" != 7 ]; then
 echo 'usage: build-j713-audio.sh KERNEL_BUILD SOUND_EXPORTS SYSROOT CARGO_HOME KERNEL_IMAGE DAEMON_IMAGE OUTPUT' >&2
 exit 2
fi
kernel=$(realpath "$1")
exports=$(realpath "$2")
sysroot=$(realpath "$3")
cargo=$(realpath "$4")
kernel_image=$5
daemon_image=$6
output=$(realpath -m "$7")
source_root=$(cd "$(dirname "$0")/../audio/j713" && pwd)
(cd "$source_root" && sha256sum --check imported-source.sha256)
# Explicit immutable local image IDs. No implicit pulls or moving tag selection.
[[ $kernel_image =~ ^sha256:[a-f0-9]{64}$ && $daemon_image =~ ^sha256:[a-f0-9]{64}$ ]]
[ ! -e "$output" ]
(cd "$kernel" && sha256sum --check "$source_root/kernel-inputs.sha256")
[ "$(sha256sum "$exports" | cut -d' ' -f1)" = 9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362 ]
mkdir -p "$output"
cp -a "$source_root/kernel" "$source_root/bootstrap" "$source_root/topology" "$output/"
cp -a "$source_root/speakersafetyd" "$output/source"
# Cargo cache may acquire its normal index/package locks; preserve the supplied cache.
cp -a "$cargo" "$output/cargo-home"
cp -a "$sysroot" "$output/air-sysroot"
for component in kernel bootstrap topology; do
 docker run --rm --network none --pull never --entrypoint /bin/bash \
   -v "$kernel:/kernel" -v "$exports:/reference-sound-exports.symvers:ro" \
   -v "$output/$component:/job" "$kernel_image" -c \
   'set -e; make -C /kernel ARCH=arm64 LLVM=1 LOCALVERSION= KCFLAGS=-Werror KBUILD_EXTRA_SYMBOLS=/reference-sound-exports.symvers J713_PROFILE=conservative-v1 M=/job -j2 modules'
done
(cd "$kernel" && sha256sum --check "$source_root/kernel-inputs.sha256")
docker run --rm --network none --pull never --entrypoint /bin/bash -v "$output:/job" "$daemon_image" -c '
set -euo pipefail
cd /job/source
export CARGO_HOME=/job/cargo-home CARGO_TARGET_DIR=/job/target-air
export PKG_CONFIG_ALLOW_CROSS=1 PKG_CONFIG_SYSROOT_DIR=/job/air-sysroot PKG_CONFIG_LIBDIR=/job/air-sysroot/usr/lib/pkgconfig
export CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=aarch64-linux-gnu-gcc
export RUSTFLAGS="-C link-arg=--sysroot=/job/air-sysroot -C link-arg=-B/job/air-sysroot/usr/lib/ -C link-arg=-L/job/air-sysroot/usr/lib -C link-arg=-Wl,-rpath-link,/job/air-sysroot/usr/lib -C link-arg=-Wl,-t"
cargo rustc --release --target aarch64-unknown-linux-gnu --locked --offline -vv -- -C link-arg=-Wl,-Map=/job/air-link.map > /job/air-link-build.log 2>&1
cp /job/target-air/aarch64-unknown-linux-gnu/release/speakersafetyd /job/speakersafetyd-j713
readelf -h /job/speakersafetyd-j713
readelf --version-info /job/speakersafetyd-j713
'
sha256sum "$output"/{kernel,bootstrap,topology}/*.ko "$output/speakersafetyd-j713"
printf '%s\n' "$kernel_image" "$daemon_image" > "$output/toolchain-image-ids.txt"
# Packaging is a separate exact-hash gate. A merely successful build is not acceptance.
