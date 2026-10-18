#!/bin/bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
set -euo pipefail
kit=$(cd "$(dirname "$0")" && pwd -P)
action=${1:---help}
if [[ "$action" == --help || $# == 0 ]]; then
  printf '%s\n' 'Omarchy M4 Native v0.1.0-alpha.1, M4 Mac mini (J773g) TESTER build.' \
    'Read START-HERE.md first. Launch from macOS on a plain M4 Mac mini (Mac16,10).' \
    'Verify files: bash Install.command --verify-only' \
    'Install: bash Install.command --install --tester [--ipsw-dir DIRECTORY]' \
    'The interactive installer can change partitions and the selected OS boot policy.'
  exit 0
fi
[[ "$action" == --verify-only || "$action" == --install ]] || { echo 'Unknown action' >&2; exit 2; }
shift
tester=no
if [[ "$action" == --install ]]; then
  [[ "${1:-}" == --tester ]] || { echo 'This is a tester build: add --tester after --install (see START-HERE.md).' >&2; exit 2; }
  tester=yes
  shift
fi
ipsw=''
if (( $# )); then
  [[ $# == 2 && "$1" == --ipsw-dir && -d "$2" ]] || { echo 'Invalid IPSW directory option' >&2; exit 2; }
  ipsw=$(cd "$2" && pwd -P)
  [[ -f "$ipsw/UniversalMac_26.6.2_25G83_Restore.ipsw" ]] || { echo 'Complete qualified IPSW is missing' >&2; exit 2; }
fi
printf '%s\n' 'Verifying installer and source files. The system image takes longer; wait for its OK line.'
(cd "$kit" && shasum -a 256 -c SHA256SUMS)
[[ "$action" == --install ]] || exit 0
[[ $(uname -s) == Darwin && $(uname -m) == arm64 ]] || { echo 'Install from native ARM64 macOS.' >&2; exit 1; }
[[ $(/usr/sbin/sysctl -n hw.model) == Mac16,10 ]] || { echo 'This tester build supports only Mac16,10 (J773g, M4 Mac mini).' >&2; exit 1; }
[[ -t 0 && -t 1 && -r /dev/tty ]] || { echo 'Use a local Terminal on the target Mac mini.' >&2; exit 1; }
printf '%s\n' 'TESTER BUILD: nobody has booted this build on an M4 Mac mini yet.' \
  'Installation can cause data loss, boot failures and hardware problems.' \
  'Keep macOS and recovery, back up first, and read START-HERE.md.'
read -r -p 'Type TEST to install this untested tester build: ' answer
[[ "$answer" == TEST && "$tester" == yes ]] || exit 0
work=$(mktemp -d /private/tmp/omarchy-m4-install.XXXXXXXX)
chmod 700 "$work"
tar -xzf "$kit/installer-macos.tar.gz" -C "$work"
cp "$kit/installer_data.json" "$work/installer_data.json"
printf 'Installer working directory: %s\n' "$work"
# Widevine setup is a J713 service; this build does not run it.
args=(/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin HOME=/var/root \
  TERM="${TERM:-xterm-256color}" LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 \
  EXPERT=1 REPO_BASE="$kit" M4_OWNER_WIDEVINE=no)
if [[ -n "$ipsw" ]]; then args+=(IPSW_BASE="$ipsw"); fi
exec /usr/bin/caffeinate -dis /usr/bin/sudo -H "${args[@]}" /bin/sh "$work/install.sh"
