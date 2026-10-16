#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
set -euo pipefail
kit=$(cd "$(dirname "$0")" && pwd -P)
action=${1:---help}
if [[ "$action" == --help || $# == 0 ]]; then
  printf '%s\n' 'Experimental Omarchy M4 Native v0.1.0-alpha.1.' \
    'Read START-HERE.md first. Launch from macOS on a 13-inch M4 Air (Mac16,12).' \
    'Verify files: bash Install.command --verify-only' \
    'Install: bash Install.command --install [--ipsw-dir DIRECTORY]' \
    'The interactive installer can change partitions and the selected OS boot policy.'
  exit 0
fi
[[ "$action" == --verify-only || "$action" == --install ]] || { echo 'Unknown action' >&2; exit 2; }
shift
ipsw=''
if (( $# )); then
  [[ $# == 2 && "$1" == --ipsw-dir && -d "$2" ]] || { echo 'Invalid IPSW directory option' >&2; exit 2; }
  ipsw=$(cd "$2" && pwd -P)
  [[ -f "$ipsw/UniversalMac_26.5.1_25F80_Restore.ipsw" ]] || { echo 'Complete qualified IPSW is missing' >&2; exit 2; }
fi
printf '%s\n' 'Verifying installer and source files. The system image takes longer; wait for its OK line.'
(cd "$kit" && shasum -a 256 -c SHA256SUMS)
[[ "$action" == --install ]] || exit 0
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || { echo 'Install from native ARM64 macOS.' >&2; exit 1; }
[[ $(/usr/sbin/sysctl -n hw.model) == Mac16,12 ]] || { echo 'This release supports only Mac16,12 (J713, 13-inch M4 MacBook Air).' >&2; exit 1; }
[[ -t 0 && -t 1 && -r /dev/tty ]] || { echo 'Use a local Terminal on the target Air.' >&2; exit 1; }
printf '%s\n' 'EXPERIMENTAL: installation can cause data loss, boot failures and hardware problems.' \
  'Keep macOS and recovery. Read KNOWN-LIMITATIONS.md, including the shutdown problem.'
read -r -p 'Have you read START-HERE.md and want to continue? [y/N] ' answer
[[ "$answer" == y || "$answer" == Y ]] || exit 0
printf '%s\n' 'Protected movies require Google Widevine. It is obtained from Google after Linux starts.' \
  'Choosing no keeps ordinary video working and leaves protected-media setup disabled.'
read -r -p 'Enable Widevine for the installed browsers? [y/N] ' answer
widevine=no
if [[ "$answer" == y || "$answer" == Y ]]; then widevine=yes; fi
work=$(mktemp -d /private/tmp/omarchy-m4-install.XXXXXXXX)
chmod 700 "$work"
tar -xzf "$kit/installer-macos.tar.gz" -C "$work"
cp "$kit/installer_data.json" "$work/installer_data.json"
printf 'Installer working directory: %s\n' "$work"
args=(/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/var/root \
  TERM="${TERM:-xterm-256color}" LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
  EXPERT=1 REPO_BASE="$kit" M4_OWNER_WIDEVINE="$widevine")
if [[ -n "$ipsw" ]]; then args+=(IPSW_BASE="$ipsw"); fi
exec /usr/bin/caffeinate -dis /usr/bin/sudo -H "${args[@]}" /bin/sh "$work/install.sh"
