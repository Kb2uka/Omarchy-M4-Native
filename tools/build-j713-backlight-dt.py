#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add the verified J713 brightness-only resources to the pinned USB0 DTB.

Only MMIO resources are static. The stage2 handoff reserves DRAM and supplies
omarchy,display-handoff-verified from the current boot's ADT and DART tables.
Requires dtc and fdtoverlay; never reads hardware or modifies the input.
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
require, sha, tree, cells, phandles = (ECLUSTER.require, ECLUSTER.sha, ECLUSTER.tree,
                                    ECLUSTER.cells, ECLUSTER.phandles)
BASE_SHA256 = '352bfa74351ad7d12d993f752fe9c09d682511bca0d4a60177cda0d716d1d614'
DART = '/soc/iommu@30a340000'
MBOX = '/soc/mbox@30ae08000'
CLIENT = '/soc/dcp-backlight@30ae00000'
AIC = '/soc/interrupt-controller@381180000'
POWER = '/soc/power-management@380700000/power-controller@10000'


def expected_nodes(old):
    require(b'apple,j713' in old['/']['compatible'].split(b'\0') and
            b'apple,t8132' in old['/']['compatible'].split(b'\0'), 'Wrong board')
    require(old[AIC]['phandle'] == cells(0x0d), 'Unexpected AIC')
    require(old[POWER]['phandle'] == cells(0x6c) and
            old[POWER]['label'] == b'disp_cpu\0', 'Unexpected existing PD181')
    require(all(path not in old for path in (DART, MBOX, CLIENT)) and
            'dcp' not in old['/aliases'], 'Brightness resources already exist')
    top = max(phandles(old))
    require(top < 0xfffffffc, 'No available phandles')
    expected = {path: dict(props) for path, props in old.items()}
    shared = {'interrupt-parent': cells(0x0d), 'power-domains': cells(0x6c),
              'status': b'okay\0'}
    expected[DART] = dict(shared, **{
        'compatible': b'apple,t8132-dart\0apple,t8110-dart\0',
        'reg': cells(3, 0x0a340000, 0, 0x20000), '#iommu-cells': cells(1),
        'interrupts': cells(0, 678, 4), 'phandle': cells(top + 1)})
    expected[MBOX] = dict(shared, **{
        'compatible': b'apple,t8122-asc-mailbox\0apple,asc-mailbox-v4\0',
        'reg': cells(3, 0x0ae08000, 0, 0x4000), '#mbox-cells': cells(0),
        'interrupts': cells(0, 653, 4, 0, 654, 4, 0, 655, 4, 0, 656, 4),
        'interrupt-names': b'send-empty\0send-not-empty\0recv-empty\0recv-not-empty\0',
        'phandle': cells(top + 2)})
    expected[CLIENT] = {
        'compatible': b'omarchy,j713-dcp-backlight\0',
        'reg': cells(3, 0x0ae00000, 0, 0x88000),
        'iommus': cells(top + 1, 23), 'mboxes': cells(top + 2),
        'mbox-names': b'mbox\0', 'power-domains': cells(0x6c),
        'status': b'okay\0', 'phandle': cells(top + 3)}
    expected['/aliases']['dcp'] = CLIENT.encode() + b'\0'
    return expected


def overlay_source(expected):
    # Local phandles and explicit fixups avoid adding symbol nodes to the DT.
    def properties(props):
        return '\n'.join(f'{name} = [{value.hex(" ")}];' for name, value in props.items())
    local = {path: dict(expected[path]) for path in (DART, MBOX, CLIENT)}
    for number, path in enumerate((DART, MBOX, CLIENT), 1):
        local[path]['phandle'] = cells(number)
    local[CLIENT]['iommus'] = cells(1, 23)
    local[CLIENT]['mboxes'] = cells(2)
    nodes = '\n'.join(f'{path.rsplit("/", 1)[1]} {{ {properties(local[path])} }};'
                      for path in (DART, MBOX, CLIENT))
    return ('/dts-v1/; /plugin/; / { fragment@0 { target-path = "/soc"; '
            '__overlay__ { #address-cells = <2>; #size-cells = <2>; ' + nodes + ' }; }; '
            'fragment@1 { target-path = "/aliases"; '
            '__overlay__ { dcp = "' + CLIENT + '"; }; }; '
            '__local_fixups__ { fragment@0 { __overlay__ { '
            'dcp-backlight@30ae00000 { iommus = <0>; mboxes = <0>; }; }; }; }; };')


def build(base, output, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified USB0 base DTB')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    expected = expected_nodes(old)
    with tempfile.TemporaryDirectory(prefix='j713-backlight-', dir=output.parent) as scratch:
        source, compiled, candidate = (Path(scratch) / name for name in
                                       ('backlight.dtso', 'backlight.dtbo', 'candidate.dtb'))
        source.write_text(overlay_source(expected))
        subprocess.run([dtc, '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(source)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new == expected and reservations == new_reservations and cpu == new_cpu,
                'Candidate changed data beyond brightness resources')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'enabled': [DART, MBOX, CLIENT], 'stream': 23,
            'reservations_and_boot_cpu_unchanged': True,
            'handoff_marker_requires_stage2': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
