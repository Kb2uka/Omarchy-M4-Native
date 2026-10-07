#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# boot.sh <dtb> <tag> [kernel]  — one logged Linux boot through the m1n1 proxy.
# Set M1N1DEVICE explicitly to the target's proxy interface, not its HV UART.
# BOOT_KIT, PYTHON and BOOT_ARGS can override the defaults below.
set -euo pipefail
unset CDPATH
DTB=${1:?usage: boot.sh <dtb> <tag> [kernel]}
TAG=${2:?usage: boot.sh <dtb> <tag> [kernel]}
[[ $# -le 3 ]] || { echo "too many arguments" >&2; exit 2; }
[[ "$TAG" =~ ^[a-zA-Z0-9_-]+$ ]] || { echo "invalid log tag" >&2; exit 2; }
DEV=${M1N1DEVICE:?set M1N1DEVICE to the target proxy device}
B=${BOOT_KIT:-$HOME/omarchy-firstboot}
PYTHON=${PYTHON:-python3}
KERNEL=${3:-$B/Image-m4hacks-smc-nvme.gz}
INITRD=$B/initramfs.cpio.gz
ARGS=${BOOT_ARGS:-nosmp idle=nop panic=0 console=tty0 fbcon=nodefer loglevel=7}
[[ -c "$DEV" && -r "$DEV" && -w "$DEV" ]] || {
  echo "proxy device is not an accessible character device: $DEV" >&2; exit 3;
}
for file in "$DTB" "$KERNEL" "$INITRD" "$B/m1n1/proxyclient/tools/linux.py"; do
  [[ -f "$file" && -r "$file" ]] || { echo "missing or unreadable file: $file" >&2; exit 2; }
done
command -v "$PYTHON" >/dev/null || { echo "Python interpreter not found: $PYTHON" >&2; exit 2; }
command -v script >/dev/null || { echo "required command not found: script" >&2; exit 2; }
HOST_OS=$(uname -s)
case "$HOST_OS" in Linux|Darwin) ;; *) echo "unsupported proxy host: $HOST_OS" >&2; exit 2 ;; esac
# Resolve inputs before changing directory. m1n1's loader uses relative imports.
absolute() { (cd "$(dirname -- "$1")" && printf '%s/%s\n' "$PWD" "$(basename -- "$1")"); }
DEV=$(absolute "$DEV")
DTB=$(absolute "$DTB")
KERNEL=$(absolute "$KERNEL")
INITRD=$(absolute "$INITRD")
PYTHON=$(absolute "$(command -v "$PYTHON")")
B=$(cd "$B" && pwd)
mkdir -p "$B/logs"
LOG=$(mktemp "$B/logs/boot-$TAG-$(date -u +%Y%m%dT%H%M%SZ).log.XXXXXX")
hash_file() {
  if command -v sha256sum >/dev/null; then sha256sum "$1"; else shasum -a 256 "$1"; fi
}
{
  printf 'host=%s device=%s\n' "$HOST_OS" "$DEV"
  printf 'kernel '; hash_file "$KERNEL"
  printf 'dtb    '; hash_file "$DTB"
  printf 'initrd '; hash_file "$INITRD"
  printf 'args=%s\n' "$ARGS"
  printf 'log=%s\n' "$LOG"
} | tee "$LOG"
cd "$B/m1n1/proxyclient"
export M1N1DEVICE="$DEV"
LOADER=("$PYTHON" tools/linux.py -b "$ARGS" "$KERNEL" "$DTB" "$INITRD")
status=0
if [[ "$HOST_OS" == Linux ]]; then
  # util-linux script executes a shell string; force Bash for its %q quoting.
  printf -v command '%q ' "${LOADER[@]}"
  SHELL="$BASH" script -q -e -f -a -c "$command" "$LOG" || status=$?
else
  script -q -a "$LOG" "${LOADER[@]}" || status=$?
fi
printf 'loader_exit=%s (not a Linux boot verdict; inspect the target console)\n' "$status" | tee -a "$LOG"
exit "$status"
