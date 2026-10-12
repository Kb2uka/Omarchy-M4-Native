#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Avoid unrelated Framework audio probing in a pending offline M4 image."""
import argparse
import json
from pathlib import Path
import stat

from m4_audio_package import digest, image_path, require


FRAMEWORK_HELPER = 'usr/share/omarchy/install/user/hardware/framework/fix-f13-amd-audio-input.sh'
FRAMEWORK_SHA256 = 'b76c3e31b0fc012178b9e3e79f8fcd3092c9d405ba50a20e9fb60d4375e98c69'
ARM64_GUARD = b'''# The Framework AMD audio workaround does not apply to this arm64 image.
if [[ $(uname -m) == aarch64 ]]; then
  return 0 2>/dev/null || exit 0
fi

'''


def planned_file(root):
    require(root.is_absolute() and root != Path(root.anchor) and
            root == root.resolve(strict=True), 'A canonical offline image directory is required')
    for name in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
        require(image_path(root, name).is_file(), 'Not a pending M4 owner image: missing ' + name)
    require(not image_path(root, 'etc/m4-install-complete').exists(),
            'Installed owner image is not a pending image')
    path = image_path(root, FRAMEWORK_HELPER)
    require(path.is_file(), 'Missing Framework audio helper')
    require(stat.S_IMODE(path.stat().st_mode) == 0o644, 'Unexpected Framework audio helper mode')
    raw = path.read_bytes()
    original = raw[len(ARM64_GUARD):] if raw.startswith(ARM64_GUARD) else raw
    require(digest(original) == FRAMEWORK_SHA256, 'Unknown Framework audio helper source')
    return path, raw, ARM64_GUARD + original


def configure(root, apply=False):
    path, current, expected = planned_file(root)
    different = current != expected
    if apply:
        if different:
            path.write_bytes(expected)
    else:
        require(not different, 'Owner setup image configuration differs: ' + FRAMEWORK_HELPER)
    return {'configured_files': [FRAMEWORK_HELPER],
            'changed_files': [FRAMEWORK_HELPER] if apply and different else [],
            'scope': 'Offline owner setup only; no device or service operations'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--check', action='store_true')
    args = parser.parse_args()
    print(json.dumps(configure(args.root, args.apply), indent=2))
