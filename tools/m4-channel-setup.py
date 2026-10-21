#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Connect an installed J713 alpha.1 Air to the signed M4 package channel.

Trusts the channel's signing key, adds the [omarchy-m4] repository in front of
the distribution repositories, then installs the channel packages from it.
Only the channel's own database is refreshed, so this is not a general system
update. An installed package newer than the channel copy is left alone;
naming it on pacman -S --needed would downgrade it, because libalpm skips
only an equal version. The boot.bin hook keeps the installed boot object in
step with the current installed package composition. A coherent boot hold
is success and stays held: a reboot does not apply those boot packages.
Another component can still need its own restart. Inspection is the default;
--apply makes the changes.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
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
SPEAKER_ROLLBACK = 'var/lib/omarchy-m4/speaker-module/rollback'
SPEAKER_NAME = 'snd_soc_j713_group'
SPEAKER_STOCK = {'sha256': '465a88aa9f70ceac8682499ad6bd9ecd34a33b35a3b5c556b42f7bd1524272d0', 'size': 52088,
                 'note': '040000001400000003000000474e5500f2078435f633f3e320dc95119734cfd263d6edd9'}
SPEAKER_FIXED = {'sha256': 'aaad802eb136ff6cb915feaa123001b3eb848bc2f038d0a5b71c8e2ff35d2581', 'size': 52000,
                 'note': '040000001400000003000000474e55009e792aa072fe3a8c75354bcc631fae233c7c014b'}
MCA_STAGED = 'usr/lib/omarchy-m4/j713-audio/snd-soc-apple-mca.ko'
MCA_MODULE = 'usr/lib/omarchy/j713-audio/7.1.9-m4-gpu-backport-20261008/modules/snd-soc-apple-mca.ko'
MCA_HOLD = 'var/lib/omarchy-m4/mca-module/hold'
MCA_ROLLBACK = 'var/lib/omarchy-m4/mca-module/rollback'
MCA_NAME = 'snd_soc_apple_mca'
MCA_STOCK = {'sha256': 'd3194660a28d30ba8ea296b6592836ad4ca9426d9471d05ba8ce3cad284baa1e', 'size': 79152,
             'note': '040000001400000003000000474e550057b999a68c50407955e8b2ed92f242b0cfe426e9'}
MCA_FIXED = {'sha256': 'fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454', 'size': 79152,
             'note': '040000001400000003000000474e55000a48e413fcbb318d62c1161c59fb2bef1de7df81'}
GPU_STAGED = 'usr/lib/omarchy-m4/gpu/asahi_m4.ko'
GPU_MODULE = 'opt/m4-gpu/asahi_m4.ko'
GPU_HOLD = 'var/lib/omarchy-m4/gpu-module/hold'
GPU_STOCK = '80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578'
HEADPHONES_STAGED = 'usr/lib/omarchy-m4/j713-headphones/snd-soc-j713-headphones.ko'
HEADPHONES_STATE = 'var/lib/omarchy-m4/headphones'
CPU_IDLE_STAGED = 'usr/lib/omarchy-m4/cpu/j713_cpuidle.ko'
CPU_GUARD_STAGED = 'usr/lib/omarchy-m4/cpu/j713_pguard.ko'
CPU_IDLE_STATE = 'var/lib/omarchy-m4/cpu-idle'
BACKLIGHT_STAGED = 'usr/lib/omarchy-m4/backlight/j713_dcp_backlight.ko'
BACKLIGHT_STATE = 'var/lib/omarchy-m4/backlight'
PACKAGES = ('omarchy-m4-keyring', 'omarchy-m4-boot', 'linux-m4-omarchy', 'omarchy-m4-gst-audio', 'omarchy-m4-speaker',
            'omarchy-m4-gpu', 'omarchy-m4-headphones', 'omarchy-m4-cpu', 'omarchy-m4-evdi', 'omarchy-m4-backlight')
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
# Composer HELD_STATUS. Success, and not a promise that reboot applies the hold.
HELD_BOOT = ('held; boot updates remain held until '
             'sudo m4-boot-compose --resume-updates --apply')


class Refused(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def snippet(server):
    return f'[{REPOSITORY}]\nSigLevel = Required DatabaseRequired\nServer = {server}\n'


def include_line(root):
    return 'Include = ' + str(root / SNIPPET)


def targets_to_install(packages, installed, upgradable):
    """Literal channel names to pass to pacman -S, in request order.

    A name is included when it is not installed, or when pacman -Qu lists it.
    An equal or newer installed name is omitted: --needed skips only an equal
    version, so naming a newer install would downgrade it to the channel copy.
    These names have no provider or replacement alias. A different package that
    merely provides one of them is not that package.
    """
    have = set(installed)
    stale = set(upgradable)
    return [name for name in packages if name not in have or name in stale]


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

    def captured(self, *args):
        """Run pacman and keep both streams. The full config is the -S config."""
        command = self.pacman_command(*args)
        print('+ ' + ' '.join(command), file=self.output, flush=True)
        return subprocess.run(command, check=False, capture_output=True, text=True,
                              encoding='utf-8', errors='replace')

    def install_state(self):
        """Installed names (-Qq) and upgradable names (-Quq) after the channel refresh.

        -Qq must succeed. -Quq exits 1 with empty output when nothing is
        upgradable; a database error also exits 1, but it prints a diagnostic,
        and that is a failure. Other statuses and query diagnostics fail closed.
        """
        installed = self.quiet_query('-Qq', 'Could not read installed packages', False)
        upgradable = self.quiet_query('-Quq', 'Could not read package updates', True)
        return installed, upgradable

    def quiet_query(self, operation, message, allow_empty_exit):
        result = self.captured(operation)
        stdout = result.stdout or ''
        stderr = result.stderr or ''
        no_updates = allow_empty_exit and result.returncode == 1 and stdout == '' and stderr == ''
        clean = result.returncode == 0 and stderr == '' and all(
            name and name.split() == [name] for name in stdout.splitlines())
        if not (no_updates or clean):
            self.show_query(stdout, stderr)
            require(False, message)
        return [] if no_updates else stdout.splitlines()

    def show_query(self, stdout, stderr):
        for text in (stdout, stderr):
            if text:
                print(text, end='' if text.endswith('\n') else '\n', file=self.output, flush=True)

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
        after an earlier refusal, when --needed installs nothing and no hook runs,
        and when every requested package is already equal or newer.
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


def audio_release(module_rel):
    """Kernel release named by this component's installed module path."""
    marker = '/j713-audio/'
    try:
        start = module_rel.index(marker) + len(marker)
        return module_rel[start:module_rel.index('/', start)]
    except ValueError:
        return None


def audio_digest(path):
    """SHA-256 of one regular file. A symlink, directory, or unreadable path is None."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def audio_expected_pins(identity):
    """The seven values one sha256/size/note identity must match, or None."""
    if not isinstance(identity, dict):
        return None
    try:
        sha256, size, note = identity['sha256'], identity['size'], identity['note']
    except KeyError:
        return None
    return {'files': sha256, 'metadata_sha256': sha256, 'metadata_size': size,
            'artifact_sha256': sha256, 'artifact_size': size, 'artifact_note': note,
            'modules': note}


def audio_selected_pins(document, module_rel, name):
    """Seven pins for one component, or None when a selected map is missing or not a dict.

    The values are the files hash, metadata hash and size, artifact hash, size,
    and note, and the modules note. Other fields are ignored. Missing maps and
    nested values of the wrong type return None instead of raising.
    """
    if not isinstance(document, dict):
        return None
    files = document.get('files')
    metadata_root = document.get('file_metadata')
    artifacts = document.get('module_artifacts')
    modules = document.get('modules')
    if not all(isinstance(item, dict) for item in (files, metadata_root, artifacts, modules)):
        return None
    metadata = metadata_root.get(module_rel)
    artifact = artifacts.get(name)
    if not isinstance(metadata, dict) or not isinstance(artifact, dict) or name not in modules:
        return None
    return {'files': files.get('/' + module_rel),
            'metadata_sha256': metadata.get('sha256'), 'metadata_size': metadata.get('size'),
            'artifact_sha256': artifact.get('sha256'), 'artifact_size': artifact.get('size'),
            'artifact_note': artifact.get('note'), 'modules': modules.get(name)}


def audio_json_document(path):
    """Parse one regular JSON file. A symlink or unreadable file is None."""
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        return None


def audio_pins_match(path, module_rel, name, identity, release=None):
    """True when a regular manifest carries this identity's seven pins.

    When release is not None, kernel_release must equal it. Backup validation
    passes None and does not require a release. Wrong or missing nested data
    is false.
    """
    expected = audio_expected_pins(identity)
    document = audio_json_document(path)
    if expected is None or not isinstance(document, dict):
        return False
    if release is not None and document.get('kernel_release') != release:
        return False
    return audio_selected_pins(document, module_rel, name) == expected


def audio_module_identity(path, identity):
    """True when path is a regular file with this identity's SHA-256 and length."""
    if audio_expected_pins(identity) is None or path.is_symlink() or not path.is_file():
        return False
    try:
        data = path.read_bytes()
    except OSError:
        return False
    return hashlib.sha256(data).hexdigest() == identity.get('sha256') and len(data) == identity.get('size')


def audio_known_state(root, module_rel, name, stock, fixed):
    """stock, forward, current, reverse, or None.

    The live module hash selects stock or fixed bytes. All seven pins must
    match that identity, and the manifest release must be the release in the
    module path. Another digest, another path, a wrong release, or a missing
    or non-regular module is unknown.
    """
    release = audio_release(module_rel)
    installed = audio_digest(root / module_rel)
    stock_sha = stock.get('sha256') if isinstance(stock, dict) else None
    fixed_sha = fixed.get('sha256') if isinstance(fixed, dict) else None
    if installed is not None and installed == stock_sha:
        installed_kind = 'stock'
    elif installed is not None and installed == fixed_sha:
        installed_kind = 'fixed'
    else:
        return None
    if not release:
        return None
    manifest = root / MANIFESTS[1]
    if installed_kind == 'stock' and audio_pins_match(manifest, module_rel, name, stock, release):
        return 'stock'
    if installed_kind == 'fixed' and audio_pins_match(manifest, module_rel, name, stock, release):
        return 'forward'
    if installed_kind == 'fixed' and audio_pins_match(manifest, module_rel, name, fixed, release):
        return 'current'
    if installed_kind == 'stock' and audio_pins_match(manifest, module_rel, name, fixed, release):
        return 'reverse'
    return None


def audio_stock_backup(root, state_rel, module_rel, name, stock):
    """True when both stock backup halves are regular and match the stock identity.

    The module half must have the stock hash and length. The manifest half
    must carry the seven stock pins. It does not have to name the live release.
    This reads; it does not restore or write state.
    """
    state = root / state_rel
    if not audio_module_identity(state / 'alpha1-module.ko', stock):
        return False
    return audio_pins_match(state / 'alpha1-manifest.json', module_rel, name, stock)


def rollback_marker_kind(path):
    """Classify one rollback marker without opening it.

    Same rule as m4-speaker-module.rollback_marker_kind. regular is a real
    regular file. absent means nothing is at this path. uncertain is any
    symlink, including a dangling one, or any other non-regular node such as
    a directory or fifo. lstat does not follow a symlink or block on a fifo.
    """
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return 'absent'
    except OSError:
        return 'uncertain'
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        return 'uncertain'
    return 'regular'


def audio_pending(root, staged_rel, module_rel, hold_rel, rollback_rel, name, stock, fixed):
    """Whether this component has recognized pending boot work.

    Stock can apply when the staged file is the fixed hash and size. A forward
    interruption can complete when the stock backup pair matches too. A regular
    rollback marker in a recognized state is pending, ahead of hold, when that
    same backup matches, including stock left behind by an interrupted marker
    cleanup. The backup is the stock module hash and length plus the seven
    stock pins, with no release requirement. A symlink, including a dangling
    one, or any other non-regular rollback marker is not pending: it is not
    rollback authorization and it is not forward work. Current, reverse without
    that marker, hold without a marker, and anything unrecognized are not
    pending. A recognized result is not a promise that every later boot write
    succeeds.
    """
    state = audio_known_state(root, module_rel, name, stock, fixed)
    if state is None:
        return False
    rollback = root / rollback_rel
    marker_kind = rollback_marker_kind(rollback)
    if marker_kind == 'uncertain':
        return False
    if marker_kind == 'regular':
        return audio_stock_backup(root, Path(hold_rel).parent, module_rel, name, stock)
    if (root / hold_rel).exists() or state in ('current', 'reverse'):
        return False
    if state == 'forward' and not audio_stock_backup(root, Path(hold_rel).parent, module_rel, name, stock):
        return False
    return audio_module_identity(root / staged_rel, fixed)


def speaker_pending(root):
    """Whether the speaker module has recognized pending boot work."""
    return audio_pending(root, SPEAKER_STAGED, SPEAKER_MODULE, SPEAKER_HOLD, SPEAKER_ROLLBACK,
                         SPEAKER_NAME, SPEAKER_STOCK, SPEAKER_FIXED)


def mca_pending(root):
    """Whether the MCA module has recognized pending boot work.

    Independent of the speaker paths, hold, rollback marker, and identities.
    """
    return audio_pending(root, MCA_STAGED, MCA_MODULE, MCA_HOLD, MCA_ROLLBACK,
                         MCA_NAME, MCA_STOCK, MCA_FIXED)


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


def backlight_pending(root):
    state = root / BACKLIGHT_STATE
    return ((root / BACKLIGHT_STAGED).is_file() and (root / 'sys/module').is_dir() and
            not (root / 'sys/module/j713_dcp_backlight').exists() and
            not (state / 'hold').exists() and not (state / 'attempt').exists())


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
            # Same full config as -S, and only after this refresh. -Q is the local
            # database; -Qu is that database compared with the sync databases.
            installed_names, upgradable_names = machine.install_state()
            selected = targets_to_install(packages, installed_names, upgradable_names)
            plan['install'] = selected
            # An empty -S is not a transaction. Dependencies of a selected name
            # are still resolved by pacman, including ones this list omits.
            if selected:
                require(machine.pacman('-S', '--needed', '--noconfirm', *selected) == 0,
                        'Package installation failed')
        except (Refused, OSError) as error:
            # A configured but unreachable repository would stop every later system update.
            write(conf, text, conf.stat().st_mode & 0o777)
            if current is None:
                (root / SNIPPET).unlink(missing_ok=True)
            elif current != wanted:
                write(root / SNIPPET, current, conf.stat().st_mode & 0o777)
            raise Refused(str(error) + '; pacman.conf was restored and the boot update may be partial. '
                          'Inspect m4-boot-compose and resume the pending operation it reports '
                          'after fixing the cause, then rerun omarchy-m4-update') from error
    boot = machine.compose()
    require(boot in ('current', 'updated; takes effect at next boot', HELD_BOOT),
            'The packages are installed, but the boot update needs attention: boot.bin is not in step with them '
            f'({boot}). See the detailed message above, then inspect m4-boot-compose and resume '
            'the pending operation it reports after fixing the cause, then rerun omarchy-m4-update')
    machine.start_notifier()
    restart = (boot_digest(root) != before or speaker_pending(root) or mca_pending(root) or
               gpu_pending(root) or headphones_pending(root) or cpu_idle_pending(root) or
               backlight_pending(root))
    if boot == HELD_BOOT and restart:
        text = 'connected; restart once to apply the other update; boot updates remain held'
    elif boot == HELD_BOOT:
        text = 'connected; boot updates remain held'
    elif restart:
        text = 'connected; restart once to apply the update'
    else:
        text = 'connected; future M4 fixes arrive with omarchy-m4-update'
    plan.update(applied=True, boot=boot, restart_needed=restart, status=text)
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
