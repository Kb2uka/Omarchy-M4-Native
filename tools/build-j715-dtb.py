#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Copyright (c) 2026 Kazuhide Takahashi
"""Build the 15-inch M4 Air (J715) boot devicetree from the J713 one (docs/j715-boot-dtb.md)."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location(
    'ecluster_dtb', Path(__file__).resolve().with_name('build-j713-ecluster-dtb.py'))
ECLUSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ECLUSTER)
require, sha, tree = ECLUSTER.require, ECLUSTER.sha, ECLUSTER.tree

# The front-USB J713 devicetree (docs/j713-front-usb.md), the newest the Air boots.
BASE_SHA256 = '352bfa74351ad7d12d993f752fe9c09d682511bca0d4a60177cda0d716d1d614'
J713 = b'apple,j713\0apple,t8132\0apple,arm-platform\0'
J715 = b'apple,j715\0apple,t8132\0apple,arm-platform\0'
J713_MODEL = b'Apple MacBook Air (13-inch, M4, 2025)\0'
J715_MODEL = b'Apple MacBook Air (15-inch, M4, 2025)\0'
PORT0 = '/soc/pcie@1cb0000000/pci@0,0'
RADIOS = (PORT0 + '/wifi@0,0', PORT0 + '/bluetooth@0,1')
TRACKPAD = '/soc/fifo@394b14000/input@8000/multi-touch'


def expected_nodes(old):
    """Return the exact tree the overlay must produce."""
    expected = {path: dict(props) for path, props in old.items()}
    expected['/'].update(compatible=J715, model=J715_MODEL)
    for radio in RADIOS:
        expected[radio]['brcm,board-type'] = b'apple,kos\0'
    expected[TRACKPAD]['firmware-name'] = b'apple/tpmtfw-j715.bin\0'
    return expected


def check_base(old):
    require(old['/'].get('compatible') == J713 and old['/'].get('model') == J713_MODEL, 'Wrong board')
    require(all(radio in old and old[radio].get('brcm,board-type') == b'apple,garden\0' for radio in RADIOS),
            'Unexpected radios')
    require(TRACKPAD in old and old[TRACKPAD].get('firmware-name') == b'apple/tpmtfw-j713.bin\0',
            'Unexpected trackpad firmware')


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified J713 base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    check_base(old)
    with tempfile.TemporaryDirectory(prefix='j715-dtb-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'j715.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        subprocess.run([dtc, '-q', '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old),
                'Candidate changed data beyond the J715 board identity')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()), 'board': 'apple,j715',
            'radios': 'apple,kos', 'trackpad_firmware': 'apple/tpmtfw-j715.bin',
            'other_properties_reservations_and_boot_cpu_unchanged': True, 'kernel_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j715-board.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
