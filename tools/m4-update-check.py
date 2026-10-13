#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Tell the desktop user when the [omarchy-m4] repository has newer M4 packages.

Read-only and unprivileged: downloads the channel's package list, compares it
with the installed M4 packages and, with --notify, shows one desktop
notification per new set of versions. Installing still happens only through
omarchy-m4-update, where pacman checks every signature.
"""
import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import urllib.error
import urllib.request

SNIPPET = Path('/etc/pacman.d/omarchy-m4.conf')
REPOSITORY = 'omarchy-m4'
TITLE = 'M4 update available'


def server(snippet):
    for line in snippet.read_text().splitlines():
        key, _, value = line.partition('=')
        if key.strip() == 'Server' and value.strip().startswith('https://'):
            return value.strip()
    return None


def available(data):
    """Return {name: version} from a repository database archive."""
    packages = {}
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive:
            if member.isfile() and member.name.endswith('/desc'):
                fields, key = {}, None
                for line in archive.extractfile(member).read().decode().splitlines():
                    if line.startswith('%') and line.endswith('%'):
                        key = line
                    elif line and key and key not in fields:
                        fields[key] = line
                if '%NAME%' in fields and '%VERSION%' in fields:
                    packages[fields['%NAME%']] = fields['%VERSION%']
    return packages


def installed(names, dbpath=None):
    command = ['pacman', '-Q'] + (['--dbpath', str(dbpath)] if dbpath else []) + sorted(names)
    result = subprocess.run(command, capture_output=True, text=True)
    versions = {}
    for line in result.stdout.splitlines():
        name, _, version = line.partition(' ')
        if name in names and version:
            versions[name] = version
    return versions


def newer(candidate, current):
    result = subprocess.run(['vercmp', candidate, current], capture_output=True, text=True, check=True)
    return int(result.stdout.strip()) > 0


def updates(data, dbpath=None):
    offered = available(data)
    current = installed(set(offered), dbpath)
    return sorted(f'{name} {offered[name]}' for name, version in current.items()
                  if newer(offered[name], version))


def notify(found, state):
    """Notify once per distinct set of updates."""
    text = '\n'.join(found) + '\n'
    if state.is_file() and state.read_text() == text:
        return False
    if shutil.which('notify-send'):
        subprocess.run(['notify-send', '--app-name=M4 updates', TITLE,
                        '\n'.join(found) + '\nRun omarchy-m4-update, then restart.'], check=False)
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(text)
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--notify', action='store_true', help='show a desktop notification')
    parser.add_argument('--snippet', type=Path, default=SNIPPET, help=argparse.SUPPRESS)
    parser.add_argument('--dbpath', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--state', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    base = os.environ.get('XDG_STATE_HOME') or str(Path.home() / '.local/state')
    state = args.state or Path(base) / 'omarchy-m4/notified'
    url = server(args.snippet) if args.snippet.is_file() else None
    if url is None:
        print('Not connected to the M4 package channel.')
        return 0
    try:
        with urllib.request.urlopen(f'{url}/{REPOSITORY}.db', timeout=60) as response:
            data = response.read()
        found = updates(data, args.dbpath)
    except (OSError, urllib.error.URLError, tarfile.TarError, subprocess.CalledProcessError, ValueError) as error:
        print('Could not check the M4 package channel: ' + str(error), file=sys.stderr)
        return 0
    if not found:
        print('M4 packages are up to date.')
        state.unlink(missing_ok=True)
        return 0
    print(TITLE + ':\n' + '\n'.join(found))
    if args.notify:
        notify(found, state)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
