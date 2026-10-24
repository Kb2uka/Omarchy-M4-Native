#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Stop J713 speaker protection before sleep and start it again after resume.

A frozen protection daemon cannot renew its lease, so the kernel cuts the
speakers off and only a reboot brings them back. Before sleep this stops the
user audio stack first, so the speaker output (TX) closes, then stops
speakersafetyd-j713, so the speaker group returns to an ownerless, verified
LOW. After resume it starts protection again only from that LOW; the
protection service's own readiness check still decides, and the WirePlumber
gate still holds user audio until protection is ready.

It never clears a fault, a lapsed lease or a failed transport. If the group is
not at a clean LOW after resume, protection is left off until a reboot, as
before. Each step is logged; a failed step never blocks sleep.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import time

PROTECTION = 'speakersafetyd-j713.service'
GROUP = 'sys/bus/platform/devices/speaker-group/status'
TX = 'proc/asound/AppleJ713/pcm0p/sub0/status'
RUN_USER = 'run/user'
STATE = 'run/omarchy-m4-audio-sleep/state.json'
# Sockets first, so nothing restarts PipeWire while it stops.
USER_UNITS = ('pipewire.socket', 'pipewire-pulse.socket', 'wireplumber.service',
              'pipewire-pulse.service', 'pipewire.service')
# The fields protection's readiness check requires of an ownerless LOW.
CLEAN_LOW = {'stable': '1', 'phase': '0', 'owner': '0', 'cancelled': '1', 'faulted': '0', 'removing': '0',
             'error': '0', 'gpio_error': '0', 'gpio_low_verified': '1', 'deadline_ns': '0', 'profile': '1'}
TX_CLOSE_SECONDS = 5
# Every systemctl call is bounded, so a hung call is logged as a failure and the
# next step still runs. pre (TimeoutStartSec=60): 5 + users * (5 + 10) + 5 + 15,
# 55 s for two logged-in users. post (TimeoutStopSec=180): protection and the
# WirePlumber readiness gate each allow 65 s to start, 150 s for one user.
QUERY_SECONDS = 5
USER_STOP_SECONDS = 10
PROTECTION_STOP_SECONDS = 15
START_SECONDS = 75


def log(message):
    print(message, file=sys.stderr, flush=True)


def systemctl(*args, timeout):
    command = ('systemctl', *args)
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(command, 124, '', f'timed out after {timeout} s')


def group(root):
    try:
        text = (root / GROUP).read_text()
    except OSError:
        return None
    return dict(token.split('=', 1) for token in text.split() if '=' in token)


def clean_low(fields):
    return fields is not None and all(fields.get(k) == v for k, v in CLEAN_LOW.items())


def tx_closed(root):
    try:
        return (root / TX).read_text().strip() == 'closed'
    except OSError:
        return False


def users(root):
    """Names of users whose systemd user manager is running."""
    found = []
    base = root / RUN_USER
    if not base.is_dir():
        return found
    for entry in sorted(base.iterdir(), key=lambda p: p.name):
        if not entry.name.isdigit() or not (entry / 'systemd' / 'private').exists():
            continue
        try:
            found.append(pwd.getpwuid(int(entry.name)).pw_name)
        except KeyError:
            continue
    return found


def user_systemctl(user, *args, timeout):
    return systemctl('--user', '--machine', user + '@.host', *args, timeout=timeout)


def active_units(user):
    result = user_systemctl(user, 'is-active', *USER_UNITS, timeout=QUERY_SECONDS)
    states = result.stdout.split()
    return [unit for unit, state in zip(USER_UNITS, states) if state == 'active']


def write_state(root, state):
    path = root / STATE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(state, sort_keys=True) + '\n')
    os.replace(temporary, path)


def read_state(root):
    path = root / STATE
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    path.unlink(missing_ok=True)
    return state


def wait(predicate, seconds, interval=.1):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


def before_sleep(root):
    state = {'protection': False, 'users': {}}
    if group(root) is None:
        log('No J713 speaker group; nothing to do')
        write_state(root, state)
        return state
    if systemctl('is-active', '--quiet', PROTECTION, timeout=QUERY_SECONDS).returncode != 0:
        log('Speaker protection is not running; leaving it off')
        write_state(root, state)
        return state
    for user in users(root):
        units = active_units(user)
        if units:
            state['users'][user] = units
    # Record before stopping anything, so resume restores whatever was stopped
    # even if a later step fails or this run is cut short.
    state['protection'] = True
    write_state(root, state)
    for user, units in state['users'].items():
        result = user_systemctl(user, 'stop', *units, timeout=USER_STOP_SECONDS)
        log(f'Stopped user audio for {user}: {" ".join(units)}'
            + ('' if result.returncode == 0 else f' (systemctl: {result.stderr.strip()})'))
    if not wait(lambda: tx_closed(root), TX_CLOSE_SECONDS):
        log('Speaker output is still open; protection cannot return to LOW and stays off until a reboot')
    result = systemctl('stop', PROTECTION, timeout=PROTECTION_STOP_SECONDS)
    if result.returncode != 0:
        log(f'Stopping speaker protection failed: {result.stderr.strip()}')
    fields = group(root)
    if clean_low(fields):
        log('Speaker group is at a verified ownerless LOW')
    else:
        log('Speaker group did not return to a clean LOW; speakers stay off until a reboot: '
            + json.dumps(fields, sort_keys=True))
    return state


def after_resume(root):
    state = read_state(root)
    if not state:
        log('No sleep record; nothing to restore')
        return 0
    status = 0
    if state.get('protection'):
        fields = group(root)
        if not clean_low(fields):
            log('Speaker group is not at a clean LOW after resume; speakers stay off until a reboot: '
                + json.dumps(fields, sort_keys=True))
            status = 1
        elif not tx_closed(root):
            log('Speaker output opened while protection was off; speakers stay off until a reboot')
            status = 1
        else:
            result = systemctl('start', PROTECTION, timeout=START_SECONDS)
            if result.returncode == 0:
                log('Speaker protection is ready again')
            else:
                log(f'Speaker protection did not start: {result.stderr.strip()}')
                status = 1
    for user, units in sorted(state.get('users', {}).items()):
        units = [unit for unit in units if unit in USER_UNITS]
        result = user_systemctl(user, 'start', *units, timeout=START_SECONDS)
        log(f'Started user audio for {user}: {" ".join(units)}'
            + ('' if result.returncode == 0 else f' (systemctl: {result.stderr.strip()})'))
    return status


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('phase', choices=('pre', 'post'))
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.geteuid() != 0:
        log('Run as root')
        return 2
    if args.phase == 'pre':
        before_sleep(args.root)
        return 0
    return after_resume(args.root)


if __name__ == '__main__':
    sys.exit(main())
