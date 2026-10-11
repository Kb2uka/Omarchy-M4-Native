#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
"""Consume a previously authorized owner's pending Widevine setup request."""
import argparse
import fcntl
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys

SETUP = Path(__file__).resolve().with_name('setup-brave-widevine.py')
spec = importlib.util.spec_from_file_location('owner_widevine_setup', SETUP)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


def bootstrap(profile):
    setup.require(os.getuid() != 0 and os.getuid() == os.geteuid(),
                  'Run as the desktop owner, without sudo')
    home = Path.home()
    marker = home / '.config/omarchy/pending' / ('widevine-' + profile + '.json')
    if not marker.exists() and not marker.is_symlink():
        return 0
    raw = setup.read_file(marker, home, 4096)
    expected = {'schema': 1, 'profile': profile, 'consent': 'owner-requested'}
    request = setup.json_value(raw)
    setup.require(request == expected and type(request.get('schema')) is int,
                  'Pending request lacks exact prior owner consent')
    lock = marker.parent / '.widevine-setup.lock'
    setup.owned(lock, home, missing=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        setup.require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                      and not info.st_mode & 0o022, 'Unsafe setup lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not marker.exists() and not marker.is_symlink():
            return 0
        setup.require(setup.read_file(marker, home, 4096) == raw, 'Pending request changed')
        # The child checks closure before any download and again before profile writes.
        # Its 15-second transfer budget is enclosed by this 20-second child lifetime.
        result = subprocess.run([sys.executable, str(SETUP), '--profile', profile,
                                 '--initialize', '--timeout', '15'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        setup.require(result.returncode == 0, 'Owner setup did not complete (exit ' +
                      str(result.returncode) + ')')
        setup.require(setup.read_file(marker, home, 4096) == raw, 'Pending request changed')
        marker.unlink()
        return 0
    finally:
        os.close(fd)


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True, choices=setup.PROFILES)
    args = parser.parse_args(arguments)
    try:
        return bootstrap(args.profile)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print('Widevine setup remains pending: ' + str(error) +
              '. Continue opening the browser; retry on a later closed-browser launch. '
              'For details, run setup-brave-widevine.py --profile ' + args.profile +
              ' --initialize --timeout 15 while Brave is closed.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
