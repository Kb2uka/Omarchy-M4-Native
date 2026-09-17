#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# mkdtb.sh <out.dtb> [label ...]  — build t8132-j713 DTB from the linux-asahi
# freeze, with the given DT labels forced to status = "disabled".
# Later overrides win in dtc, so appending "&label { status = "disabled"; };"
# is exact. Baseline: exactly ONE simple_bus_reg warning (proven gate).
set -euo pipefail
OUT=$1; shift
export PATH=/opt/homebrew/bin:$PATH
SRC=~/omarchy-firstboot/omarchy/linux-asahi
cd "$SRC"
{
  cat arch/arm64/boot/dts/apple/t8132-j713.dts
  for l in "$@"; do echo "&$l { status = \"disabled\"; };"; done
} | clang -E -nostdinc -I include -I arch/arm64/boot/dts/apple -undef -D__DTS__ \
      -x assembler-with-cpp - \
  | dtc -O dtb -o "$OUT" -I dts - 2> >(grep -v simple_bus_reg >&2 || true)
echo "built $OUT ($(shasum -a 256 "$OUT" | cut -c1-16)) disabled: ${*:-none}"
