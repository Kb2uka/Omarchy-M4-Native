#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add reporting-only CPU die temperature channels to the J713 cpufreq DTB."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location(
    'poweroff_dtb', Path(__file__).resolve().with_name('build-j713-poweroff-dtb.py'))
POWEROFF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POWEROFF)
require, sha, tree = POWEROFF.require, POWEROFF.sha, POWEROFF.tree

# The bounded cpufreq DTB (docs/j713-cpufreq.md).
BASE_SHA256 = '033025bbdda3ce72b5eefe39d1d6d68209256fd43e4fd587c96ae1cf38978555'
HWMON = '/soc/smc@38c600000/hwmon'
# SMC key -> hwmon label, in channel order (docs/j713-cpu-temperature.md).
CHANNELS = {
    'TCMz': 'CPU Die Max',
    'Tp01': 'P-cluster Temperature 1',
    'Tp05': 'P-cluster Temperature 2',
    'Tp09': 'P-cluster Temperature 3',
    'Tp0D': 'P-cluster Temperature 4',
    'Te05': 'E-cluster Temperature 1',
    'Te0S': 'E-cluster Temperature 2',
}


def expected_nodes(old):
    """Return the exact tree the overlay must produce."""
    expected = {path: dict(props) for path, props in old.items()}
    for key, label in CHANNELS.items():
        expected[f'{HWMON}/temperature-{key}'] = {'apple,key-id': key.encode() + b'\0',
                                                  'label': label.encode() + b'\0'}
    return expected


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified cpufreq base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    board = old['/']['compatible'].split(b'\0')
    require(b'apple,j713' in board and b'apple,t8132' in board, 'Wrong board')
    require(old.get(HWMON, {}).get('compatible') == b'apple,smc-hwmon\0', 'Unexpected SMC hwmon node')
    keys = {props.get('apple,key-id') for path, props in old.items() if path.startswith(HWMON + '/')}
    for key in CHANNELS:
        require(f'{HWMON}/temperature-{key}' not in old and key.encode() + b'\0' not in keys,
                'CPU temperature channels already exist')
    with tempfile.TemporaryDirectory(prefix='j713-cputemp-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'cputemp.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        subprocess.run([dtc, '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old),
                'Candidate changed data beyond the CPU temperature channels')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()), 'hwmon_node': HWMON,
            'channels': [{'key': key, 'label': label} for key, label in CHANNELS.items()],
            'other_properties_reservations_and_boot_cpu_unchanged': True,
            'kernel_changed': False, 'clocks_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j713-cpu-temperature.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
