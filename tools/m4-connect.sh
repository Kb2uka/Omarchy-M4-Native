#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
#
# Connect this M4 Air to the signed [omarchy-m4] update channel in one paste:
#
#   curl -fsSL --retry 5 --retry-all-errors https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages/m4-connect.sh | bash
#
# The publish step writes the SHA256 of the setup script it publishes into this
# file. Any other setup script is refused before anything runs. Everything is
# inside main(), which bash runs only once the definition is complete, and main
# stops bash reading further input, so a cut-off or retried download cannot run
# a partial script or run setup twice.
set -euo pipefail

main() {
  # Read nothing more from the pipe this script arrived on. Bash reads a piped
  # script a byte at a time, so after main it reaches end of input here, even
  # when a retried download handed it a prefix followed by the whole file; and
  # nothing main starts can consume script bytes. A terminal is left alone, so
  # text pasted into an interactive shell does not close it.
  if [[ ! -t 0 ]]; then
    exec </dev/null
  fi
  local base="${M4_CONNECT_BASE:-https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages}"
  local expected='@SETUP_SHA256@'
  if [[ ! $expected =~ ^[0-9a-f]{64}$ ]]; then
    echo 'm4-connect: this copy has no published checksum; use the one from the packages release' >&2
    return 1
  fi
  # Global, not local: the EXIT trap runs after main() has returned.
  dir=$(mktemp -d)
  trap 'rm -rf "$dir"' EXIT
  echo 'Downloading the M4 update setup...'
  # Retry everything: a Wi-Fi DNS hiccup shows up as "Could not resolve host".
  curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 \
    -o "$dir/omarchy-m4-setup.py" "$base/omarchy-m4-setup.py"
  local actual
  actual=$(sha256sum "$dir/omarchy-m4-setup.py")
  actual=${actual%% *}
  if [[ $actual != "$expected" ]]; then
    echo "m4-connect: the setup script does not match its published checksum; nothing was changed" >&2
    return 1
  fi
  echo 'omarchy-m4-setup.py: OK'
  echo 'Connecting to M4 updates. Type your password when asked.'
  sudo python3 "$dir/omarchy-m4-setup.py" --apply
}

# No redirection here: bash would restore the pipe when main returns.
main "$@"
