#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Luthfi Hizb
"""Load the J713 headphone jack modules (omarchy-m4-headphones).

The drop-in on m4-j713-audio-register.service runs this with --boot once the
speaker registration has succeeded: the overlay points at nodes that the
registration creates. It loads j713_hp_dt (the CS42L84 codec, the cluster 2
power domain and the card node), then snd_soc_j713_headphones (the
J713Headphones card: headphone playback and the headset microphone). The stock
snd-soc-cs42l84 codec driver autoloads from the new node. Only the pinned
modules, only on the pinned kernel and board.

One strike: an attempt marker is written durably before the first module loads
and removed once both have loaded. If a boot dies while loading, the next boot
finds the marker and loads nothing, because the Air has no boot menu to recover
from. --start loads the modules in the running session instead (and clears a
marker left by such a boot). Inspection is the default.

Off switch: --disable writes a hold file, and no boot or --start loads the
modules (headphones and headset mic alike) until --enable removes it. Modules
already loaded stay until the next restart. The package stays installed, so
omarchy-m4-update keeps it current.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

RELEASE = '7.1.9-m4-gpu-backport-20261008'
BOARD = b'apple,j713'
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
OSRELEASE = 'proc/sys/kernel/osrelease'
STAGED = 'usr/lib/omarchy-m4/j713-headphones'
STATE = 'var/lib/omarchy-m4/headphones'
# Load order matters: the card driver binds to the node the overlay adds.
MODULES = (
    ('j713_hp_dt', 'j713_hp_dt.ko',
     {'sha256': '3f501c6193aeddc62031395e899909bf352851278f829458998ca1c4dbbe6149', 'size': 10656}),
    ('snd_soc_j713_headphones', 'snd-soc-j713-headphones.ko',
     {'sha256': '2e743937b4bd8315ec6dba6bef4bbfbf197d4bf6f8688761954579192df48244', 'size': 48160}),
)
# modinfo depends of snd-soc-j713-headphones.ko; insmod does not resolve them.
DEPENDS = ('snd-soc-core', 'snd-pcm', 'snd-pcm-dmaengine')


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


def mark(root):
    """Write the attempt marker and make it durable before any module loads."""
    state = root / STATE
    state.mkdir(parents=True, exist_ok=True)
    fd = os.open(state / 'attempt', os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(b'loading the headphone modules\n')
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(state)


def unmark(root):
    state = root / STATE
    if (state / 'attempt').exists():
        (state / 'attempt').unlink()
        sync_directory(state)


def loaded(root, name):
    return (root / 'sys/module' / name).exists()


def inspect(root):
    compatible = root / COMPATIBLE
    release = root / OSRELEASE
    report = {
        'board': compatible.is_file() and BOARD in compatible.read_bytes().split(b'\0'),
        'kernel': release.is_file() and release.read_text().strip() == RELEASE,
        'staged': {}, 'loaded': {}, 'attempt_marker': (root / STATE / 'attempt').exists(),
        'held': (root / STATE / 'hold').exists(),
    }
    for name, filename, pin in MODULES:
        path = root / STAGED / filename
        data = path.read_bytes() if path.is_file() and not path.is_symlink() else None
        report['staged'][name] = data is not None and len(data) == pin['size'] and sha(data) == pin['sha256']
        report['loaded'][name] = loaded(root, name)
    if all(report['loaded'].values()):
        report['state'] = 'loaded'
    elif any(report['loaded'].values()):
        report['state'] = 'partial'
    elif report['attempt_marker']:
        report['state'] = 'struck'
    else:
        report['state'] = 'not loaded'
    return report


def hold(root, held):
    """Turn the off switch on (write the hold file) or off (remove it), durably."""
    state = root / STATE
    path = state / 'hold'
    if held:
        state.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'the headphone modules stay unloaded until sudo m4-headphones --enable\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(state)
    elif path.exists() or path.is_symlink():
        path.unlink()
        sync_directory(state)


def load(root, report, run):
    require(report['board'], 'This is not a J713 MacBook Air')
    require(report['kernel'], 'The running kernel is not ' + RELEASE)
    for name, filename, _ in MODULES:
        require(report['staged'][name], f'Staged {filename} is missing or differs from the pin')
    mark(root)
    failed = None
    if run(['modprobe', '-a', *DEPENDS]) != 0:
        failed = 'the dependencies ' + ', '.join(DEPENDS)
    for name, filename, _ in MODULES:
        if failed is None and not loaded(root, name) and run(['insmod', str(root / STAGED / filename)]) != 0:
            failed = filename
    # The load returned, failed or not, so it did not take the boot down with it.
    unmark(root)
    require(failed is None, f'Loading {failed} failed; see journalctl -k -b for the reason')
    return 'loaded'


def run_command(command):
    return subprocess.run(command, check=False).returncode


def run(root, mode, runner=run_command):
    if mode in ('disable', 'enable'):
        hold(root, mode == 'disable')
    report = inspect(root)
    if mode == 'disable':
        report['status'] = ('off; the loaded modules stay until the next restart'
                            if report['state'] in ('loaded', 'partial') else 'off; nothing loads at boot')
        return report
    if mode in ('boot', 'start'):
        require(not report['held'],
                'The headphone jack is switched off. Run sudo m4-headphones --enable to switch it back on')
        require(mode == 'start' or not report['attempt_marker'],
                'A previous boot stopped while loading the headphone modules, so this boot loads none. '
                'Run sudo m4-headphones --start to try again in the running session')
        if report['state'] == 'loaded':
            report['status'] = 'current'
        else:
            status = load(root, report, runner) + '; the J713Headphones card is starting'
            report = dict(inspect(root), status=status)
    else:  # inspect or enable
        report['status'] = {
            'loaded': 'current',
            'partial': 'only some modules are loaded; restart to start the card cleanly',
            'struck': 'a boot stopped while loading; nothing loads at boot until sudo m4-headphones --start',
            'not loaded': 'loads at the next boot',
        }[report['state']]
        if report['held']:
            report['status'] = 'off; nothing loads at boot until sudo m4-headphones --enable' + (
                '; the loaded modules stay until the next restart' if report['state'] in ('loaded', 'partial') else '')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--boot', action='store_true',
                        help='load the modules; only m4-j713-audio-register.service runs this')
    action.add_argument('--start', action='store_true', help='load the modules now, in the running session')
    action.add_argument('--disable', action='store_true',
                        help='switch the headphone jack off: nothing loads at boot until --enable')
    action.add_argument('--enable', action='store_true', help='switch the headphone jack back on')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    mode = next((name for name in ('boot', 'start', 'disable', 'enable') if getattr(args, name)), 'inspect')
    if args.root == Path('/') and mode != 'inspect' and os.geteuid() != 0:
        print('m4-headphones: run with sudo', file=sys.stderr)
        return 1
    try:
        report = run(args.root, mode)
    except (Refused, OSError, ValueError) as error:
        print('m4-headphones: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
