#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add P states up to 4464 MHz behind a closed boost gate, and E states up to 2808 MHz, to the J713 E-cluster DTB."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile

SPEC = importlib.util.spec_from_file_location(
    'ecluster_dtb', Path(__file__).resolve().with_name('build-j713-ecluster-dtb.py'))
ECLUSTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ECLUSTER)
require, sha, tree, cells, phandles = ECLUSTER.require, ECLUSTER.sha, ECLUSTER.tree, ECLUSTER.cells, ECLUSTER.phandles
E_OPP, P_OPP, ZONE, P_POLICY = ECLUSTER.E_OPP, ECLUSTER.P_OPP, ECLUSTER.ZONE, ECLUSTER.P_POLICY

# The E-cluster and CPU thermal zone DTB (docs/j713-cpu-idle.md).
BASE_SHA256 = 'fdedc09f2fe8ed681a61208589024e01fb5efa77beebbd0eb4ad267c08e77f7c'
# One-based state -> MHz, ADT voltage-states5-sram (P) and voltage-states1-sram (E),
# docs/j713-cpu-boost.md. E8 (2892 MHz) is left out: the hardware runs it as E7.
P_BASE = {1: 912, 2: 1260, 3: 1512, 4: 1800}
P_STATES = {5: 2088, 6: 2352, 7: 2616, 8: 2868, 9: 3096, 10: 3300, 11: 3468, 12: 3624,
            13: 3756, 14: 3876, 15: 3936, 16: 3984, 17: 4044, 18: 4104, 19: 4416, 20: 4464}
E_BASE = {1: 900, 2: 1080, 3: 1476, 4: 1860, 5: 2172}
E_STATES = {6: 2496, 7: 2808}
# Always reached (the die never reads 20 C), and pins the P cooling device 16
# states below P20, which is P4: 1800 MHz until j713_pguard opens the gate.
GATE = ZONE + '/trips/boost-gate'
GATE_MAP = ZONE + '/cooling-maps/map-p-boost-gate'
GATE_MILLI_C, GATE_COOLING_STATE = 20000, 16


def opp(state, mhz):
    return {'opp-hz': struct.pack('>Q', mhz * 1000000), 'opp-level': cells(state)}


def expected_nodes(old):
    """Return the exact tree the overlay must produce."""
    expected = {path: dict(props) for path, props in old.items()}
    gate = max(phandles(old)) + 1
    for state, mhz in P_STATES.items():
        expected[f'{P_OPP}/opp{state:02d}'] = opp(state, mhz)
    for state, mhz in E_STATES.items():
        expected[f'{E_OPP}/opp{state:02d}'] = opp(state, mhz)
    expected[GATE] = {'temperature': cells(GATE_MILLI_C), 'hysteresis': cells(0),
                      'type': b'passive\0', 'phandle': cells(gate)}
    expected[GATE_MAP] = {'trip': cells(gate),
                          'cooling-device': cells(P_POLICY[1], GATE_COOLING_STATE, GATE_COOLING_STATE)}
    return expected


def check_table(old, table, states, name):
    entries = sorted(path for path in old if path.startswith(table + '/'))
    require(entries == [f'{table}/opp{state:02d}' for state in states] and
            all(old[f'{table}/opp{state:02d}'] == opp(state, mhz) for state, mhz in states.items()),
            'Unexpected %s OPP table' % name)


def check_base(old):
    board = old['/']['compatible'].split(b'\0')
    require(b'apple,j713' in board and b'apple,t8132' in board, 'Wrong board')
    check_table(old, P_OPP, P_BASE, 'P')
    check_table(old, E_OPP, E_BASE, 'E')
    path, handle = P_POLICY
    require(phandles(old).get(handle) == path, 'Policy CPU phandle moved: ' + path)
    require(ZONE + '/trips/cpu-die-passive' in old and ZONE + '/cooling-maps/map-p-cluster' in old,
            'No CPU die thermal zone')
    require(sorted(p for p in old if p.startswith(ZONE + '/trips/')) == [ZONE + '/trips/cpu-die-passive'],
            'Unexpected CPU die trips')


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified E-cluster base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    check_base(old)
    with tempfile.TemporaryDirectory(prefix='j713-pboost-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'pboost.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        # The gate's cooling map names the P policy CPU by phandle number, which dtc cannot
        # resolve in an overlay.
        subprocess.run([dtc, '-W', 'no-cooling_device_property', '-I', 'dts', '-O', 'dtb',
                        '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old),
                'Candidate changed data beyond the boost states and the boost gate')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()),
            'p_states_mhz': list(P_BASE.values()) + list(P_STATES.values()),
            'e_states_mhz': list(E_BASE.values()) + list(E_STATES.values()),
            'boost_gate': {'trip_c': GATE_MILLI_C / 1000, 'p_cooling_state': GATE_COOLING_STATE,
                           'p_mhz_while_closed': P_BASE[4], 'opened_by': 'j713_pguard'},
            'other_properties_reservations_and_boot_cpu_unchanged': True, 'kernel_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j713-pboost.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
