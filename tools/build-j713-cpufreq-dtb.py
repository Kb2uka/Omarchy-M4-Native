#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add bounded CPU frequency scaling and measured core capacities to the J713 poweroff DTB."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location(
    'poweroff_dtb', Path(__file__).resolve().with_name('build-j713-poweroff-dtb.py'))
POWEROFF = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(POWEROFF)
require, sha, tree = POWEROFF.require, POWEROFF.sha, POWEROFF.tree

# Update 1 (v0.1.0-alpha.1-update.1) portable DTB.
BASE_SHA256 = 'e54c77a760a5b696486f9d80c7c83ca4295d641113bb9e54d13e259863bf9b07'
COMPATIBLE = b'apple,t8132-cluster-cpufreq\0apple,t8112-cluster-cpufreq\0'
# Cluster -> (controller node, MMIO base, OPP table, [(Hz, P-state)], capacity-dmips-mhz,
#             CPU core compatible, CPU nodes). Tables stop at iBoot's handoff states.
CLUSTERS = {
    'e': ('/soc/cpufreq@210e20000', 0x210e20000, '/opp-table-0', [(900000000, 1)], 663,
          b'apple,donan-e\0', tuple(f'/cpus/cpu@{n}' for n in range(6))),
    'p': ('/soc/cpufreq@211e20000', 0x211e20000, '/opp-table-1',
          [(912000000, 1), (1260000000, 2), (1512000000, 3), (1800000000, 4)], 1024,
          b'apple,donan-p\0', tuple(f'/cpus/cpu@{n}' for n in (10100, 10101, 10102, 10103))),
}
CPU_PROPERTIES = {'operating-points-v2', 'performance-domains', 'capacity-dmips-mhz'}
U32 = lambda *values: struct.pack('>' + 'I' * len(values), *values)


def expected_nodes(old, new):
    """Return the exact tree the overlay must produce, using the candidate's fresh phandles."""
    expected = {path: dict(props) for path, props in old.items()}
    used = {p['phandle'] for p in old.values() if 'phandle' in p}
    for controller, base, table, opps, capacity, _, cpus in CLUSTERS.values():
        handles = []
        for path in (controller, table):
            handle = new.get(path, {}).get('phandle')
            require(handle is not None and len(handle) == 4 and handle not in used,
                    'Candidate changed data beyond the cpufreq clusters and CPU capacities')
            used.add(handle)
            handles.append(handle)
        expected[controller] = {'compatible': COMPATIBLE, 'reg': U32(base >> 32, base & 0xffffffff, 0, 0x1000),
                                '#performance-domain-cells': U32(0), 'phandle': handles[0]}
        expected[table] = {'compatible': b'operating-points-v2\0', 'opp-shared': b'', 'phandle': handles[1]}
        for hz, level in opps:
            expected[f'{table}/opp{level:02d}'] = {'opp-hz': struct.pack('>Q', hz), 'opp-level': U32(level)}
        for cpu in cpus:
            expected[cpu].update({'operating-points-v2': handles[1], 'performance-domains': handles[0],
                                  'capacity-dmips-mhz': U32(capacity)})
    return expected


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified portable base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    board = old['/']['compatible'].split(b'\0')
    require(b'apple,j713' in board and b'apple,t8132' in board, 'Wrong board')
    require(old['/soc'].get('#address-cells') == U32(2) and old['/soc'].get('#size-cells') == U32(2),
            'Unexpected /soc address layout')
    for controller, _, table, _, _, core, cpus in CLUSTERS.values():
        require(controller not in old and table not in old, 'Cpufreq nodes already exist')
        for path in cpus:
            require(path in old and old[path].get('compatible') == core and
                    not CPU_PROPERTIES & set(old[path]), 'Unexpected CPU node: ' + path)
    with tempfile.TemporaryDirectory(prefix='j713-cpufreq-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'cpufreq.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        subprocess.run([dtc, '-I', 'dts', '-O', 'dtb', '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old, new),
                'Candidate changed data beyond the cpufreq clusters and CPU capacities')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()),
            'clusters': {name: {'controller': c[0], 'max_mhz': c[3][-1][0] // 1000000,
                                'states': [level for _, level in c[3]], 'capacity_dmips_mhz': c[4],
                                'cpus': list(c[6])} for name, c in CLUSTERS.items()},
            'other_properties_reservations_and_boot_cpu_unchanged': True,
            'kernel_changed': False, 'hardware_scaling_verified': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j713-cpufreq.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
