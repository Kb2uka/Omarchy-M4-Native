#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Keep the J713 GPU and display PMGR domains powered across s2idle.

Adds `apple,always-on` to the gfx, disp_cpu and dptx_phy power controllers of
the pinned backlight DTB. pmgr-pwrstate then sets GENPD_FLAG_ALWAYS_ON, so
system suspend no longer powers them off. Requires dtc and fdtoverlay; never
reads hardware or modifies the input.
"""
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
BASE_SHA256 = 'afb025a8f1c946d164fe962f20b2e9d5cd987cfc668985eee62e41767722b2aa'
PMGR = '/soc/power-management@380700000'
DOMAINS = {
    PMGR + '/power-controller@250': 'gfx',
    PMGR + '/power-controller@10000': 'disp_cpu',
    PMGR + '/power-controller@3c8': 'dptx_phy',
}
PROPERTY = 'apple,always-on'


def expected_nodes(old):
    require(b'apple,j713' in old['/']['compatible'].split(b'\0') and
            b'apple,t8132' in old['/']['compatible'].split(b'\0'), 'Wrong board')
    expected = {path: dict(props) for path, props in old.items()}
    for path, label in DOMAINS.items():
        props = old.get(path)
        require(props is not None and props.get('label') == label.encode() + b'\0',
                f'Unexpected power domain {path}')
        require(PROPERTY not in props, f'{label} is already always-on')
        expected[path][PROPERTY] = b''
    return expected


def overlay_source():
    fragments = ' '.join(f'fragment@{number} {{ target-path = "{path}"; '
                         f'__overlay__ {{ {PROPERTY}; }}; }};'
                         for number, path in enumerate(DOMAINS))
    return '/dts-v1/; /plugin/; / { ' + fragments + ' };'


def build(base, output, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified backlight base DTB')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    expected = expected_nodes(old)
    with tempfile.TemporaryDirectory(prefix='j713-sleep-domains-', dir=output.parent) as scratch:
        source, compiled, candidate = (Path(scratch) / name for name in
                                       ('sleep-domains.dtso', 'sleep-domains.dtbo',
                                        'candidate.dtb'))
        source.write_text(overlay_source())
        subprocess.run([dtc, '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(source)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new == expected and reservations == new_reservations and cpu == new_cpu,
                'Candidate changed data beyond the always-on domains')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'always_on': DOMAINS, 'reservations_and_boot_cpu_unchanged': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
