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
import fcntl
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
LOCK = 'run/lock/omarchy-m4-setup.lock'
SPEAKER_STAGED = 'usr/lib/omarchy-m4/j713-audio/snd-soc-j713-group.ko'
SPEAKER_MODULE = 'usr/lib/omarchy/j713-audio/7.1.9-m4-gpu-backport-20261008/modules/snd-soc-j713-group.ko'
SPEAKER_HOLD = 'var/lib/omarchy-m4/speaker-module/hold'
GPU_STAGED = 'usr/lib/omarchy-m4/gpu/asahi_m4.ko'
GPU_MODULE = 'opt/m4-gpu/asahi_m4.ko'
GPU_HOLD = 'var/lib/omarchy-m4/gpu-module/hold'
GPU_STOCK = '80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578'
HEADPHONES_STAGED = 'usr/lib/omarchy-m4/j713-headphones/snd-soc-j713-headphones.ko'
HEADPHONES_STATE = 'var/lib/omarchy-m4/headphones'
CPU_IDLE_STAGED = 'usr/lib/omarchy-m4/cpu/j713_cpuidle.ko'
CPU_GUARD_STAGED = 'usr/lib/omarchy-m4/cpu/j713_pguard.ko'
CPU_IDLE_STATE = 'var/lib/omarchy-m4/cpu-idle'
PACKAGES = ('omarchy-m4-keyring', 'omarchy-m4-boot', 'linux-m4-omarchy', 'omarchy-m4-gst-audio', 'omarchy-m4-speaker',
            'omarchy-m4-gpu', 'omarchy-m4-headphones', 'omarchy-m4-cpu', 'omarchy-m4-evdi')
SINKS = 'omarchy-m4-gst-audio'  # gst-plugins-good already has both sinks and conflicts with it
BOARD = b'apple,j713'
CONF = 'etc/pacman.conf'
SNIPPET = 'etc/pacman.d/omarchy-m4.conf'
GNUPG = 'etc/pacman.d/gnupg'
# Public keyring material a normal user must read to verify sync databases,
# as on stock Arch (directory 755, these files 644). Private material is
# never touched: anything else under GNUPG keeps its mode.
PUBLIC_KEYRING_FILES = ('pubring.gpg', 'pubring.kbx', 'trustdb.gpg')
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


def make_keyring_readable(root):
    """Let normal users verify sync databases (issue #71, stock Arch layout).

    alpha.1 ships /etc/pacman.d/gnupg mode 700 root, so with DatabaseRequired
    every user-level pacman run prints keyring errors. Relax only the
    directory and the public database files; private keys keep their modes.
    """
    directory = root / GNUPG
    require(directory.is_dir() and not directory.is_symlink(), 'Missing /' + GNUPG)
    # Intended world-readable modes (stock Arch layout), not a leak: the
    # directory must be traversable and these database files readable for
    # user-level signature verification; private keys below keep their modes.
    os.chmod(directory, 0o755)
    for name in PUBLIC_KEYRING_FILES:
        path = directory / name
        if path.is_file() and not path.is_symlink():
            os.chmod(path, 0o644)


class Machine:
    """Runs pacman for the real system, or for a test root with its own database."""

    def __init__(self, root, output=sys.stderr):
        self.root = root
        self.output = output
        self.sandbox = root != Path('/')

    def pacman(self, *args, config=None):
        command = self.pacman_command(*args, config=config)
        if self.sandbox and args[0] == '-S':
            command.append('--noscriptlet')
        return self.run(command)

    def pacman_command(self, *args, config=None):
        command = ['pacman']
        if self.sandbox:
            command += ['--root', str(self.root), '--dbpath', str(self.root / 'var/lib/pacman'),
                        '--gpgdir', str(self.root / 'etc/pacman.d/gnupg'),
                        '--cachedir', str(self.root / 'var/cache/pacman/pkg'),
                        '--logfile', str(self.root / 'var/log/pacman.log'),
                        '--hookdir', str(self.root / 'etc/pacman.d/hooks')]
        return command + ['--config', str(config or self.root / CONF), *args]

    def installed(self, name):
        """Whether a package is installed, without pacman's 'not found' error on screen."""
        command = self.pacman_command('-Q', name)
        return subprocess.run(command, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

    def pacman_key(self, *args):
        command = ['pacman-key', '--config', str(self.root / CONF)]
        if self.sandbox:
            command += ['--gpgdir', str(self.root / 'etc/pacman.d/gnupg')]
        return self.run(command + list(args))

    def compose(self):
        """Run the composer as the hook would and return its status.

        The hook already ran when a package changed; this also completes a retry
        after an earlier refusal, when --needed installs nothing and no hook runs.
        """
        command = [sys.executable, str(self.root / 'usr/bin/m4-boot-compose'), '--root', str(self.root)]
        if not self.sandbox:
            command = ['/usr/bin/m4-boot-compose']
        command.append('--apply')
        print('+ ' + ' '.join(command), file=self.output, flush=True)
        result = subprocess.run(command, check=False, capture_output=True, text=True)
        print(result.stdout + result.stderr, end='', file=self.output, flush=True)
        try:
            return json.loads(result.stdout)['status'] if result.returncode == 0 else 'refused'
        except (ValueError, KeyError):
            return 'unknown'

    def start_notifier(self):
        """Start the daily check now for the person running sudo; otherwise it starts at next login."""
        user = os.environ.get('SUDO_USER')
        if self.sandbox or not user or user == 'root':
            return
        self.run(['systemctl', '--user', '--machine', user + '@.host', 'start', 'omarchy-m4-update-check.timer'])

    def run(self, command):
        # Progress goes to stderr; stdout carries only the JSON result.
        print('+ ' + ' '.join(command), file=self.output, flush=True)
        return subprocess.run(command, check=False, stdout=sys.stderr).returncode


def speaker_pending(root):
    """Whether omarchy-m4-speaker staged a module that the next boot will swap in."""
    staged, module = root / SPEAKER_STAGED, root / SPEAKER_MODULE
    if not staged.is_file() or not module.is_file() or (root / SPEAKER_HOLD).exists():
        return False
    return hashlib.sha256(module.read_bytes()).digest() != hashlib.sha256(staged.read_bytes()).digest()


def gpu_pending(root):
    """Whether omarchy-m4-gpu staged a module that the next boot will swap in for alpha.1's."""
    staged, module = root / GPU_STAGED, root / GPU_MODULE
    if not staged.is_file() or not module.is_file() or (root / GPU_HOLD).exists():
        return False
    return hashlib.sha256(module.read_bytes()).hexdigest() == GPU_STOCK


def headphones_pending(root):
    """Whether omarchy-m4-headphones staged a card that the next boot will load.

    Only on a running system (sys/module present), never while switched off
    (hold) or after a boot that died while loading (attempt).
    """
    state = root / HEADPHONES_STATE
    return ((root / HEADPHONES_STAGED).is_file() and (root / 'sys/module').is_dir() and
            not (root / 'sys/module/snd_soc_j713_headphones').exists() and
            not (state / 'hold').exists() and not (state / 'attempt').exists())


def cpu_idle_pending(root):
    """Whether omarchy-m4-cpu staged an idle driver or boost guard that the next boot will load.

    Only on a running system (sys/module present), never while switched off
    (hold; boost-hold for the guard) or after a boot that died while loading
    (attempt).
    """
    state = root / CPU_IDLE_STATE
    if not (root / 'sys/module').is_dir() or (state / 'hold').exists() or (state / 'attempt').exists():
        return False
    idle = (root / CPU_IDLE_STAGED).is_file() and not (root / 'sys/module/j713_cpuidle').exists()
    guard = ((root / CPU_GUARD_STAGED).is_file() and not (root / 'sys/module/j713_pguard').exists() and
             not (state / 'boost-hold').exists())
    return idle or guard


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


def setup(root, apply=False, server=SERVER, key=PUBLIC_KEY, fingerprint=FINGERPRINT, output=sys.stderr):
    if not apply:
        return connect(root, False, server, key, fingerprint, output)
    # One run at a time: the first-boot service, a pasted connect line and
    # omarchy-m4-update must not restore pacman.conf underneath each other.
    lock_path = root / LOCK
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return connect(root, True, server, key, fingerprint, output)


def connect(root, apply, server, key, fingerprint, output):
    machine = Machine(root, output)
    preflight(root, machine)
    packages = [p for p in PACKAGES if p != SINKS or not machine.installed('gst-plugins-good')]
    conf = root / CONF
    text = conf.read_text()
    updated = with_include(text, include_line(root))
    wanted = snippet(server)
    current = (root / SNIPPET).read_text() if (root / SNIPPET).is_file() else None
    plan = {'trust_key': fingerprint, 'snippet': str(root / SNIPPET), 'snippet_changes': current != wanted,
            'pacman_conf_changes': updated is not None, 'install': packages, 'applied': False}
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
        make_keyring_readable(root)
        plan['keyring_readable'] = True
        try:
            if current != wanted:
                write(root / SNIPPET, wanted, conf.stat().st_mode & 0o777)  # readable like pacman.conf
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
            require(machine.pacman('-S', '--needed', '--noconfirm', *packages) == 0, 'Package installation failed')
        except (Refused, OSError) as error:
            # A configured but unreachable repository would stop every later system update.
            write(conf, text, conf.stat().st_mode & 0o777)
            if current is None:
                (root / SNIPPET).unlink(missing_ok=True)
            elif current != wanted:
                write(root / SNIPPET, current, conf.stat().st_mode & 0o777)
            raise Refused(str(error) + '; pacman.conf was restored and boot.bin was not changed') from error
    boot = machine.compose()
    require(boot in ('current', 'updated; takes effect at next boot'),
            'The packages are installed, but boot.bin is not in step with them '
            f'({boot}); boot.bin keeps booting what it booted before. See the message above, '
            'fix the cause, then run this again')
    machine.start_notifier()
    restart = (boot_digest(root) != before or speaker_pending(root) or gpu_pending(root) or
               headphones_pending(root) or cpu_idle_pending(root))
    plan.update(applied=True, boot=boot, restart_needed=restart,
                status=('connected; restart once to apply the update' if restart else
                        'connected; future M4 fixes arrive with omarchy-m4-update'))
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
