#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Keep the published channel's makepkg metadata format on native ARM builders.
# Extract only portable shell tooling; keep the host's native vercmp/testpkg.
set -euo pipefail
destination=${1:?usage: bootstrap-m4-makepkg.sh new-directory}
if [[ -e "$destination" || -L "$destination" ]]; then
  printf '%s already exists\n' "$destination" >&2
  exit 1
fi
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
url=https://archive.ubuntu.com/ubuntu/pool/universe/p/pacman-package-manager/makepkg_6.0.2-6ubuntu2_amd64.deb
curl --fail --location --silent --show-error --output "$scratch/makepkg.deb" "$url"
printf '%s  %s\n' \
  bf4a72330e297a7fc4b259ef5be5c05eab0b5c6882cf3eccc65e0bb8c3271d1c \
  "$scratch/makepkg.deb" | sha256sum --check --strict -
mkdir -p "$destination"
bsdtar -xOf "$scratch/makepkg.deb" data.tar.zst | bsdtar -xf - -C "$destination" \
  ./usr/bin/makepkg ./usr/share/makepkg ./usr/share/doc/makepkg/copyright
LIBRARY="$destination/usr/share/makepkg" "$destination/usr/bin/makepkg" --version
