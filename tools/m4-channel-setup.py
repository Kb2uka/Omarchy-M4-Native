#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Connect an installed J713 alpha.1 Air to the signed M4 package channel.

Trusts the channel's signing key, adds the [omarchy-m4] repository in front of
the distribution repositories, then installs the channel packages from it.
Only the channel's own database is refreshed, so this is not a general system
update. The boot.bin hook then keeps the installed boot object in step; on an
Air that already has update 1 it changes nothing. Inspection is the default;
--apply makes the changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

REPOSITORY = 'omarchy-m4'
SERVER = 'https://github.com/Kb2uka/Omarchy-M4-Native/releases/download/packages'
FINGERPRINT = '578DF78177CF6DF29B888B8C9404F3CC90797A50'
PUBLIC_KEY = '''\
-----BEGIN PGP PUBLIC KEY BLOCK-----

mDMEas2BAxYJKwYBBAHaRw8BAQdAiBuujG2cS3qh2JVnz+AN7D6UJU6b4AdarwMc
pg1W96+0P09tYXJjaHkgTTQgTmF0aXZlIHBhY2thZ2Ugc2lnbmluZyAoS0IyVUtB
KSA8a2IydWthODBAZ21haWwuY29tPoiQBBMWCgA4FiEEV433gXfPbfKbiIuMlATz
zJB5elAFAmrNgQMCGwMFCwkIBwIGFQoJCAsCBBYCAwECHgECF4AACgkQlATzzJB5
elBimQEA+Km/UWABthdAlyL1C6VsVQXpIUWOhZx05gfh8BbmLewA/0deC8xDxBDj
ACjuCm7/wS3BkXKT/XBLOSU61X3JF2UI
=FvsK
-----END PGP PUBLIC KEY BLOCK-----
'''
PACKAGES = ('omarchy-m4-keyring', 'omarchy-m4-boot', 'linux-m4-omarchy')
BOARD = b'apple,j713'
CONF = 'etc/pacman.conf'
SNIPPET = 'etc/pacman.d/omarchy-m4.conf'
BACKUP = '.pre-omarchy-m4'
MANIFESTS = ('etc/omarchy/j713-video-manifest.json', 'etc/omarchy/j713-audio-manifest.json')
COMPATIBLE = 'sys/firmware/devicetree/base/compatible'
BOOT = 'boot/efi/omarchy/boot.bin'


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def snippet(server):
    return f'[{REPOSITORY}]\nSigLevel = Required DatabaseRequired\nServer = {server}\n'


def include_line(root):
    return 'Include = ' + str(root / SNIPPET)


def with_include(text, line):
    """Return pacman.conf text with the snippet included above [core], or None if present."""
    lines = text.splitlines(keepends=True)
    if any(entry.strip() == line for entry in lines):
        return None
    cores = [index for index, entry in enumerate(lines) if entry.strip() == '[core]']
    require(len(cores) == 1, 'pacman.conf must have exactly one [core] section')
    sections = [entry.strip() for entry in lines[:cores[0]] if entry.lstrip().startswith('[')]
    require(sections == ['[options]'], 'pacman.conf has repositories before [core]; add the channel by hand')
    require(not any(entry.strip() == f'[{REPOSITORY}]' for entry in lines),
            f'pacman.conf already defines [{REPOSITORY}] directly')
    lines.insert(cores[0], line + '\n\n')
    return ''.join(lines)


def write(path, text, mode):
    temp = path.with_name(path.name + '.new')
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    with temp.open('x') as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(temp, mode)
    os.replace(temp, path)


class Machine:
    """Runs pacman for the real system, or for a test root with its own database."""

    def __init__(self, root, output=sys.stdout):
        self.root = root
        self.output = output
        self.sandbox = root != Path('/')

    def pacman(self, *args, config=None):
        command = ['pacman']
        if self.sandbox:
            command += ['--root', str(self.root), '--dbpath', str(self.root / 'var/lib/pacman'),
                        '--gpgdir', str(self.root / 'etc/pacman.d/gnupg'),
                        '--cachedir', str(self.root / 'var/cache/pacman/pkg'),
                        '--logfile', str(self.root / 'var/log/pacman.log'),
                        '--hookdir', str(self.root / 'etc/pacman.d/hooks')]
        command += ['--config', str(config or self.root / CONF), *args]
        if self.sandbox and args[0] == '-S':
            command.append('--noscriptlet')
        return self.run(command)

    def pacman_key(self, *args):
        command = ['pacman-key', '--config', str(self.root / CONF)]
        if self.sandbox:
            command += ['--gpgdir', str(self.root / 'etc/pacman.d/gnupg')]
        return self.run(command + list(args))

    def boot_status(self):
        """Return the composer's verdict on the installed boot.bin."""
        command = [sys.executable, str(self.root / 'usr/bin/m4-boot-compose'), '--root', str(self.root)]
        if not self.sandbox:
            command = ['/usr/bin/m4-boot-compose']
        print('+ ' + ' '.join(command), file=self.output, flush=True)
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        print(result.stdout + result.stderr, end='', file=self.output, flush=True)
        try:
            return json.loads(result.stdout)['status'] if result.returncode == 0 else 'refused'
        except (ValueError, KeyError):
            return 'unknown'

    def run(self, command):
        print('+ ' + ' '.join(command), file=self.output, flush=True)
        return subprocess.run(command, check=False).returncode


def boot_digest(root):
    path = root / BOOT
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def preflight(root, machine):
    compatible = root / COMPATIBLE
    require(compatible.is_file() and BOARD in compatible.read_bytes().split(b'\0'),
            'This is not a J713 MacBook Air')
    for name in MANIFESTS:
        require((root / name).is_file(), 'Not an installed alpha.1 system: missing /' + name)
    require((root / CONF).is_file(), 'Missing /' + CONF)
    require(machine.pacman('-Q', 'linux-m4-omarchy') == 0,
            'The alpha.1 kernel package linux-m4-omarchy is not installed')


def setup(root, apply=False, server=SERVER, key=PUBLIC_KEY, fingerprint=FINGERPRINT, output=sys.stdout):
    machine = Machine(root, output)
    preflight(root, machine)
    conf = root / CONF
    text = conf.read_text()
    updated = with_include(text, include_line(root))
    wanted = snippet(server)
    current = (root / SNIPPET).read_text() if (root / SNIPPET).is_file() else None
    plan = {'trust_key': fingerprint, 'snippet': str(root / SNIPPET), 'snippet_changes': current != wanted,
            'pacman_conf_changes': updated is not None, 'install': list(PACKAGES), 'applied': False}
    if not apply:
        plan['status'] = 'ready; rerun with --apply'
        return plan
    require(machine.sandbox or os.geteuid() == 0, 'Run with sudo')
    before = boot_digest(root)
    with tempfile.TemporaryDirectory(prefix='omarchy-m4-setup-') as scratch:
        scratch = Path(scratch)
        key_file = scratch / 'omarchy-m4.asc'
        key_file.write_text(key)
        require(machine.pacman_key('--add', str(key_file)) == 0, 'pacman-key could not add the channel key')
        require(machine.pacman_key('--lsign-key', fingerprint) == 0,
                'pacman-key could not trust the channel key ' + fingerprint)
        if current != wanted:
            write(root / SNIPPET, wanted, 0o644)
        if updated is not None:
            backup = conf.with_name(conf.name + BACKUP)
            if not backup.exists():
                write(backup, text, conf.stat().st_mode & 0o777)
            write(conf, updated, conf.stat().st_mode & 0o777)
        # Refresh only the channel database; the distribution databases stay as they are.
        only = scratch / 'pacman-omarchy-m4.conf'
        only.write_text('[options]\nArchitecture = auto\nSigLevel = Required DatabaseRequired\n' +
                        include_line(root) + '\n')
        require(machine.pacman('-Sy', config=only) == 0, 'Could not download the M4 package database')
        require(machine.pacman('-S', '--needed', '--noconfirm', *PACKAGES) == 0,
                'Package installation failed; boot.bin is only changed by a successful composer run')
    boot = machine.boot_status()
    require(boot == 'current', 'The packages are installed, but boot.bin is not in step with them '
            f'({boot}); boot.bin keeps booting what it booted before. See the message above')
    restart = boot_digest(root) != before
    plan.update(applied=True, boot='current', restart_needed=restart,
                status=('connected; restart once to boot the updated boot.bin' if restart else
                        'connected; future M4 fixes arrive with omarchy update'))
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--apply', action='store_true', help='make the changes (default: inspect only)')
    parser.add_argument('--root', type=Path, default=Path('/'), help=argparse.SUPPRESS)
    parser.add_argument('--server', default=SERVER, help=argparse.SUPPRESS)
    parser.add_argument('--key-file', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--fingerprint', default=FINGERPRINT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    key = args.key_file.read_text() if args.key_file else PUBLIC_KEY
    try:
        result = setup(args.root, args.apply, args.server, key, args.fingerprint)
    except (Refused, OSError) as error:
        print('omarchy-m4-setup: refused: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
