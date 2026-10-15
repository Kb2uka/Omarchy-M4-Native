#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Load the J713 CPU idle driver and P-cluster boost guard (omarchy-m4-cpu, #65, #66).

m4-cpu-idle.service runs this with --boot. It loads j713_cpuidle, which lets
idle cores power down instead of spinning under idle=nop. Only the pinned
module, only on the pinned kernel and board, and only while no other CPU idle
driver is registered.

Then, when the devicetree carries the closed boost gate, it loads j713_pguard,
which caps the P cluster from the CPU die temperature and opens the gate
itself (docs/j713-cpu-boost.md). A guard that is missing, differs from its pin
or fails to load leaves the gate closed and P at 1800 MHz; CPU idle still
loads. With boost on, real-time tasks get the 1800 MHz floor they had before
instead of the cluster maximum (RT_FLOOR), and the guard has raised the CPU
die zone's sustained trip from 95 C to 100 C.

One strike: an attempt marker is written durably before the module loads and
removed only after the cores have idled through it for a settle period. A
lost-state fault shows within milliseconds of a core's first power-down, so if
a boot dies while loading or settling, the next boot finds the marker and loads
nothing, because the Air has no boot menu to recover from. --start loads the
driver in the running session instead (and clears a marker left by such a
boot). Inspection is the default.

Off switch: --disable writes a hold file, and no boot or --start loads the
driver until --enable removes it. A loaded driver stays until the next restart:
the kernel pins an idle driver's module while it is registered. The package
stays installed, so omarchy-m4-update keeps it current.

Boost alone: --boost off closes the gate now, holding P at 1800 MHz, puts the
sustained trip back to 95 C, and no boot loads the guard until --boost on, which reopens the gate at once when the
guard is loaded and otherwise at the next boot. The guard cannot be unloaded.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

RELEASE = '7.1.9-m4-gpu-backport-20261008'
BOARD = b'apple,j713'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
OSRELEASE = 'proc/sys/kernel/osrelease'
CURRENT_DRIVER = 'sys/devices/system/cpu/cpuidle/current_driver'
CPUS = 'sys/devices/system/cpu'
STAGED = 'usr/lib/omarchy-m4/cpu/j713_cpuidle.ko'
STATE = 'var/lib/omarchy-m4/cpu-idle'
NAME, DRIVER = 'j713_cpuidle', 'j713_idle'
PIN = {'sha256': 'a2a7947a1d2260abcfc6eb10d7d63643ad2e191e73e93fedddf605116cdfd5a9', 'size': 12680}
GUARD_STAGED = 'usr/lib/omarchy-m4/cpu/j713_pguard.ko'
GUARD_NAME = 'j713_pguard'
GUARD_PIN = {'sha256': '91fe247e7e83d30f62f35bcf64ddbcb42faa3a1c109881d5aefcb64dc7e5e691', 'size': 39696}
# The boost gate: the passive trip on the CPU die zone that the boost devicetree
# sets to 20 C (closed) and j713_pguard raises to 150 C (open).
THERMAL = 'sys/class/thermal'
GATE_ZONE = 'cpu-die-thermal'
GATE = {'20000': 'closed', '150000': 'open'}
# The zone's sustained passive trip: 95 C from the devicetree, 100 C while the guard runs.
SUSTAIN = {'95000': 'normal', '100000': 'boost'}
# schedutil runs a real-time task at its uclamp minimum, by default the policy
# maximum. Speaker protection is real-time and wakes a P core every few ms, so
# with boost that held the idle P cluster above 3 GHz half the time (+0.7 W).
# This floor maps to 4464000 * 412 / 1024 = 1796062 kHz, which resolves to P4,
# 1800 MHz, the real-time clock before boost. On an E core (capacity 417) it
# resolves to E7, the E maximum, as the kernel default (1024) does. A floor
# already at or below 412 is left alone; a higher one is saved and restored
# when boost is switched off.
RT_FLOOR_KNOB = 'proc/sys/kernel/sched_util_clamp_min_rt_default'
RT_FLOOR = 412
# The power-down state is state1 on every CPU the driver serves.
POWER_DOWN = 'cpuidle/state1'
SETTLE_SECONDS = 120


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sync_directory(path):
    directory = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Locked:
    """Hold the loader lock, so a --start cannot clear the marker of a boot that is still settling."""

    def __init__(self, root):
        self.state = root / STATE

    def __enter__(self):
        self.state.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(self.state / 'lock', os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            raise Refused('Another m4-cpu-idle run is loading or settling the driver; try again in two minutes')
        return self

    def __exit__(self, *exc):
        os.close(self.fd)


def write_state(root, name, text):
    """Write a state file and make it durable."""
    state = root / STATE
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(text.encode())
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(state)


def remove_state(root, name):
    state = root / STATE
    path = state / name
    if path.exists() or path.is_symlink():
        path.unlink()
        sync_directory(state)


def read(path):
    try:
        return path.read_text().strip()
    except OSError:
        return None


def power_downs(root):
    """Total power-down entries across all CPUs, or None when the driver serves none."""
    counts = [read(cpu / POWER_DOWN / 'usage') for cpu in (root / CPUS).glob('cpu[0-9]*')]
    counts = [int(count) for count in counts if count is not None and count.isdigit()]
    return sum(counts) if counts else None


def pinned(path, pin):
    data = path.read_bytes() if path.is_file() and not path.is_symlink() else None
    return data is not None and len(data) == pin['size'] and sha(data) == pin['sha256']


def zone_trip(root, states):
    """The CPU die zone's passive trip whose temperature is one of states: (file, state), or (None, 'absent')."""
    for zone in sorted((root / THERMAL).glob('thermal_zone*')):
        if read(zone / 'type') != GATE_ZONE:
            continue
        for kind in sorted(zone.glob('trip_point_*_type')):
            temp = kind.with_name(kind.name[:-len('type')] + 'temp')
            if read(kind) == 'passive' and read(temp) in states:
                return temp, states[read(temp)]
    return None, 'absent'


def gate_trip(root):
    """The boost gate's trip temperature file and state ('closed' or 'open'), or (None, 'absent')."""
    return zone_trip(root, GATE)


def boost_state(report):
    if report['guard_loaded'] and report['gate'] == 'open':
        return 'on'
    if report['boost_held']:
        return 'off; P stays at 1800 MHz until sudo m4-cpu-idle --boost on'
    if report['guard_loaded']:
        return 'off; the gate was closed by hand; sudo m4-cpu-idle --boost on reopens it'
    if report['gate'] == 'absent':
        return 'waits for the boost devicetree; restart after omarchy-m4-update'
    return 'starts with the CPU idle driver at the next boot'


def inspect(root):
    compatible = root / COMPATIBLE
    release = root / OSRELEASE
    report = {
        'board': compatible.is_file() and BOARD in compatible.read_bytes().split(b'\0'),
        'kernel': release.is_file() and release.read_text().strip() == RELEASE,
        'staged': pinned(root / STAGED, PIN),
        'loaded': (root / 'sys/module' / NAME).exists(),
        'guard_staged': pinned(root / GUARD_STAGED, GUARD_PIN),
        'guard_loaded': (root / 'sys/module' / GUARD_NAME).exists(),
        'boost_held': (root / STATE / 'boost-hold').exists(),
        'gate': gate_trip(root)[1],
        'sustained_trip': zone_trip(root, SUSTAIN)[1],
        'rt_floor': read(root / RT_FLOOR_KNOB),
        'driver': read(root / CURRENT_DRIVER),
        'power_downs': power_downs(root),
        'attempt_marker': (root / STATE / 'attempt').exists(),
        'held': (root / STATE / 'hold').exists(),
    }
    if report['loaded'] and report['driver'] == DRIVER:
        report['state'] = 'loaded'
    elif report['loaded']:
        report['state'] = 'partial'
    elif report['attempt_marker']:
        report['state'] = 'struck'
    else:
        report['state'] = 'not loaded'
    report['boost'] = boost_state(report)
    return report


def settle(root, seconds, sleep):
    """Wait while the cores power down through the driver; the marker stays until this returns."""
    before = power_downs(root)
    sleep(seconds)
    after = power_downs(root)
    require(before is not None and after is not None and after > before,
            'The driver loaded but no core powered down through it')


def load_guard(root, report, run):
    """Load the boost guard; called with the attempt marker written. It opens the gate itself."""
    if report['guard_loaded']:
        return None
    if report['gate'] == 'open' or report['sustained_trip'] == 'boost':
        # Changed by hand, with no guard behind them: back to the devicetree's trips, so P is held
        # at 1800 MHz and the zone at 95 C, and the guard finds the trips it raises itself.
        set_boost_trips(root, False)
    if report['boost_held']:
        return None
    if report['gate'] == 'absent':
        return 'boost waits for the boost devicetree'
    if not report['guard_staged']:
        return 'boost left off: staged j713_pguard.ko is missing or differs from the pin'
    if run(['insmod', str(root / GUARD_STAGED)]) != 0:
        return 'boost left off: loading j713_pguard.ko failed; see journalctl -k -b'
    lower_rt_floor(root)
    return 'boost guard loaded'


def joined(*parts):
    return '; '.join(part for part in parts if part)


def load(root, report, run, settle_seconds, sleep):
    require(report['board'], 'This is not a J713 MacBook Air')
    require(report['kernel'], 'The running kernel is not ' + RELEASE)
    require(report['staged'], 'Staged j713_cpuidle.ko is missing or differs from the pin')
    require(report['driver'] in (None, 'none'),
            f"Another CPU idle driver ({report['driver']}) is registered; leaving it in charge")
    write_state(root, 'attempt', 'loading the CPU idle driver\n')
    if run(['insmod', str(root / STAGED)]) != 0:
        # The load returned, so it did not take the boot down with it.
        remove_state(root, 'attempt')
        raise Refused('Loading j713_cpuidle.ko failed; see journalctl -k -b for the reason')
    try:
        # The guard needs the idle driver in charge, and its load shares the attempt window.
        boost = load_guard(root, inspect(root), run)
        settle(root, settle_seconds, sleep)
    finally:
        # The boot lived through the settle period, so neither module took it down.
        remove_state(root, 'attempt')
    return joined('loaded; idle cores power down', boost)


def start_guard(root, report, run, settle_seconds, sleep):
    """In a running session with the idle driver loaded, load the guard under the attempt marker."""
    if report['guard_loaded'] or report['boost_held'] or report['gate'] == 'absent':
        return None
    write_state(root, 'attempt', 'loading the P-cluster boost guard\n')
    try:
        boost = load_guard(root, report, run)
        if boost == 'boost guard loaded':
            settle(root, settle_seconds, sleep)
    finally:
        remove_state(root, 'attempt')
    return boost


def lower_rt_floor(root):
    """With boost on, cap the real-time uclamp floor at RT_FLOOR, saving a higher value to restore."""
    knob = root / RT_FLOOR_KNOB
    now = read(knob)
    if now is None or not now.isdigit():
        return  # a kernel without uclamp has no knob, and that is fine
    if int(now) > RT_FLOOR:
        write_state(root, 'rt-floor', now + '\n')
        knob.write_text(f'{RT_FLOOR}\n')
    elif int(now) < RT_FLOOR:
        # The owner's own floor is lower: nothing was replaced, so nothing to restore.
        remove_state(root, 'rt-floor')
    # At RT_FLOOR already, from an earlier boost on or set so by the owner: keep any value saved
    # when this lowered it, so --boost off can still restore it.


def restore_rt_floor(root):
    """Put back the floor lower_rt_floor replaced, unless something else changed it since."""
    saved = read(root / STATE / 'rt-floor')
    knob = root / RT_FLOOR_KNOB
    if saved is not None and saved.isdigit() and read(knob) == str(RT_FLOOR):
        knob.write_text(saved + '\n')
    remove_state(root, 'rt-floor')


def set_trip(root, states, state):
    """Write one of the zone's trips to the temperature of state; nothing when absent or already there."""
    trip, now = zone_trip(root, states)
    if trip is None or now == state:
        return False
    value = next(temp for temp, name in states.items() if name == state)
    trip.write_text(value + '\n')
    return True


def set_gate(root, state):
    """Write the gate's trip; the guard's cap bounds the cluster whenever it is open."""
    return set_trip(root, GATE, state)


def set_boost_trips(root, on):
    """Boost on: gate open, sustained trip 100 C (only behind a loaded guard). Off: gate closed, 95 C."""
    set_gate(root, 'open' if on else 'closed')
    set_trip(root, SUSTAIN, 'boost' if on else 'normal')


def boost(root, mode):
    if mode == 'boost-off':
        write_state(root, 'boost-hold', 'the boost guard stays unloaded until sudo m4-cpu-idle --boost on\n')
        set_boost_trips(root, False)
        restore_rt_floor(root)
    else:
        remove_state(root, 'boost-hold')
        report = inspect(root)
        if report['guard_loaded']:
            set_boost_trips(root, True)
            lower_rt_floor(root)
    report = inspect(root)
    report['status'] = 'boost ' + report['boost']
    return report


def run_command(command):
    return subprocess.run(command, check=False).returncode


def run(root, mode, runner=run_command, settle_seconds=SETTLE_SECONDS, sleep=time.sleep):
    if mode == 'inspect':
        return change(root, mode, runner, settle_seconds, sleep)
    with Locked(root):
        return change(root, mode, runner, settle_seconds, sleep)


def change(root, mode, runner, settle_seconds, sleep):
    if mode in ('boost-off', 'boost-on'):
        return boost(root, mode)
    if mode == 'disable':
        write_state(root, 'hold', 'the CPU idle driver stays unloaded until sudo m4-cpu-idle --enable\n')
    elif mode == 'enable':
        remove_state(root, 'hold')
    report = inspect(root)
    if mode == 'disable':
        report['status'] = ('off; the loaded driver stays until the next restart'
                            if report['state'] in ('loaded', 'partial') else 'off; nothing loads at boot')
        return report
    if mode in ('boot', 'start'):
        require(not report['held'],
                'CPU idle is switched off. Run sudo m4-cpu-idle --enable to switch it back on')
        require(mode == 'start' or not report['attempt_marker'],
                'A previous boot stopped while loading the CPU idle driver, so this boot loads none. '
                'Run sudo m4-cpu-idle --start to try again in the running session')
        if report['state'] == 'loaded':
            remove_state(root, 'attempt')
            boost_note = start_guard(root, report, runner, settle_seconds, sleep) if mode == 'start' else None
            report = dict(inspect(root), status=joined('current', boost_note))
        else:
            status = load(root, report, runner, settle_seconds, sleep)
            report = dict(inspect(root), status=status)
    else:  # inspect or enable
        report['status'] = {
            'loaded': 'current',
            'partial': 'the module is loaded but another idle driver is in charge',
            'struck': 'a boot stopped while loading; nothing loads at boot until sudo m4-cpu-idle --start',
            'not loaded': 'loads at the next boot',
        }[report['state']]
        if report['held']:
            report['status'] = 'off; nothing loads at boot until sudo m4-cpu-idle --enable' + (
                '; the loaded driver stays until the next restart' if report['state'] in ('loaded', 'partial') else '')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--boot', action='store_true', help='load the driver; only m4-cpu-idle.service runs this')
    action.add_argument('--start', action='store_true', help='load the driver now, in the running session')
    action.add_argument('--disable', action='store_true',
                        help='switch CPU idle off: nothing loads at boot until --enable')
    action.add_argument('--enable', action='store_true', help='switch CPU idle back on')
    action.add_argument('--boost', choices=('on', 'off'),
                        help='off: hold P at 1800 MHz now and at every boot; on: allow boost again')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    mode = next((name for name in ('boot', 'start', 'disable', 'enable') if getattr(args, name)), 'inspect')
    if args.boost:
        mode = 'boost-' + args.boost
    if args.root == Path('/') and mode != 'inspect' and os.geteuid() != 0:
        print('m4-cpu-idle: run with sudo', file=sys.stderr)
        return 1
    try:
        report = run(args.root, mode)
    except (Refused, OSError, ValueError) as error:
        print('m4-cpu-idle: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
