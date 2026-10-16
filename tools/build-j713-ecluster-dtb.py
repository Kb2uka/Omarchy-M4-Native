#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Add E-cluster states up to 2172 MHz and a CPU die thermal zone to the J713 CPU temperature DTB."""
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

# The CPU temperature DTB (docs/j713-cpu-temperature.md).
BASE_SHA256 = '08e8e719381f88a1e50d821c145dad5b2d97fd772609e13d7778e19795e9cad1'
E_OPP = '/opp-table-0'
P_OPP = '/opp-table-1'
HWMON = '/soc/smc@38c600000/hwmon'
# One-based E state -> MHz, ADT voltage-states1-sram (docs/j713-cpu-idle.md).
E_STATES = {2: 1080, 3: 1476, 4: 1860, 5: 2172}
E_CPUS = [f'/cpus/cpu@{n}' for n in range(6)]
P_CPUS = [f'/cpus/cpu@1010{n}' for n in range(4)]
# Policy CPUs by their phandle in the base: Linux CPU 0 (P) and CPU 1 (E).
P_POLICY, E_POLICY = ('/cpus/cpu@10100', 7), ('/cpus/cpu@0', 1)
SENSOR_CHANNEL, SENSOR_KEY = 6, b'TCMz\0'
TRIP_MILLI_C, HYSTERESIS_MILLI_C, POLL_MS = 95000, 5000, 1000
ZONE = '/thermal-zones/cpu-die-thermal'
NO_LIMIT = 0xffffffff


def cells(*values):
    return struct.pack(f'>{len(values)}I', *values)


def phandles(nodes):
    return {struct.unpack('>I', props['phandle'])[0]: path
            for path, props in nodes.items() if 'phandle' in props}


def temperature_channels(nodes):
    """SMC hwmon temperature children in devicetree order, which is the driver's channel order."""
    prefix = HWMON + '/temperature-'
    return [path for path in nodes if path.startswith(prefix) and path.count('/') == HWMON.count('/') + 1]


def expected_nodes(old):
    """Return the exact tree the overlay must produce."""
    expected = {path: dict(props) for path, props in old.items()}
    top = max(phandles(old))
    sensor, trip = top + 1, top + 2
    for state, mhz in E_STATES.items():
        expected[f'{E_OPP}/opp{state:02d}'] = {'opp-hz': struct.pack('>Q', mhz * 1000000),
                                               'opp-level': cells(state)}
    expected[HWMON].update({'#thermal-sensor-cells': cells(1), 'phandle': cells(sensor)})
    for cpu in E_CPUS + P_CPUS:
        expected[cpu]['#cooling-cells'] = cells(2)
    expected['/thermal-zones'] = {}
    expected[ZONE] = {'polling-delay-passive': cells(POLL_MS), 'polling-delay': cells(POLL_MS),
                      'thermal-sensors': cells(sensor, SENSOR_CHANNEL)}
    expected[ZONE + '/trips'] = {}
    expected[ZONE + '/trips/cpu-die-passive'] = {
        'temperature': cells(TRIP_MILLI_C), 'hysteresis': cells(HYSTERESIS_MILLI_C),
        'type': b'passive\0', 'phandle': cells(trip)}
    expected[ZONE + '/cooling-maps'] = {}
    for name, (_, handle) in (('p', P_POLICY), ('e', E_POLICY)):
        expected[f'{ZONE}/cooling-maps/map-{name}-cluster'] = {
            'trip': cells(trip), 'cooling-device': cells(handle, NO_LIMIT, NO_LIMIT)}
    return expected


def check_base(old):
    board = old['/']['compatible'].split(b'\0')
    require(b'apple,j713' in board and b'apple,t8132' in board, 'Wrong board')
    e_table = sorted(path for path in old if path.startswith(E_OPP + '/'))
    require(e_table == [E_OPP + '/opp01'] and
            old[E_OPP + '/opp01']['opp-hz'] == struct.pack('>Q', 900000000), 'Unexpected E OPP table')
    handles = {struct.unpack('>I', old[E_OPP]['phandle'])[0]: 'e', struct.unpack('>I', old[P_OPP]['phandle'])[0]: 'p'}
    for cpu in E_CPUS + P_CPUS:
        require(cpu in old and '#cooling-cells' not in old[cpu], 'Unexpected CPU node ' + cpu)
        table = handles.get(struct.unpack('>I', old[cpu]['operating-points-v2'])[0])
        require(table == ('e' if cpu in E_CPUS else 'p'), 'CPU without its cluster OPP table: ' + cpu)
    by_handle = phandles(old)
    for path, handle in (P_POLICY, E_POLICY):
        require(by_handle.get(handle) == path, 'Policy CPU phandle moved: ' + path)
    require(old.get(HWMON, {}).get('compatible') == b'apple,smc-hwmon\0', 'Unexpected SMC hwmon node')
    require('phandle' not in old[HWMON] and '#thermal-sensor-cells' not in old[HWMON],
            'SMC hwmon node already referenced')
    channels = temperature_channels(old)
    require(len(channels) > SENSOR_CHANNEL and
            old[channels[SENSOR_CHANNEL]].get('apple,key-id') == SENSOR_KEY,
            'CPU Die Max is not temperature channel %d' % SENSOR_CHANNEL)
    require(not any(path == '/thermal-zones' or path.startswith('/thermal-zones/') for path in old),
            'Thermal zones already exist')


def build(base, output, overlay, dtc='dtc', fdtoverlay='fdtoverlay'):
    require(base.is_file() and not base.is_symlink(), 'Base must be a regular file')
    original = base.read_bytes()
    require(sha(original) == BASE_SHA256, 'Unqualified CPU temperature base DTB')
    require(overlay.is_file() and not overlay.is_symlink(), 'Overlay must be a regular file')
    require(not output.exists() and not output.is_symlink(), 'Output must not exist')
    require(output.parent.is_dir(), 'Output directory must exist')
    old, reservations, cpu = tree(original)
    check_base(old)
    with tempfile.TemporaryDirectory(prefix='j713-ecluster-', dir=output.parent) as scratch:
        compiled = Path(scratch) / 'ecluster.dtbo'
        candidate = Path(scratch) / 'candidate.dtb'
        # The cooling maps name base CPU nodes by phandle number, which dtc cannot resolve in an overlay.
        subprocess.run([dtc, '-W', 'no-cooling_device_property', '-I', 'dts', '-O', 'dtb',
                        '-o', str(compiled), str(overlay)], check=True)
        subprocess.run([fdtoverlay, '-i', str(base), '-o', str(candidate), str(compiled)], check=True)
        data = candidate.read_bytes()
        new, new_reservations, new_cpu = tree(data)
        require(new_reservations == reservations and new_cpu == cpu and new == expected_nodes(old),
                'Candidate changed data beyond the E-cluster states and the CPU thermal zone')
        require(base.read_bytes() == original, 'Base changed during build')
        with output.open('xb') as stream:
            stream.write(data)
    return {'base_sha256': sha(original), 'output_sha256': sha(data), 'output_bytes': len(data),
            'overlay_sha256': sha(overlay.read_bytes()),
            'e_states_mhz': [900] + list(E_STATES.values()), 'p_states_changed': False,
            'thermal_zone': {'sensor': 'TCMz', 'channel': SENSOR_CHANNEL,
                             'passive_trip_c': TRIP_MILLI_C / 1000, 'hysteresis_c': HYSTERESIS_MILLI_C / 1000,
                             'polling_ms': POLL_MS, 'critical_trip': None},
            'other_properties_reservations_and_boot_cpu_unchanged': True, 'kernel_changed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--overlay', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'patches/j713-ecluster-thermal.dtso')
    args = parser.parse_args()
    print(json.dumps(build(args.base, args.output, args.overlay), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
