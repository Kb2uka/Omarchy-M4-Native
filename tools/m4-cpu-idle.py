#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Load the J713 CPU idle driver (omarchy-m4-cpu, #65).

m4-cpu-idle.service runs this with --boot. It loads j713_cpuidle, which lets
idle cores power down instead of spinning under idle=nop. Only the pinned
module, only on the pinned kernel and board, and only while no other CPU idle
driver is registered.

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
"""
import argparse
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


def inspect(root):
    compatible = root / COMPATIBLE
    release = root / OSRELEASE
    staged = root / STAGED
    data = staged.read_bytes() if staged.is_file() and not staged.is_symlink() else None
    report = {
        'board': compatible.is_file() and BOARD in compatible.read_bytes().split(b'\0'),
        'kernel': release.is_file() and release.read_text().strip() == RELEASE,
        'staged': data is not None and len(data) == PIN['size'] and sha(data) == PIN['sha256'],
        'loaded': (root / 'sys/module' / NAME).exists(),
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
    return report


def settle(root, seconds, sleep):
    """Wait while the cores power down through the driver; the marker stays until this returns."""
    before = power_downs(root)
    sleep(seconds)
    after = power_downs(root)
    require(before is not None and after is not None and after > before,
            'The driver loaded but no core powered down through it')


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
        settle(root, settle_seconds, sleep)
    finally:
        # The boot lived through the settle period, so the driver did not take it down.
        remove_state(root, 'attempt')
    return 'loaded; idle cores power down'


def run_command(command):
    return subprocess.run(command, check=False).returncode


def run(root, mode, runner=run_command, settle_seconds=SETTLE_SECONDS, sleep=time.sleep):
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
            report = dict(inspect(root), status='current')
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
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    mode = next((name for name in ('boot', 'start', 'disable', 'enable') if getattr(args, name)), 'inspect')
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
