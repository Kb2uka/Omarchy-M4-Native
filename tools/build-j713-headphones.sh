#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Luthfi Hizb
# Build the two headphone jack modules in a disposable container, from the same
# pinned kernel inputs as build-j713-audio.sh. No target installation. A
# successful build is not acceptance: compare the hashes with headphone_modules
# in packaging/omarchy-m4/channel.json.
set -euo pipefail
if [ "$#" != 4 ]; then
 echo 'usage: build-j713-headphones.sh KERNEL_BUILD SOUND_EXPORTS KERNEL_IMAGE OUTPUT' >&2
 exit 2
fi
kernel=$(realpath "$1")
exports=$(realpath "$2")
kernel_image=$3
output=$(realpath -m "$4")
audio=$(cd "$(dirname "$0")/../audio/j713" && pwd)
# An explicit immutable local image ID. No implicit pulls or moving tag selection.
[[ $kernel_image =~ ^sha256:[a-f0-9]{64}$ ]]
[ ! -e "$output" ]
(cd "$kernel" && sha256sum --check "$audio/kernel-inputs.sha256")
[ "$(sha256sum "$exports" | cut -d' ' -f1)" = 9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362 ]
mkdir -p "$output"
cp -a "$audio/headphones/kernel" "$output/kernel"
rm -rf "$output/kernel/test"
docker run --rm --network none --pull never --entrypoint /bin/bash --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$kernel:/kernel" -v "$exports:/reference-sound-exports.symvers:ro" \
  -v "$output/kernel:/job" "$kernel_image" -c \
  'set -e; make -C /kernel ARCH=arm64 LLVM=1 LOCALVERSION= KCFLAGS=-Werror KBUILD_EXTRA_SYMBOLS=/reference-sound-exports.symvers M=/job -j2 modules'
(cd "$kernel" && sha256sum --check "$audio/kernel-inputs.sha256")
sha256sum "$output"/kernel/*.ko
