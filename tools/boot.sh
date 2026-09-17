#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# boot.sh <dtb> <tag> [kernel]  — one logged Linux boot through the m1n1 proxy.
# Kernel defaults to Image-m4hacks-smc-nvme.gz (fixed for the whole night;
# pass a third arg ONLY for the rung-1 kernel fallback in docs/night3-runbook.md).
# Arms tools/reboot-probe.sh first (console-free "boot completed" verdict).
set -uo pipefail
DTB=${1:?usage: boot.sh <dtb> <tag> [kernel]}; TAG=${2:?usage: boot.sh <dtb> <tag> [kernel]}
B=~/omarchy-firstboot
KERNEL=${3:-$B/Image-m4hacks-smc-nvme.gz}
INITRD=$B/initramfs.cpio.gz
ARGS="nosmp idle=nop panic=-1 console=tty0 fbcon=nodefer loglevel=7"
DEV=/dev/cu.usbmodemYOUR_DEVICE1
STAMP=$(date +%Y%m%d-%H%M%S)
LOG=$B/logs/boot-$TAG-$STAMP.log
mkdir -p "$B/logs"
[ -r "$DTB" ] || { echo "no such dtb: $DTB" >&2; exit 2; }
[ -r "$KERNEL" ] || { echo "no such kernel: $KERNEL" >&2; exit 2; }
[ -e "$DEV" ] || { echo "proxy $DEV not present — is the Air in m1n1 with the cable on the port farther from MagSafe?" >&2; exit 3; }
{
  echo "boot $STAMP tag=$TAG"
  echo "kernel $(shasum -a 256 "$KERNEL")"
  echo "dtb    $(shasum -a 256 "$DTB")"
  echo "initrd $(shasum -a 256 "$INITRD")"
  echo "args   $ARGS"
} | tee "$LOG"
"$B/tools/reboot-probe.sh" &
PROBE=$!
cd "$B/m1n1/proxyclient"
M1N1DEVICE=$DEV script -q -a "$LOG" /usr/bin/python3 tools/linux.py -b "$ARGS" "$KERNEL" "$DTB" "$INITRD"
echo "linux.py exited $? (Miniterm dying with 'Device not configured' at handoff is normal)" | tee -a "$LOG"
echo "reboot-probe pid $PROBE still watching — its verdict lands in logs/reboot-probe-*.log"
wait $PROBE
