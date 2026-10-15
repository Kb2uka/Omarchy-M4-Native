#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# fetch-asahi-dts.sh <new-directory> — fetch only the Apple devicetree sources
# and dt-bindings headers of the pinned Asahi kernel (tag asahi-7.1.9-1), the
# tree this repository's patches/00*.patch apply to. tools/m4-board-dtbs.py
# and tests/test_m4_board_dtbs.py read it.
set -euo pipefail
commit=77cb8f24c2381a8abb7272d7bbdec548d6426a8a
dir=${1:?usage: fetch-asahi-dts.sh <new-directory>}
[[ ! -e "$dir" ]] || { echo "$dir already exists" >&2; exit 1; }
git init -q "$dir"
cd "$dir"
git remote add origin https://github.com/AsahiLinux/linux.git
git sparse-checkout set --no-cone /arch/arm64/boot/dts/apple/ /include/dt-bindings/
# The checkout downloads the sparse files' blobs, so it retries with the fetch.
for attempt in 1 2 3; do
  git fetch -q --depth 1 --filter=blob:none origin "$commit" &&
    git -c advice.detachedHead=false checkout -q -f FETCH_HEAD && break
  (( attempt < 3 )) || { echo 'Could not fetch the pinned Asahi tree' >&2; exit 1; }
  sleep $((attempt * 15))
done
[[ $(git rev-parse HEAD) == "$commit" ]] || { echo 'Fetched an unexpected commit' >&2; exit 1; }
echo "Asahi devicetree sources at $commit in $PWD"
