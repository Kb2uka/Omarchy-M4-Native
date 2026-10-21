# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Channel setup: trust the key, add the repository first, install without a system upgrade.

The fake base kernel is already current unless a test marks it upgradable. A
newer installed channel package is not named on the install transaction.
"""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_channel_setup', ROOT / 'tools/m4-channel-setup.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())

# Synthetic module bytes. Records are built from these, not from production constants.
SPEAKER_STOCK_BYTES = b'speaker-stock-module'
SPEAKER_FIXED_BYTES = b'speaker-fixed-module'
MCA_STOCK_BYTES = b'mca-stock-module'
MCA_FIXED_BYTES = b'mca-fixed-module'
SPEAKER_STOCK_NOTE = 'speaker-stock-note'
SPEAKER_FIXED_NOTE = 'speaker-fixed-note'
MCA_STOCK_NOTE = 'mca-stock-note'
MCA_FIXED_NOTE = 'mca-fixed-note'
SPEAKER_COMPONENT = 'snd_soc_j713_group'
MCA_COMPONENT = 'snd_soc_apple_mca'
# component, kind, production constant, bytes, note, component name.
AUDIO_IDENTITY_SPECS = (
    ('speaker', 'stock', 'SPEAKER_STOCK', SPEAKER_STOCK_BYTES, SPEAKER_STOCK_NOTE, SPEAKER_COMPONENT),
    ('speaker', 'fixed', 'SPEAKER_FIXED', SPEAKER_FIXED_BYTES, SPEAKER_FIXED_NOTE, SPEAKER_COMPONENT),
    ('mca', 'stock', 'MCA_STOCK', MCA_STOCK_BYTES, MCA_STOCK_NOTE, MCA_COMPONENT),
    ('mca', 'fixed', 'MCA_FIXED', MCA_FIXED_BYTES, MCA_FIXED_NOTE, MCA_COMPONENT),
)
UNKNOWN_AUDIO = b'unknown-audio-module'
LIVE_PIN_FIELDS = (
    'files', 'metadata_sha256', 'metadata_size', 'artifact_sha256',
    'artifact_size', 'artifact_note', 'modules',
)
APPLY_STATUS = 'connected; restart once to apply the update'
QUIET_STATUS = 'connected; future M4 fixes arrive with omarchy-m4-update'


def audio_sha(data):
    return MODULE.hashlib.sha256(data).hexdigest()


def audio_record(data, note):
    """One sha256/size/note identity. Built here, not by the production extractor."""
    return {'sha256': audio_sha(data), 'size': len(data), 'note': note}

# The installed alpha.1 Air's pacman.conf, without comments (observed 2026-10-12).
AIR_CONF = '''[options]
HoldPkg     = pacman glibc
Architecture = aarch64
CheckSpace
ParallelDownloads = 5
DownloadUser = alpm
SigLevel    = Required DatabaseOptional
LocalFileSigLevel = Optional
[core]
Include = /etc/pacman.d/mirrorlist
[extra]
Include = /etc/pacman.d/mirrorlist
[alarm]
Include = /etc/pacman.d/mirrorlist
[aur]
Include = /etc/pacman.d/mirrorlist
'''


class FakeSystem:
    """Records commands; answers like a machine with alpha.1 installed.

    `installed` is the local database (-Q and -Qq). `upgradable` is the pacman
    -Quq name list and is independent of it. `queries` overrides one of those
    answers with (returncode, stdout, stderr) when a test needs an error.
    """

    def __init__(self, root, fail=(), boot_status='current', change_boot=False, installed=('linux-m4-omarchy',),
                 boot_then_fail=(), upgradable=(), queries=None):
        self.root, self.fail, self.status, self.change_boot = root, fail, boot_status, change_boot
        self.installed, self.boot_then_fail = installed, boot_then_fail
        self.upgradable, self.queries = upgradable, queries or {}
        self.commands, self.refresh_config = [], None

    def __call__(self, command, check=False, capture_output=False, text=False, stdout=None, stderr=None,
                 encoding=None, errors=None):
        self.commands.append(command)
        joined = ' '.join(map(str, command))
        # An ALPM hook or the composer can replace boot.bin and still exit failed
        # (verification or journal cleanup). The setup message must not deny that.
        if any(word in command or word in joined for word in self.boot_then_fail):
            (self.root / MODULE.BOOT).write_bytes(b'partial-transition')
            return types.SimpleNamespace(returncode=1, stdout='', stderr='verification failed')
        if 'm4-boot-compose' in joined:
            return types.SimpleNamespace(returncode=0, stdout=json.dumps({'status': self.status}), stderr='')
        if '-Sy' in command:
            self.refresh_config = Path(command[command.index('--config') + 1]).read_text()
        if '-S' in command and self.change_boot:
            (self.root / MODULE.BOOT).write_bytes(b'composed')
        if '-Qq' in command or '-Quq' in command:
            return self.query_result(command)
        failed = any(word in command for word in self.fail)
        if '-Q' in command and command[-1] not in self.installed:
            failed = True
        return types.SimpleNamespace(returncode=1 if failed else 0, stdout='', stderr='')

    def query_result(self, command):
        operation = '-Qq' if '-Qq' in command else '-Quq'
        if operation in self.queries:
            code, out, err = self.queries[operation]
            return types.SimpleNamespace(returncode=code, stdout=out, stderr=err)
        # A forced failure must not look like -Quq's empty "nothing to do" exit.
        if any(word in command for word in self.fail):
            stderr = '' if operation == '-Qq' else 'error: query failed\n'
            return types.SimpleNamespace(returncode=1, stdout='', stderr=stderr)
        if operation == '-Qq':
            return types.SimpleNamespace(
                returncode=0, stdout=''.join(name + '\n' for name in self.installed), stderr='')
        if self.upgradable:
            return types.SimpleNamespace(
                returncode=0, stdout=''.join(name + '\n' for name in self.upgradable), stderr='')
        return types.SimpleNamespace(returncode=1, stdout='', stderr='')


class SetupTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.put(MODULE.COMPATIBLE, 'apple,j713\0apple,t8132\0')
        for name in MODULE.MANIFESTS:
            self.put(name, '{}')
        self.put(MODULE.CONF, AIR_CONF)
        self.put('etc/pacman.d/mirrorlist', 'Server = http://mirror.archlinuxarm.org/$arch/$repo\n')
        (self.root / MODULE.CONF).chmod(0o640)
        self.put(MODULE.BOOT, 'alpha.1 boot')
        self.include = 'Include = ' + str(self.root / MODULE.SNIPPET)
        # alpha.1 ships the pacman keyring directory readable by root only;
        # pacman-key leaves the public database files world-readable (issue #71).
        gnupg = self.root / MODULE.GNUPG
        gnupg.mkdir(parents=True, exist_ok=True)
        gnupg.chmod(0o700)
        for name in ('pubring.gpg', 'trustdb.gpg'):
            path = gnupg / name
            path.write_bytes(b'public')
            path.chmod(0o644)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def put_bytes(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            path.unlink()
        path.write_bytes(data)

    def setup_with(self, system, apply=True):
        with mock.patch.object(MODULE.subprocess, 'run', system):
            return MODULE.setup(self.root, apply, output=io.StringIO())

    def pacman_of(self, system):
        return [command for command in system.commands if command[0] == 'pacman']

    def assert_no_system_upgrade(self, commands):
        for command in commands:
            for arg in command:
                if arg.startswith('-') and not arg.startswith('--') and arg != '-Quq':
                    self.assertNotIn('u', arg, 'setup must never run a system upgrade: ' + ' '.join(command))

    def assert_queries_after_refresh(self, system):
        """-Qq and -Quq use the full -S config and follow the channel-only refresh."""
        pacman = self.pacman_of(system)
        refresh = next(command for command in pacman if '-Sy' in command)
        installed = next(command for command in pacman if '-Qq' in command)
        updates = next(command for command in pacman if '-Quq' in command)
        full = str(self.root / MODULE.CONF)
        self.assertLess(system.commands.index(refresh), system.commands.index(installed))
        self.assertLess(system.commands.index(installed), system.commands.index(updates))
        self.assertNotEqual(refresh[refresh.index('--config') + 1], full)
        for command in (installed, updates):
            self.assertEqual(command[command.index('--config') + 1], full)
            self.assertNotIn('--noscriptlet', command)
        installs = [command for command in pacman if '-S' in command]
        for command in installs:
            self.assertLess(system.commands.index(updates), system.commands.index(command))
            self.assertEqual(command[command.index('--config') + 1], full)
        return installs

    def test_connects_without_a_system_upgrade(self):
        system = FakeSystem(self.root)
        result = self.setup_with(system)
        self.assertEqual((result['status'], result['restart_needed']),
                         ('connected; future M4 fixes arrive with omarchy-m4-update', False))
        pacman = self.pacman_of(system)
        self.assertEqual(pacman[0][-2:], ['-Q', 'linux-m4-omarchy'])
        self.assertEqual(pacman[1][-2:], ['-Q', 'gst-plugins-good'])
        self.assertEqual(pacman[2][-1], '-Sy')
        self.assertIn('-Qq', pacman[3])
        self.assertIn('-Quq', pacman[4])
        # The fake base kernel is installed and not upgradable, so it stays.
        expected_names = [name for name in CHANNEL['packages'] if name != 'linux-m4-omarchy']
        expected = ['-S', '--needed', '--noconfirm', *expected_names, '--noscriptlet']
        self.assertEqual(pacman[5][-len(expected):], expected)
        self.assertEqual(result['install'], expected_names)
        self.assert_queries_after_refresh(system)
        self.assert_no_system_upgrade(pacman)
        self.assertIn('[options]', system.refresh_config)
        self.assertIn(self.include, system.refresh_config)
        self.assertNotIn('[core]', system.refresh_config)
        keys = [c for c in system.commands if c[0] == 'pacman-key']
        self.assertEqual(keys[0][-2], '--add')
        self.assertEqual(keys[1][-2:], ['--lsign-key', MODULE.FINGERPRINT])

    def test_repository_goes_first_and_once(self):
        self.setup_with(FakeSystem(self.root))
        conf = (self.root / MODULE.CONF).read_text()
        self.assertEqual(conf, AIR_CONF.replace('[core]\n', self.include + '\n\n[core]\n'))
        self.assertEqual((self.root / MODULE.CONF).stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.root / MODULE.SNIPPET).stat().st_mode & 0o777, 0o640)
        self.assertEqual((self.root / (MODULE.CONF + MODULE.BACKUP)).read_text(), AIR_CONF)
        self.assertEqual((self.root / MODULE.SNIPPET).read_text(),
                         '[omarchy-m4]\nSigLevel = Required DatabaseRequired\nServer = ' + CHANNEL['server'] + '\n')
        self.setup_with(FakeSystem(self.root))
        self.assertEqual((self.root / MODULE.CONF).read_text(), conf)

    def test_inspection_changes_nothing(self):
        system = FakeSystem(self.root)
        result = self.setup_with(system, apply=False)
        self.assertEqual((result['applied'], result['pacman_conf_changes']), (False, True))
        self.assertEqual((self.root / MODULE.CONF).read_text(), AIR_CONF)
        self.assertFalse((self.root / MODULE.SNIPPET).exists())
        self.assertEqual([c[-2:] for c in system.commands], [['-Q', 'linux-m4-omarchy'], ['-Q', 'gst-plugins-good']])

    def test_package_probe_keeps_pacman_errors_off_screen(self):
        system = FakeSystem(self.root)
        calls = []
        real = system.__call__

        def recording(command, **options):
            calls.append((command, options))
            return real(command, **options)
        with mock.patch.object(MODULE.subprocess, 'run', side_effect=recording):
            MODULE.setup(self.root, apply=False, output=io.StringIO())
        probe = next(options for command, options in calls if command[-2:] == ['-Q', 'gst-plugins-good'])
        self.assertEqual((probe['stdout'], probe['stderr']), (subprocess.DEVNULL, subprocess.DEVNULL))

    def test_installed_gst_plugins_good_keeps_its_sinks(self):
        system = FakeSystem(self.root, installed=('linux-m4-omarchy', 'gst-plugins-good'))
        result = self.setup_with(system)
        self.assertTrue(result['status'].startswith('connected'))
        self.assertNotIn(MODULE.SINKS, result['install'])
        install = next(c for c in system.commands if '-S' in c)
        self.assertNotIn(MODULE.SINKS, install)
        self.assertNotIn('linux-m4-omarchy', install)
        self.assertIn('omarchy-m4-boot', install)

    def test_newer_candidates_stay_while_another_package_upgrades(self):
        held = ('omarchy-m4-boot', 'omarchy-m4-speaker')
        upgrade = 'omarchy-m4-gpu'
        missing = 'omarchy-m4-headphones'
        installed = ('linux-m4-omarchy', *held, upgrade, 'glibc')
        upgradable = (upgrade, 'glibc')
        system = FakeSystem(self.root, installed=installed, upgradable=upgradable)
        result = self.setup_with(system)
        self.assertTrue(result['status'].startswith('connected'))
        installs = self.assert_queries_after_refresh(system)
        self.assertEqual(len(installs), 1)
        install = installs[0]
        for name in held:
            self.assertNotIn(name, install)
            self.assertNotIn(name, result['install'])
        self.assertIn(upgrade, install)
        self.assertIn(missing, install)
        self.assertNotIn('linux-m4-omarchy', install)
        self.assertNotIn('glibc', install)
        requested = [name for name in CHANNEL['packages']]
        expected = [name for name in requested if name not in installed or name in upgradable]
        self.assertEqual(result['install'], expected)
        self.assertEqual([arg for arg in install if arg in requested], expected)
        self.assertLess(requested.index(upgrade), requested.index(missing))
        self.assertLess(result['install'].index(upgrade), result['install'].index(missing))
        self.assert_no_system_upgrade(self.pacman_of(system))

    def test_missing_channel_package_is_the_only_install_target(self):
        missing = 'omarchy-m4-backlight'
        installed = tuple(name for name in CHANNEL['packages'] if name != missing)
        system = FakeSystem(self.root, installed=installed)
        result = self.setup_with(system)
        installs = self.assert_queries_after_refresh(system)
        expected = ['-S', '--needed', '--noconfirm', missing, '--noscriptlet']
        self.assertEqual(installs[0][-len(expected):], expected)
        self.assertEqual(result['install'], [missing])

    def test_current_packages_skip_empty_install_and_still_finish(self):
        system = FakeSystem(self.root, installed=tuple(CHANNEL['packages']))
        seen = []
        real_notifier = MODULE.Machine.start_notifier

        def spy(machine):
            seen.append(machine.sandbox)
            return real_notifier(machine)

        with mock.patch.object(MODULE.Machine, 'start_notifier', spy):
            result = self.setup_with(system)
        self.assert_queries_after_refresh(system)
        self.assertFalse(any('-S' in command for command in system.commands))
        compose = [c for c in system.commands if 'm4-boot-compose' in ' '.join(map(str, c))]
        self.assertEqual(compose[0][-1], '--apply')
        self.assertEqual(seen, [True])
        self.assertEqual(result['install'], [])
        self.assertEqual(result['boot'], 'current')
        self.assertEqual((result['status'], result['restart_needed'], result['applied']),
                         (QUIET_STATUS, False, True))

    def test_no_update_query_accepts_empty_exit_1(self):
        system = FakeSystem(self.root, queries={'-Quq': (1, '', '')})
        answers = []
        real = system.__call__

        def recording(command, **options):
            result = real(command, **options)
            if '-Quq' in command:
                answers.append((result.returncode, result.stdout, result.stderr, options.get('capture_output')))
            return result

        with mock.patch.object(MODULE.subprocess, 'run', side_effect=recording):
            result = MODULE.setup(self.root, True, output=io.StringIO())
        self.assertEqual(answers, [(1, '', '', True)])
        self.assertTrue(result['applied'])
        install = next(command for command in system.commands if '-S' in command)
        self.assertNotIn('linux-m4-omarchy', install)
        self.assertIn('omarchy-m4-boot', install)

    def test_apply_runs_one_at_a_time(self):
        import fcntl, threading
        lock = self.root / MODULE.LOCK
        lock.parent.mkdir(parents=True, exist_ok=True)
        held = open(lock, 'a')
        fcntl.flock(held, fcntl.LOCK_EX)
        done = threading.Event()
        system = FakeSystem(self.root)
        thread = threading.Thread(target=lambda: (self.setup_with(system), done.set()))
        thread.start()
        self.assertFalse(done.wait(0.5), 'a second run must wait for the lock')
        self.assertEqual(system.commands, [])
        fcntl.flock(held, fcntl.LOCK_UN)
        held.close()
        self.assertTrue(done.wait(10))
        thread.join()

    def test_restart_reported_when_boot_changes(self):
        result = self.setup_with(FakeSystem(self.root, change_boot=True))
        self.assertTrue(result['restart_needed'])
        self.assertIn('restart once', result['status'])

    def _audio_rows(self, name):
        rows = [row for row in AUDIO_IDENTITY_SPECS if row[0] == name]
        if len(rows) != 2 or {row[1] for row in rows} != {'stock', 'fixed'}:
            raise AssertionError(name)
        if len({row[5] for row in rows}) != 1:
            raise AssertionError(name)
        return rows

    def _patch_audio_identities(self):
        """Patch each identity to the synthetic value of its original shape.

        A string constant is replaced with the synthetic sha256, so scalar
        production code can still recognize these bytes. Any other original
        shape is replaced with the full synthetic record. Paths and component
        names stay as shipped. The originals are kept for the shape assertion.
        """
        originals, values = {}, {}
        for _, _, constant, blob, note, _ in AUDIO_IDENTITY_SPECS:
            original = getattr(MODULE, constant)
            originals[constant] = original
            record = audio_record(blob, note)
            values[constant] = record['sha256'] if isinstance(original, str) else record
        self._audio_identity_originals = originals
        return mock.patch.multiple(MODULE, create=True, **values)

    def _audio_identities(self, name):
        """Independent synthetic records. Not the patched production constants."""
        found = {kind: (blob, note, component)
                 for _, kind, _, blob, note, component in self._audio_rows(name)}
        stock_bytes, stock_note, component_name = found['stock']
        fixed_bytes, fixed_note, _ = found['fixed']
        return (stock_bytes, fixed_bytes, audio_record(stock_bytes, stock_note),
                audio_record(fixed_bytes, fixed_note), component_name)

    def _assert_patched_identities(self):
        originals = self._audio_identity_originals
        for name in ('speaker', 'mca'):
            stock_bytes, fixed_bytes, stock, fixed, component_name = self._audio_identities(name)
            records = {'stock': (stock, stock_bytes), 'fixed': (fixed, fixed_bytes)}
            for _, kind, constant, _, _, _ in self._audio_rows(name):
                record, blob = records[kind]
                self.assertEqual(set(record), {'sha256', 'size', 'note'})
                self.assertEqual(record['sha256'], audio_sha(blob))
                self.assertEqual(record['size'], len(blob))
                self.assertIsInstance(record['note'], str)
                self.assertNotEqual(record['note'], '')
                patched = getattr(MODULE, constant)
                if isinstance(originals[constant], str):
                    self.assertEqual(patched, record['sha256'])
                else:
                    self.assertEqual(patched, record)
            self.assertNotEqual(stock['sha256'], fixed['sha256'])
            self.assertNotEqual(stock['note'], fixed['note'])
            self.assertNotIn(audio_sha(UNKNOWN_AUDIO), (stock['sha256'], fixed['sha256']))
            self.assertIn(component_name, (SPEAKER_COMPONENT, MCA_COMPONENT))

    def component_paths(self, name):
        if name == 'speaker':
            return (MODULE.SPEAKER_MODULE, MODULE.SPEAKER_STAGED, MODULE.SPEAKER_HOLD, MODULE.SPEAKER_ROLLBACK)
        if name == 'mca':
            return (MODULE.MCA_MODULE, MODULE.MCA_STAGED, MODULE.MCA_HOLD, MODULE.MCA_ROLLBACK)
        raise AssertionError(name)

    def _release_of(self, module_rel):
        marker = '/j713-audio/'
        start = module_rel.index(marker) + len(marker)
        return module_rel[start:module_rel.index('/', start)]

    def _place_bytes(self, rel, data, kind):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
        elif path.exists():
            path.unlink()
        if kind == 'missing':
            return
        if kind == 'dir':
            path.mkdir()
            return
        if kind == 'file':
            path.write_bytes(data)
            return
        if kind == 'symlink':
            real = path.with_name(path.name + '.real')
            real.write_bytes(data)
            path.symlink_to(real.name)
            return
        raise AssertionError(kind)

    def _write_component_manifest(self, rel, module_rel, component_name, record, release,
                                  pin_path=None, merge=False):
        """Write one component's files, metadata, artifact, and modules pins.

        A merge keeps owner keys and the other component's entries. The files
        hash is stored on ``pin_path`` when that path is given, and removed
        from the real module path. This does not call the production extractor.
        """
        path = self.root / rel
        document = {}
        if merge and path.is_file() and not path.is_symlink():
            try:
                existing = json.loads(path.read_text())
            except (ValueError, UnicodeError, OSError):
                existing = None
            if isinstance(existing, dict):
                document = dict(existing)
        document['kernel_release'] = release
        files = document.get('files')
        files = dict(files) if isinstance(files, dict) else {}
        if pin_path is None:
            files['/' + module_rel] = record['sha256']
        else:
            files.pop('/' + module_rel, None)
            files['/' + pin_path] = record['sha256']
        document['files'] = files
        metadata = document.get('file_metadata')
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata[module_rel] = {'sha256': record['sha256'], 'size': record['size'], 'mode': 420}
        document['file_metadata'] = metadata
        artifacts = document.get('module_artifacts')
        artifacts = dict(artifacts) if isinstance(artifacts, dict) else {}
        artifacts[component_name] = {
            'module': component_name, 'note': record['note'], 'path': '/' + module_rel,
            'sha256': record['sha256'], 'size': record['size']}
        document['module_artifacts'] = artifacts
        modules = document.get('modules')
        modules = dict(modules) if isinstance(modules, dict) else {}
        modules[component_name] = record['note']
        document['modules'] = modules
        self.put(rel, json.dumps(document, indent=2, sort_keys=True) + '\n')

    def _plant_backup(self, name, kind, module_rel, component_name, stock_bytes, stock, fixed, release):
        """Stock backup is the module hash and length plus that module's seven pins."""
        if not kind:
            return
        state = self.component_paths(name)[2].rpartition('/')[0]
        module_backup = state + '/alpha1-module.ko'
        manifest_backup = state + '/alpha1-manifest.json'
        if kind == 'missing-half':
            self.put_bytes(module_backup, stock_bytes)
            return
        if kind == 'symlink':
            self.put_bytes(state + '/real-stock-module.ko', stock_bytes)
            link = self.root / module_backup
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to('real-stock-module.ko')
            self._write_component_manifest(manifest_backup, module_rel, component_name, stock, release)
            return
        if kind == 'stock':
            data, record = stock_bytes, stock
        elif kind == 'wrong-module':
            data, record = b'not-the-stock-module', stock
        elif kind == 'wrong-pin':
            data, record = stock_bytes, fixed
        else:
            raise AssertionError(kind)
        self.put_bytes(module_backup, data)
        self._write_component_manifest(manifest_backup, module_rel, component_name, record, release)

    def _plant_staged(self, rel, kind, stock_bytes, fixed_bytes):
        if kind in (None, 'missing'):
            return
        if kind == 'symlink':
            self._place_bytes(rel, fixed_bytes, 'symlink')
            return
        payloads = {'stock': stock_bytes, 'fixed': fixed_bytes, 'unknown': UNKNOWN_AUDIO,
                    'corrupt': b'corrupt-staged-audio'}
        if kind not in payloads:
            raise AssertionError(kind)
        self.put_bytes(rel, payloads[kind])

    def _plant_marker(self, rel, kind):
        if not kind:
            return
        if kind is True:
            self.put(rel, '')
            return
        if kind == 'symlink':
            self.put_bytes(rel + '.real', b'')
            link = self.root / rel
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(Path(rel).name + '.real')
            return
        raise AssertionError(kind)

    def _record_for_pin(self, pin, stock, fixed, installed_bytes):
        """Map a pin label to a full record without using the production extractor."""
        if pin == 'stock':
            return stock
        if pin == 'fixed':
            return fixed
        if pin == 'unknown':
            return audio_record(b'not-a-known-pin', 'unknown-note')
        if pin == 'matches':
            return audio_record(installed_bytes, 'matches-note')
        raise AssertionError(pin)

    def plant_audio_state(self, name, installed, pin, staged=None, release=None, backup=None,
                          hold=False, rollback=False, manifest='present', module='file', pin_path=None):
        """Plant one component's bytes, seven pins, stage, and optional stock backup."""
        module_rel, staged_rel, hold_rel, rollback_rel = self.component_paths(name)
        stock_bytes, fixed_bytes, stock, fixed, component_name = self._audio_identities(name)
        payloads = {'stock': stock_bytes, 'fixed': fixed_bytes, 'unknown': UNKNOWN_AUDIO}
        if installed not in payloads:
            raise AssertionError(installed)
        record = self._record_for_pin(pin, stock, fixed, payloads[installed])
        chosen_release = release or self._release_of(module_rel)
        self._place_bytes(module_rel, payloads[installed], module)
        self._plant_staged(staged_rel, staged, stock_bytes, fixed_bytes)
        manifest_rel = MODULE.MANIFESTS[1]
        if manifest == 'missing':
            (self.root / manifest_rel).unlink(missing_ok=True)
        elif manifest == 'malformed':
            self.put(manifest_rel, '{')
        elif manifest == 'present':
            self._write_component_manifest(
                manifest_rel, module_rel, component_name, record, chosen_release,
                pin_path=pin_path, merge=True)
        else:
            raise AssertionError(manifest)
        self._plant_backup(name, backup, module_rel, component_name, stock_bytes, stock, fixed,
                           chosen_release)
        if hold:
            self.put(hold_rel, '')
        self._plant_marker(rollback_rel, rollback)

    def assert_audio_pending(self, name, needed, through_setup=True, alone=True):
        pending = MODULE.speaker_pending if name == 'speaker' else MODULE.mca_pending
        other = MODULE.mca_pending if name == 'speaker' else MODULE.speaker_pending
        self.assertEqual(pending(self.root), needed)
        if alone:
            self.assertEqual(other(self.root), False)
        if not through_setup:
            return
        result = self.setup_with(FakeSystem(self.root))
        self.assertEqual((result['restart_needed'], result['status']),
                         (needed, APPLY_STATUS if needed else QUIET_STATUS))

    def _run_audio_cases(self, cases):
        self._assert_patched_identities()
        for name in ('speaker', 'mca'):
            for case in cases:
                with self.subTest(component=name, state=case['label']):
                    self.setUp()
                    options = dict(case)
                    needed = options.pop('needed')
                    through = options.pop('through_setup', True)
                    options.pop('label')
                    self.plant_audio_state(name, **options)
                    self.assert_audio_pending(name, needed, through_setup=through)

    def _assert_audio_pair(self, speaker, mca):
        self.assertEqual(MODULE.speaker_pending(self.root), speaker)
        self.assertEqual(MODULE.mca_pending(self.root), mca)
        restart = speaker or mca
        result = self.setup_with(FakeSystem(self.root))
        self.assertEqual((result['restart_needed'], result['status']),
                         (restart, APPLY_STATUS if restart else QUIET_STATUS))

    def test_audio_states_classify_forward_work(self):
        """Stock with the fixed stage is pending; current, reverse, and unknown are not.

        Fixture records stand in for the stock and fixed identities. Fresh stock does not
        need a backup. Forward interruption is pending only with a stock backup
        and a regular staged file of the fixed hash and size. Reverse (stock module,
        fixed pins) stays not pending when that staged file is the fixed module,
        with or without a stock backup, while the marker is absent. A recognized
        result is pending work, not a promise that every later boot write succeeds.
        """
        cases = (
            dict(installed='stock', pin='stock', staged='fixed', needed=True, label='stock to fixed'),
            dict(installed='fixed', pin='fixed', staged='fixed', needed=False, label='already fixed'),
            dict(installed='fixed', pin='fixed', staged='corrupt', needed=False,
                 label='fixed with a different stage'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='stock', needed=True,
                 label='forward interruption with stock backup'),
            dict(installed='fixed', pin='stock', staged='fixed', needed=False,
                 label='forward interruption without backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='wrong-module', needed=False,
                 label='forward interruption with the wrong module backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='wrong-pin', needed=False,
                 label='forward interruption with the wrong pin backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='missing-half', needed=False,
                 label='forward interruption with half a backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='symlink', needed=False,
                 label='forward interruption with a symlink backup'),
            dict(installed='fixed', pin='stock', staged='corrupt', backup='stock', needed=False,
                 label='forward interruption with a corrupt stage'),
            dict(installed='fixed', pin='stock', staged='missing', backup='stock', needed=False,
                 label='forward interruption without a stage'),
            dict(installed='fixed', pin='stock', staged='symlink', backup='stock', needed=False,
                 label='forward interruption with a symlink stage'),
            dict(installed='stock', pin='fixed', staged='stock', needed=False,
                 label='reverse without a marker'),
            dict(installed='stock', pin='fixed', staged='stock', backup='stock', needed=False,
                 label='reverse backup without a marker'),
            dict(installed='stock', pin='fixed', staged='fixed', needed=False,
                 label='reverse fixed stage without a marker'),
            dict(installed='stock', pin='fixed', staged='fixed', backup='stock', needed=False,
                 label='reverse fixed stage with stock backup and no marker'),
            dict(installed='unknown', pin='stock', staged='fixed', needed=False, label='unknown module bytes'),
            dict(installed='unknown', pin='matches', staged='fixed', needed=False,
                 label='unknown bytes are not a known pin'),
            dict(installed='unknown', pin='matches', staged='unknown', needed=False,
                 label='unknown bytes that match their stage'),
            dict(installed='stock', pin='unknown', staged='stock', needed=False, label='unknown pin'),
            dict(installed='stock', pin='stock', staged='fixed', release='not-the-release', needed=False,
                 label='wrong release'),
            dict(installed='stock', pin='stock', staged='corrupt', needed=False,
                 label='stock with a corrupt stage'),
            dict(installed='stock', pin='stock', staged='missing', needed=False, label='stock without a stage'),
            dict(installed='stock', pin='stock', staged='symlink', needed=False,
                 label='stock with a symlink stage'),
            dict(installed='stock', pin='stock', staged='fixed', pin_path='usr/lib/elsewhere.ko', needed=False,
                 label='pin on another path'),
            dict(installed='stock', pin='stock', staged='fixed', manifest='malformed', needed=False,
                 label='malformed manifest'),
            dict(installed='stock', pin='stock', staged='fixed', manifest='missing', through_setup=False,
                 needed=False, label='missing manifest'),
            dict(installed='stock', pin='stock', staged='fixed', module='symlink', needed=False,
                 label='symlink module'),
            dict(installed='stock', pin='stock', staged='fixed', module='dir', needed=False,
                 label='directory module'),
            dict(installed='stock', pin='stock', staged='fixed', module='missing', needed=False,
                 label='missing module'),
        )
        with self._patch_audio_identities():
            self._run_audio_cases(cases)

    def test_audio_rollback_marker_requires_a_recognized_backup(self):
        """A regular rollback marker is pending only for a recognized state with a stock backup.

        The marker wins over hold, including restored stock. Unknown bytes, an
        unknown pin, a bad backup, or a symlink marker are not pending. A corrupt
        staged file is not forward repair, and it does not cancel that marker.
        """
        cases = (
            dict(installed='fixed', pin='fixed', staged='fixed', backup='stock', hold=True, rollback=True,
                 needed=True, label='fixed with marker'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 needed=True, label='forward with marker'),
            dict(installed='stock', pin='fixed', staged='stock', backup='stock', hold=True, rollback=True,
                 needed=True, label='reverse with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 needed=True, label='restored stock with marker'),
            dict(installed='stock', pin='stock', staged='corrupt', backup='stock', hold=True, rollback=True,
                 needed=True, label='restored stock marker ignores the stage'),
            dict(installed='fixed', pin='stock', staged='corrupt', backup='stock', hold=True, rollback=True,
                 needed=True, label='forward marker ignores the stage'),
            dict(installed='fixed', pin='fixed', staged='fixed', hold=True, rollback=True, needed=False,
                 label='fixed marker without backup'),
            dict(installed='fixed', pin='stock', staged='fixed', hold=True, rollback=True, needed=False,
                 label='forward marker without backup'),
            dict(installed='stock', pin='fixed', staged='stock', hold=True, rollback=True, needed=False,
                 label='reverse marker without backup'),
            dict(installed='stock', pin='stock', staged='fixed', hold=True, rollback=True, needed=False,
                 label='restored stock marker without backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='wrong-module', hold=True, rollback=True,
                 needed=False, label='marker with the wrong module backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='wrong-pin', hold=True, rollback=True,
                 needed=False, label='marker with the wrong pin backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='missing-half', hold=True, rollback=True,
                 needed=False, label='marker with half a backup'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='symlink', hold=True, rollback=True,
                 needed=False, label='marker with a symlink backup'),
            dict(installed='unknown', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 needed=False, label='unknown bytes with marker'),
            dict(installed='stock', pin='unknown', staged='stock', backup='stock', hold=True, rollback=True,
                 needed=False, label='unknown pin with marker'),
            dict(installed='unknown', pin='matches', staged='fixed', backup='stock', hold=True, rollback=True,
                 needed=False, label='matching unknown pin with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 release='not-the-release', needed=False, label='wrong release with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 pin_path='usr/lib/elsewhere.ko', needed=False, label='pin on another path with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 manifest='malformed', needed=False, label='malformed manifest with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 manifest='missing', through_setup=False, needed=False,
                 label='missing manifest with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 module='symlink', needed=False, label='symlink module with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 module='dir', needed=False, label='directory module with marker'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback=True,
                 module='missing', needed=False, label='missing module with marker'),
            dict(installed='stock', pin='stock', staged='fixed', hold=True, needed=False,
                 label='hold without marker on stock'),
            dict(installed='fixed', pin='stock', staged='fixed', backup='stock', hold=True, needed=False,
                 label='hold without marker on forward'),
            dict(installed='stock', pin='fixed', staged='stock', hold=True, needed=False,
                 label='hold without marker on reverse'),
            dict(installed='fixed', pin='fixed', staged='fixed', hold=True, needed=False,
                 label='hold without marker on fixed'),
            dict(installed='fixed', pin='fixed', staged='fixed', backup='stock', rollback='symlink', needed=False,
                 label='symlink marker on current'),
            dict(installed='stock', pin='stock', staged='fixed', backup='stock', hold=True, rollback='symlink',
                 needed=False, label='symlink marker does not beat hold'),
        )
        with self._patch_audio_identities():
            self._run_audio_cases(cases)
            with self.subTest(state='speaker pending while mca marker is unknown'):
                self.setUp()
                self.plant_audio_state('speaker', 'stock', 'stock', staged='fixed')
                self.plant_audio_state('mca', 'unknown', 'stock', staged='fixed', backup='stock',
                                       hold=True, rollback=True)
                self._assert_audio_pair(True, False)
            with self.subTest(state='mca marker while speaker is held'):
                self.setUp()
                self.plant_audio_state('speaker', 'stock', 'stock', staged='fixed', hold=True)
                self.plant_audio_state('mca', 'fixed', 'fixed', staged='fixed', backup='stock',
                                       hold=True, rollback=True)
                self._assert_audio_pair(False, True)
            with self.subTest(state='speaker hold does not hide a pending mca swap'):
                self.setUp()
                self.plant_audio_state('speaker', 'stock', 'stock', staged='fixed', hold=True)
                self.plant_audio_state('mca', 'stock', 'stock', staged='fixed')
                self._assert_audio_pair(False, True)

    def test_uncertain_rollback_marker_is_not_audio_work(self):
        """A non-regular rollback marker is not pending boot work.

        Fixed with a valid stock backup, and stock with the fixed module
        staged, stay not pending for the speaker and for MCA. A symlink to a
        file outside the state directory, a dangling symlink, a directory, or
        a fifo is not rollback authorization and does not fall through to a
        forward swap. The module, manifest, backup, marker, and symlink target
        stay as planted.
        """
        try:
            probe = self.root / 'fifo-probe'
            os.mkfifo(probe)
            probe.unlink()
            kinds = ('symlink', 'dangling', 'directory', 'fifo')
        except OSError:
            kinds = ('symlink', 'dangling', 'directory')

        def identity(path):
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                return ('absent',)
            if stat.S_ISLNK(mode):
                return ('symlink', os.readlink(path))
            if stat.S_ISDIR(mode):
                return ('directory', tuple(sorted(child.name for child in path.iterdir())))
            if stat.S_ISREG(mode):
                return ('file', path.read_bytes())
            if stat.S_ISFIFO(mode):
                return ('fifo',)
            return ('other', stat.S_IFMT(mode))

        def plant_marker(rollback_rel, kind):
            marker = self.root / rollback_rel
            marker.parent.mkdir(parents=True, exist_ok=True)
            outside = self.root / 'var' / 'outside-rollback-target'
            outside.parent.mkdir(parents=True, exist_ok=True)
            outside.write_bytes(b'outside-target')
            missing = marker.parent.parent / 'missing-rollback-target'
            if kind == 'symlink':
                marker.symlink_to(Path('../../../outside-rollback-target'))
            elif kind == 'dangling':
                marker.symlink_to(Path('../missing-rollback-target'))
            elif kind == 'directory':
                marker.mkdir()
            elif kind == 'fifo':
                os.mkfifo(marker)
            else:
                raise AssertionError(kind)
            return outside, missing

        with self._patch_audio_identities():
            self._assert_patched_identities()
            for name in ('speaker', 'mca'):
                for live, pin in (('fixed', 'fixed'), ('stock', 'stock')):
                    for kind in kinds:
                        with self.subTest(component=name, live=live, kind=kind):
                            self.setUp()
                            self.plant_audio_state(name, live, pin, staged='fixed', backup='stock')
                            _, _, hold_rel, rollback_rel = self.component_paths(name)
                            outside, missing = plant_marker(rollback_rel, kind)
                            stage = self.root / hold_rel.rpartition('/')[0]
                            if kind == 'symlink':
                                self.assertEqual(os.readlink(self.root / rollback_rel),
                                                 '../../../outside-rollback-target')
                                linked = (stage / '../../../outside-rollback-target').resolve()
                                self.assertEqual(linked, outside.resolve())
                                self.assertNotIn(stage.resolve(), linked.parents)
                            elif kind == 'dangling':
                                self.assertEqual(os.readlink(self.root / rollback_rel),
                                                 '../missing-rollback-target')
                                self.assertEqual(identity(missing), ('absent',))
                            other = 'mca' if name == 'speaker' else 'speaker'
                            other_rollback = self.component_paths(other)[3]
                            module_rel, staged_rel, _, _ = self.component_paths(name)

                            def snapshot():
                                state = hold_rel.rpartition('/')[0]
                                return (identity(self.root / module_rel),
                                        identity(self.root / staged_rel),
                                        identity(self.root / MODULE.MANIFESTS[1]),
                                        identity(self.root / (state + '/alpha1-module.ko')),
                                        identity(self.root / (state + '/alpha1-manifest.json')),
                                        identity(self.root / hold_rel),
                                        identity(self.root / rollback_rel),
                                        identity(self.root / other_rollback),
                                        identity(outside),
                                        identity(missing))

                            before = snapshot()
                            self.assert_audio_pending(name, False)
                            self.assertEqual(snapshot(), before)

    def test_enotdir_rollback_marker_is_not_audio_work(self):
        """A regular file in place of the state directory is not an absent marker.

        Stock bytes, stock pins, and a fixed staged module would be pending
        if the marker were missing. The state directory is a regular file, so
        lstat of the rollback path raises ENOTDIR and the component is not
        pending. The other component keeps that same stock stage with no
        state directory: its marker lstat is ENOENT and it stays pending.
        The occupied file, both modules, the manifest, both staged files,
        and the other component's state stay as planted. Current is not the
        fixture: it is already not pending when the marker is only absent.
        """
        occupied = b'not-a-state-directory'
        unrelated = (
            MODULE.GPU_HOLD.rpartition('/')[0],
            MODULE.HEADPHONES_STATE,
            MODULE.CPU_IDLE_STATE,
            MODULE.BACKLIGHT_STATE,
        )

        def described(path):
            if path.is_symlink():
                return ('symlink', os.readlink(path))
            if not path.exists():
                return ('absent',)
            info = path.lstat()
            mode = stat.S_IMODE(info.st_mode)
            if stat.S_ISREG(info.st_mode):
                return ('file', mode, path.read_bytes())
            if stat.S_ISDIR(info.st_mode):
                return ('directory', mode, tuple(sorted(child.name for child in path.iterdir())))
            return ('other', mode, stat.S_IFMT(info.st_mode))

        def pin_keys(name):
            module_rel, _, _, _ = self.component_paths(name)
            _, _, stock, _, component_name = self._audio_identities(name)
            document = json.loads((self.root / MODULE.MANIFESTS[1]).read_text())
            metadata = document['file_metadata'][module_rel]
            artifact = document['module_artifacts'][component_name]
            self.assertEqual(set(metadata), {'sha256', 'size', 'mode'})
            self.assertEqual(set(artifact), {'module', 'note', 'path', 'sha256', 'size'})
            self.assertEqual(document['files']['/' + module_rel], stock['sha256'])
            self.assertEqual(metadata['sha256'], stock['sha256'])
            self.assertEqual(metadata['size'], stock['size'])
            self.assertEqual(artifact['note'], stock['note'])
            self.assertEqual(artifact['sha256'], stock['sha256'])
            self.assertEqual(artifact['size'], stock['size'])
            self.assertEqual(document['modules'][component_name], stock['note'])

        with self._patch_audio_identities():
            self._assert_patched_identities()
            for name in ('speaker', 'mca'):
                with self.subTest(component=name):
                    self.setUp()
                    other = 'mca' if name == 'speaker' else 'speaker'
                    self.plant_audio_state(name, 'stock', 'stock', staged='fixed')
                    self.plant_audio_state(other, 'stock', 'stock', staged='fixed')
                    module_rel, staged_rel, hold_rel, rollback_rel = self.component_paths(name)
                    other_module, other_staged, other_hold, other_rollback = self.component_paths(other)
                    stock_bytes, fixed_bytes, _, _, _ = self._audio_identities(name)
                    other_stock, other_fixed, _, _, _ = self._audio_identities(other)
                    self.assertEqual((self.root / module_rel).read_bytes(), stock_bytes)
                    self.assertEqual((self.root / staged_rel).read_bytes(), fixed_bytes)
                    self.assertEqual((self.root / other_module).read_bytes(), other_stock)
                    self.assertEqual((self.root / other_staged).read_bytes(), other_fixed)
                    pin_keys(name)
                    pin_keys(other)
                    state_rel = hold_rel.rpartition('/')[0]
                    state_path = self.root / state_rel
                    self.assertFalse(state_path.is_symlink())
                    if state_path.is_dir():
                        self.assertEqual([child.name for child in state_path.iterdir()], [])
                        self.assertTrue(state_path.resolve().is_relative_to(self.root.resolve()))
                        state_path.rmdir()
                    self.assertFalse(state_path.exists())
                    state_path.parent.mkdir(parents=True, exist_ok=True)
                    state_path.write_bytes(occupied)
                    state_path.chmod(0o640)
                    self.assertTrue(state_path.is_file())
                    self.assertFalse(state_path.is_dir())
                    self.assertFalse(state_path.is_symlink())
                    self.assertTrue(stat.S_ISREG(state_path.lstat().st_mode))
                    self.assertEqual(stat.S_IMODE(state_path.lstat().st_mode), 0o640)
                    with self.assertRaises(NotADirectoryError):
                        (self.root / rollback_rel).lstat()
                    hold_path = self.root / hold_rel
                    self.assertFalse(hold_path.is_symlink() or hold_path.exists())
                    other_state = self.root / other_hold.rpartition('/')[0]
                    self.assertFalse(other_state.exists())
                    self.assertFalse(other_state.is_symlink())
                    with self.assertRaises(FileNotFoundError):
                        (self.root / other_rollback).lstat()

                    def snapshot():
                        return (described(state_path),
                                described(self.root / module_rel),
                                described(self.root / other_module),
                                described(self.root / MODULE.MANIFESTS[0]),
                                described(self.root / MODULE.MANIFESTS[1]),
                                described(self.root / staged_rel),
                                described(self.root / other_staged),
                                described(other_state),
                                tuple(described(self.root / rel) for rel in unrelated))

                    before = snapshot()
                    if name == 'speaker':
                        self.assertFalse(MODULE.speaker_pending(self.root))
                        self.assertTrue(MODULE.mca_pending(self.root))
                    else:
                        self.assertFalse(MODULE.mca_pending(self.root))
                        self.assertTrue(MODULE.speaker_pending(self.root))
                    self.assertEqual(snapshot(), before)

    def test_audio_identities_match_the_speaker_helper(self):
        """Updater identities are the helper's full stock and fixed records.

        Paths and the release are compared unchanged. The record comparison is
        last: a missing constant is an AttributeError. The four records and the
        two component names are duplicated on purpose. This import is unpatched
        and requires the whole sha256/size/note record, so a scalar production
        identity fails here. Scenario fixtures may patch a string; this test
        does not.
        """
        spec = importlib.util.spec_from_file_location(
            'm4_speaker_module_shipped', ROOT / 'tools/m4-speaker-module.py')
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        self.assertEqual(MODULE.SPEAKER_MODULE, helper.MODULE)
        self.assertEqual(MODULE.SPEAKER_STAGED, helper.STAGED)
        self.assertEqual(MODULE.SPEAKER_HOLD, helper.STATE + '/hold')
        self.assertEqual(MODULE.SPEAKER_ROLLBACK, helper.STATE + '/rollback')
        self.assertEqual(MODULE.MCA_MODULE, helper.MCA_MODULE)
        self.assertEqual(MODULE.MCA_STAGED, helper.MCA_STAGED)
        self.assertEqual(MODULE.MCA_HOLD, helper.MCA_STATE + '/hold')
        self.assertEqual(MODULE.MCA_ROLLBACK, helper.MCA_STATE + '/rollback')
        self.assertEqual(MODULE.MANIFESTS[1], helper.MANIFEST)
        self.assertEqual(self._release_of(MODULE.SPEAKER_MODULE), helper.RELEASE)
        self.assertEqual(self._release_of(MODULE.MCA_MODULE), helper.RELEASE)
        self.assertEqual(
            MODULE.MCA_MODULE,
            'usr/lib/omarchy/j713-audio/7.1.9-m4-gpu-backport-20261008/modules/snd-soc-apple-mca.ko')
        self.assertEqual(MODULE.MCA_STAGED, 'usr/lib/omarchy-m4/j713-audio/snd-soc-apple-mca.ko')
        self.assertEqual(MODULE.MCA_HOLD, 'var/lib/omarchy-m4/mca-module/hold')
        self.assertEqual(MODULE.MCA_ROLLBACK, 'var/lib/omarchy-m4/mca-module/rollback')
        self.assertNotEqual(MODULE.MCA_MODULE, MODULE.SPEAKER_MODULE)
        self.assertNotEqual(MODULE.MCA_STAGED, MODULE.SPEAKER_STAGED)
        self.assertNotEqual(MODULE.MCA_HOLD, MODULE.SPEAKER_HOLD)
        self.assertNotEqual(MODULE.MCA_ROLLBACK, MODULE.SPEAKER_ROLLBACK)
        self.assertEqual(MODULE.SPEAKER_STOCK, helper.STOCK)
        self.assertEqual(MODULE.SPEAKER_FIXED, helper.FIXED)
        self.assertEqual(MODULE.MCA_STOCK, helper.MCA_STOCK)
        self.assertEqual(MODULE.MCA_FIXED, helper.MCA_FIXED)
        self.assertEqual(MODULE.SPEAKER_NAME, 'snd_soc_j713_group')
        self.assertEqual(MODULE.MCA_NAME, 'snd_soc_apple_mca')
        self.assertEqual(MODULE.SPEAKER_NAME, helper.NAME)
        self.assertEqual(MODULE.MCA_NAME, helper.MCA_NAME)
        for record in (helper.STOCK, helper.FIXED, helper.MCA_STOCK, helper.MCA_FIXED):
            self.assertEqual(set(record), {'sha256', 'size', 'note'})

    def _mutate_selected_pin(self, name, field, how):
        """Change or remove one of the seven live pins. The fixture edits the JSON itself."""
        module_rel, _, _, _ = self.component_paths(name)
        component_name = SPEAKER_COMPONENT if name == 'speaker' else MCA_COMPONENT
        path = self.root / MODULE.MANIFESTS[1]
        document = json.loads(path.read_text())
        if field == 'files':
            key = '/' + module_rel
            if how == 'change':
                document['files'][key] = 'f' * 64
            elif how == 'remove':
                del document['files'][key]
            else:
                raise AssertionError(how)
        elif field in ('metadata_sha256', 'metadata_size'):
            entry = document['file_metadata'][module_rel]
            key = 'sha256' if field == 'metadata_sha256' else 'size'
            if how == 'change':
                entry[key] = 'e' * 64 if key == 'sha256' else 1
            elif how == 'remove':
                del entry[key]
            else:
                raise AssertionError(how)
        elif field in ('artifact_sha256', 'artifact_size', 'artifact_note'):
            entry = document['module_artifacts'][component_name]
            key = {'artifact_sha256': 'sha256', 'artifact_size': 'size', 'artifact_note': 'note'}[field]
            if how == 'change':
                entry[key] = {'sha256': 'd' * 64, 'size': 1, 'note': 'wrong-note'}[key]
            elif how == 'remove':
                del entry[key]
            else:
                raise AssertionError(how)
        elif field == 'modules':
            if how == 'change':
                document['modules'][component_name] = 'wrong-note'
            elif how == 'remove':
                del document['modules'][component_name]
            else:
                raise AssertionError(how)
        else:
            raise AssertionError(field)
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')

    def test_each_live_pin_is_required_when_bytes_and_stage_are_valid(self):
        """A stock module and a fixed stage are pending only when all seven pins match.

        Changing or removing any one pin leaves the module bytes and the staged
        file alone and is not pending, for the speaker and for MCA.
        """
        with self._patch_audio_identities():
            self._assert_patched_identities()
            for name in ('speaker', 'mca'):
                with self.subTest(component=name, pin='valid'):
                    self.setUp()
                    stock_bytes, fixed_bytes, _, _, _ = self._audio_identities(name)
                    module_rel, staged_rel, _, _ = self.component_paths(name)
                    self.plant_audio_state(name, 'stock', 'stock', staged='fixed')
                    self.assertEqual((self.root / module_rel).read_bytes(), stock_bytes)
                    self.assertEqual((self.root / staged_rel).read_bytes(), fixed_bytes)
                    self.assert_audio_pending(name, True)
                for field in LIVE_PIN_FIELDS:
                    for how in ('change', 'remove'):
                        with self.subTest(component=name, pin=field, how=how):
                            self.setUp()
                            stock_bytes, fixed_bytes, _, _, _ = self._audio_identities(name)
                            module_rel, staged_rel, _, _ = self.component_paths(name)
                            self.plant_audio_state(name, 'stock', 'stock', staged='fixed')
                            self._mutate_selected_pin(name, field, how)
                            self.assertEqual((self.root / module_rel).read_bytes(), stock_bytes)
                            self.assertEqual((self.root / staged_rel).read_bytes(), fixed_bytes)
                            self.assert_audio_pending(name, False)

    def _corrupt_backup_manifest(self, name, kind):
        """Spoil one backup pin and leave the stock files hash in place."""
        module_rel, _, _, _ = self.component_paths(name)
        _, _, stock, _, component_name = self._audio_identities(name)
        state = self.component_paths(name)[2].rpartition('/')[0]
        path = self.root / (state + '/alpha1-manifest.json')
        document = json.loads(path.read_text())
        self.assertEqual(document['files']['/' + module_rel], stock['sha256'])
        if kind == 'wrong-note':
            document['modules'][component_name] = 'wrong-note'
        elif kind == 'wrong-size':
            document['file_metadata'][module_rel]['size'] = stock['size'] + 1
        elif kind == 'missing-map':
            del document['module_artifacts']
        elif kind == 'wrong-type':
            document['file_metadata'][module_rel] = ['not-a-metadata-map']
        elif kind == 'modules-type':
            document['modules'][component_name] = {'note': document['modules'][component_name]}
        else:
            raise AssertionError(kind)
        self.assertEqual(document['files']['/' + module_rel], stock['sha256'])
        path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')

    def test_backup_must_match_every_pin_not_only_the_files_hash(self):
        """Forward work and a regular rollback marker need the whole stock backup.

        A correct files hash with a wrong note or size, a missing nested map,
        or a nested value of the wrong type is not pending. A backup whose
        release differs still counts when the seven pins and the module hash
        and length match. There is no separate backup-release requirement.
        """
        corruptions = ('wrong-note', 'wrong-size', 'missing-map', 'wrong-type', 'modules-type')
        situations = (
            dict(installed='fixed', pin='stock', staged='fixed', label='forward'),
            dict(installed='fixed', pin='fixed', staged='fixed', rollback=True, hold=True,
                 label='marker'),
        )
        with self._patch_audio_identities():
            self._assert_patched_identities()
            for name in ('speaker', 'mca'):
                for situation in situations:
                    with self.subTest(component=name, state=situation['label'], backup='valid'):
                        self.setUp()
                        self.plant_audio_state(name, situation['installed'], situation['pin'],
                                               staged='fixed', backup='stock',
                                               rollback=situation.get('rollback', False),
                                               hold=situation.get('hold', False))
                        self.assert_audio_pending(name, True)
                    for kind in corruptions:
                        with self.subTest(component=name, state=situation['label'], backup=kind):
                            self.setUp()
                            self.plant_audio_state(name, situation['installed'], situation['pin'],
                                                   staged='fixed', backup='stock',
                                                   rollback=situation.get('rollback', False),
                                                   hold=situation.get('hold', False))
                            self._corrupt_backup_manifest(name, kind)
                            self.assert_audio_pending(name, False)
                with self.subTest(component=name, state='forward', backup='other-release'):
                    self.setUp()
                    self.plant_audio_state(name, 'fixed', 'stock', staged='fixed', backup='stock')
                    state = self.component_paths(name)[2].rpartition('/')[0]
                    path = self.root / (state + '/alpha1-manifest.json')
                    document = json.loads(path.read_text())
                    document['kernel_release'] = 'not-the-live-release'
                    path.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
                    self.assert_audio_pending(name, True)

    def test_speaker_stays_pending_when_only_mca_metadata_is_invalid(self):
        """One component's bad metadata does not clear the other's pending swap.

        The shared manifest keeps the owner key and both components' entries.
        Only the MCA metadata size is wrong.
        """
        with self._patch_audio_identities():
            self._assert_patched_identities()
            _, _, speaker_stock, _, _ = self._audio_identities('speaker')
            _, _, mca_stock, _, _ = self._audio_identities('mca')
            self.plant_audio_state('speaker', 'stock', 'stock', staged='fixed')
            manifest = self.root / MODULE.MANIFESTS[1]
            document = json.loads(manifest.read_text())
            document['owner_calibration'] = {'gain': 4}
            manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
            self.plant_audio_state('mca', 'stock', 'stock', staged='fixed')
            document = json.loads(manifest.read_text())
            self.assertEqual(document['owner_calibration'], {'gain': 4})
            self.assertEqual(document['files']['/' + MODULE.SPEAKER_MODULE], speaker_stock['sha256'])
            self.assertEqual(document['files']['/' + MODULE.MCA_MODULE], mca_stock['sha256'])
            self.assertEqual(document['modules'][SPEAKER_COMPONENT], speaker_stock['note'])
            self.assertEqual(document['modules'][MCA_COMPONENT], mca_stock['note'])
            self.assertEqual(document['file_metadata'][MODULE.SPEAKER_MODULE]['size'],
                             speaker_stock['size'])
            self.assertIn(SPEAKER_COMPONENT, document['module_artifacts'])
            self.assertIn(MCA_COMPONENT, document['module_artifacts'])
            document['file_metadata'][MODULE.MCA_MODULE]['size'] = 1
            manifest.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
            self._assert_audio_pair(True, False)
            again = json.loads(manifest.read_text())
            self.assertEqual(again['owner_calibration'], {'gain': 4})
            self.assertEqual(again['file_metadata'][MODULE.SPEAKER_MODULE]['size'],
                             speaker_stock['size'])
            self.assertEqual(again['file_metadata'][MODULE.MCA_MODULE]['size'], 1)
            self.assertEqual(again['modules'][SPEAKER_COMPONENT], speaker_stock['note'])

    def test_restart_reported_when_a_gpu_swap_is_pending(self):
        alpha1 = b'alpha.1 gpu module'
        with mock.patch.object(MODULE, 'GPU_STOCK', MODULE.hashlib.sha256(alpha1).hexdigest()):
            (self.root / MODULE.GPU_MODULE).parent.mkdir(parents=True)
            (self.root / MODULE.GPU_MODULE).write_bytes(alpha1)
            self.put(MODULE.GPU_STAGED, 'fixed module')
            result = self.setup_with(FakeSystem(self.root))
            self.assertEqual((result['restart_needed'], result['status']),
                             (True, 'connected; restart once to apply the update'))
            self.put(MODULE.GPU_HOLD, '')
            self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'held: nothing applies')
            (self.root / MODULE.GPU_HOLD).unlink()
            self.put(MODULE.GPU_MODULE, 'fixed module')
            self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'already swapped')
            self.put(MODULE.GPU_MODULE, 'hand-patched module')
            self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'unknown: never applied')

    def test_restart_reported_when_the_headphone_card_is_new(self):
        self.put(MODULE.HEADPHONES_STAGED, 'staged card module')
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'not a running system')
        (self.root / 'sys/module').mkdir(parents=True)
        result = self.setup_with(FakeSystem(self.root))
        self.assertEqual((result['restart_needed'], result['status']),
                         (True, 'connected; restart once to apply the update'))
        for name in ('hold', 'attempt'):
            with self.subTest(name):
                self.put(MODULE.HEADPHONES_STATE + '/' + name, '')
                self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], name + ': nothing loads')
                (self.root / MODULE.HEADPHONES_STATE / name).unlink()
        (self.root / 'sys/module/snd_soc_j713_headphones').mkdir()
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'already loaded')

    def test_restart_reported_when_cpu_idle_is_new(self):
        self.put(MODULE.CPU_IDLE_STAGED, 'staged idle module')
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'not a running system')
        (self.root / 'sys/module').mkdir(parents=True)
        result = self.setup_with(FakeSystem(self.root))
        self.assertEqual((result['restart_needed'], result['status']),
                         (True, 'connected; restart once to apply the update'))
        for name in ('hold', 'attempt'):
            with self.subTest(name):
                self.put(MODULE.CPU_IDLE_STATE + '/' + name, '')
                self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], name + ': nothing loads')
                (self.root / MODULE.CPU_IDLE_STATE / name).unlink()
        (self.root / 'sys/module/j713_cpuidle').mkdir()
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'already loaded')

    def test_restart_reported_when_the_boost_guard_is_new(self):
        (self.root / 'sys/module/j713_cpuidle').mkdir(parents=True)
        self.put(MODULE.CPU_IDLE_STAGED, 'staged idle module')
        self.put(MODULE.CPU_GUARD_STAGED, 'staged boost guard')
        self.assertTrue(self.setup_with(FakeSystem(self.root))['restart_needed'], 'idle loaded, guard not yet')
        self.put(MODULE.CPU_IDLE_STATE + '/boost-hold', '')
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'boost switched off')
        (self.root / MODULE.CPU_IDLE_STATE / 'boost-hold').unlink()
        (self.root / 'sys/module/j713_pguard').mkdir()
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'already loaded')

    def test_boot_not_current_is_a_failure(self):
        with self.assertRaisesRegex(MODULE.Refused, 'boot.bin is not in step'):
            self.setup_with(FakeSystem(self.root, boot_status='refused'))

    def test_refusals_before_any_change(self):
        cases = (
            (lambda: self.put(MODULE.COMPATIBLE, 'apple,j613\0'), FakeSystem, 'not a J713'),
            (lambda: (self.root / MODULE.MANIFESTS[1]).unlink(), FakeSystem, 'Not an installed alpha.1'),
            (lambda: None, lambda root: FakeSystem(root, fail=('-Q',)), 'linux-m4-omarchy is not installed'),
            (lambda: None, lambda root: FakeSystem(root, fail=('--add',)), 'could not add'),
            (lambda: None, lambda root: FakeSystem(root, fail=('--lsign-key',)), 'could not trust'),
            (lambda: self.put(MODULE.CONF, AIR_CONF.replace('[core]', '[mine]\n[core]')), FakeSystem,
             'repositories before'),
            (lambda: self.put(MODULE.CONF, AIR_CONF + '[omarchy-m4]\n'), FakeSystem, 'already defines'),
        )
        for prepare, system, message in cases:
            with self.subTest(message):
                self.setUp()
                prepare()
                before = (self.root / MODULE.CONF).read_text()
                with self.assertRaisesRegex(MODULE.Refused, message):
                    self.setup_with(system(self.root))
                self.assertEqual((self.root / MODULE.CONF).read_text(), before)
                self.assertFalse((self.root / MODULE.SNIPPET).exists())

    def test_failed_download_or_install_restores_pacman_conf(self):
        for word, message in (('-Sy', 'Could not download'), ('--needed', 'Package installation failed')):
            with self.subTest(word):
                self.setUp()
                with self.assertRaisesRegex(MODULE.Refused, message + '.*pacman.conf was restored'):
                    self.setup_with(FakeSystem(self.root, fail=(word,)))
                self.assertEqual((self.root / MODULE.CONF).read_text(), AIR_CONF)
                self.assertFalse((self.root / MODULE.SNIPPET).exists())

    def test_query_failures_refuse_before_install_and_restore_config(self):
        # -Quq exit 1 is the no-update answer only when both streams are empty.
        # A diagnostic, a nonempty body, or any other status fails closed.
        cases = (
            ('installed query failed', {'-Qq': (1, '', '')}, 'Could not read installed packages', False),
            ('installed diagnostic', {'-Qq': (0, 'linux-m4-omarchy\n', 'error: could not open database\n')},
             'Could not read installed packages', False),
            ('installed name garbage', {'-Qq': (0, 'not a package name\n', '')},
             'Could not read installed packages', False),
            ('update diagnostic', {'-Quq': (1, '', 'error: failed to synchronize all databases\n')},
             'Could not read package updates', True),
            ('update exit 1 with names', {'-Quq': (1, 'linux-m4-omarchy\n', '')},
             'Could not read package updates', True),
            ('update warning on success', {'-Quq': (0, 'omarchy-m4-gpu\n', 'warning: database file\n')},
             'Could not read package updates', True),
            ('update other status', {'-Quq': (2, '', '')}, 'Could not read package updates', True),
        )
        for label, queries, message, saw_updates in cases:
            with self.subTest(label):
                self.setUp()
                system = FakeSystem(self.root, queries=queries)
                with self.assertRaisesRegex(MODULE.Refused, message + '.*pacman.conf was restored'):
                    self.setup_with(system)
                self.assertTrue(any('-Sy' in command for command in system.commands))
                self.assertTrue(any('-Qq' in command for command in system.commands))
                self.assertEqual(any('-Quq' in command for command in system.commands), saw_updates)
                self.assertFalse(any('-S' in command for command in system.commands))
                self.assertFalse(any('m4-boot-compose' in ' '.join(map(str, command))
                                     for command in system.commands))
                self.assertEqual((self.root / MODULE.CONF).read_text(), AIR_CONF)
                self.assertFalse((self.root / MODULE.SNIPPET).exists())

    def assert_pending_boot_wording(self, message):
        lowered = message.lower()
        self.assertNotIn('unchanged', lowered)
        self.assertNotIn('not changed', lowered)
        self.assertNotIn('keeps booting', lowered)
        self.assertIn('boot', lowered)
        self.assertIn('resume', lowered)
        self.assertIn('pending operation', lowered)
        self.assertIn('m4-boot-compose', message)
        after_resume = lowered[lowered.rfind('resume'):]
        self.assertRegex(after_resume, r'rerun omarchy-m4-update')

    def test_install_failure_after_boot_write_keeps_the_partial_transition(self):
        # Package install runs the boot hook. The hook can write boot.bin and
        # then fail verification; the refusal must not claim the file is unchanged.
        with self.assertRaises(MODULE.Refused) as caught:
            self.setup_with(FakeSystem(self.root, boot_then_fail=('--needed',)))
        self.assertEqual((self.root / MODULE.BOOT).read_bytes(), b'partial-transition')
        self.assertEqual((self.root / MODULE.CONF).read_text(), AIR_CONF)
        self.assert_pending_boot_wording(str(caught.exception))

    def test_composer_refusal_after_boot_write_keeps_the_partial_transition(self):
        # Composer can replace boot.bin and then fail journal cleanup. The
        # refusal must not claim the machine still boots the previous image.
        with self.assertRaises(MODULE.Refused) as caught:
            self.setup_with(FakeSystem(self.root, boot_then_fail=('m4-boot-compose',)))
        self.assertEqual((self.root / MODULE.BOOT).read_bytes(), b'partial-transition')
        self.assert_pending_boot_wording(str(caught.exception))

    def test_existing_snippet_is_put_back_after_a_failure(self):
        self.put(MODULE.SNIPPET, '[omarchy-m4]\nServer = https://old.invalid\n')
        with self.assertRaises(MODULE.Refused):
            self.setup_with(FakeSystem(self.root, fail=('-Sy',)))
        self.assertEqual((self.root / MODULE.SNIPPET).read_text(), '[omarchy-m4]\nServer = https://old.invalid\n')

    def test_retry_completes_the_boot_update(self):
        system = FakeSystem(self.root, boot_status='updated; takes effect at next boot')
        result = self.setup_with(system)
        self.assertEqual(result['boot'], 'updated; takes effect at next boot')
        compose = [c for c in system.commands if 'm4-boot-compose' in ' '.join(map(str, c))]
        self.assertEqual(compose[0][-1], '--apply')

    def test_production_commands(self):
        machine = MODULE.Machine(Path('/'), io.StringIO())
        system = FakeSystem(self.root)
        with mock.patch.object(MODULE.subprocess, 'run', system), \
             mock.patch.dict(MODULE.os.environ, {'SUDO_USER': 'owner'}):
            machine.pacman('-S', '--needed', '--noconfirm', *MODULE.PACKAGES)
            machine.pacman_key('--lsign-key', MODULE.FINGERPRINT)
            machine.compose()
            machine.start_notifier()
        install, key, compose, timer = system.commands
        self.assertEqual(install, ['pacman', '--config', '/etc/pacman.conf', '-S', '--needed', '--noconfirm',
                                   *MODULE.PACKAGES])
        self.assertEqual(key, ['pacman-key', '--config', '/etc/pacman.conf', '--lsign-key', MODULE.FINGERPRINT])
        self.assertEqual(compose, ['/usr/bin/m4-boot-compose', '--apply'])
        self.assertEqual(timer, ['systemctl', '--user', '--machine', 'owner@.host', 'start',
                                 'omarchy-m4-update-check.timer'])

    def test_channel_constants_match_the_repository(self):
        keyring = ROOT / 'packaging/omarchy-m4/omarchy-m4-keyring/omarchy-m4.gpg'
        self.assertEqual(MODULE.PUBLIC_KEY, keyring.read_text())
        self.assertEqual(MODULE.FINGERPRINT, CHANNEL['signing_fingerprint'])
        self.assertEqual(MODULE.SERVER, CHANNEL['server'])
        self.assertEqual(MODULE.REPOSITORY, CHANNEL['repository'])
        self.assertEqual(list(MODULE.PACKAGES), CHANNEL['packages'])

    def test_keyring_made_readable_for_users(self):
        gnupg = self.root / MODULE.GNUPG
        private = gnupg / 'private-keys-v1.d'
        private.mkdir(exist_ok=True)
        private.chmod(0o700)
        secret = private / 'secret.key'
        secret.write_bytes(b'private')
        secret.chmod(0o600)
        kbx = gnupg / 'pubring.kbx'
        kbx.write_bytes(b'public')
        kbx.chmod(0o600)
        result = self.setup_with(FakeSystem(self.root))
        self.assertTrue(result['keyring_readable'])
        self.assertEqual(gnupg.stat().st_mode & 0o777, 0o755)
        for name in ('pubring.gpg', 'pubring.kbx', 'trustdb.gpg'):
            self.assertEqual((gnupg / name).stat().st_mode & 0o777, 0o644, name)
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)
        self.assertEqual(secret.stat().st_mode & 0o777, 0o600)

    def test_symlinked_keyring_material_is_never_followed(self):
        gnupg = self.root / MODULE.GNUPG
        outside = self.root / 'outside'
        outside.mkdir(exist_ok=True)
        target = outside / 'real-pubring.gpg'
        target.write_bytes(b'not ours')
        target.chmod(0o600)
        (gnupg / 'pubring.gpg').unlink()
        (gnupg / 'pubring.gpg').symlink_to(target)
        result = self.setup_with(FakeSystem(self.root))
        self.assertTrue(result['keyring_readable'])
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertEqual(target.read_bytes(), b'not ours')
        self.assertEqual(gnupg.stat().st_mode & 0o777, 0o755)

    def test_symlinked_keyring_dir_refuses(self):
        gnupg = self.root / MODULE.GNUPG
        real = self.root / 'real-gnupg'
        real.mkdir(exist_ok=True)
        for path in sorted(gnupg.rglob('*')):
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                path.rmdir()
        gnupg.rmdir()
        gnupg.symlink_to(real)
        with self.assertRaisesRegex(MODULE.Refused, 'Missing /' + MODULE.GNUPG):
            self.setup_with(FakeSystem(self.root))
        self.assertEqual((self.root / MODULE.CONF).read_text(), AIR_CONF)
        self.assertFalse((self.root / MODULE.SNIPPET).exists())

    def test_inspection_leaves_keyring_alone(self):
        gnupg = self.root / MODULE.GNUPG
        result = self.setup_with(FakeSystem(self.root), apply=False)
        self.assertNotIn('keyring_readable', result)
        self.assertEqual(gnupg.stat().st_mode & 0o777, 0o700)
        self.assertEqual((gnupg / 'pubring.gpg').stat().st_mode & 0o777, 0o644)

    def test_missing_keyring_dir_refuses_before_changes(self):
        gnupg = self.root / MODULE.GNUPG
        for path in sorted(gnupg.rglob('*')):
            path.unlink()
        gnupg.rmdir()
        with self.assertRaisesRegex(MODULE.Refused, 'Missing /' + MODULE.GNUPG):
            self.setup_with(FakeSystem(self.root))
        self.assertEqual((self.root / MODULE.CONF).read_text(), AIR_CONF)
        self.assertFalse((self.root / MODULE.SNIPPET).exists())

    def test_held_boot_status_matches_the_composer(self):
        """The unpatched held strings match, and that status does not promise a reboot."""
        import test_m4_boot_compose as boot_tests
        self.assertEqual(MODULE.HELD_BOOT, boot_tests.MODULE.HELD_STATUS)
        self.assertNotIn('restart', MODULE.HELD_BOOT)
        with mock.patch.object(MODULE.Machine, 'compose', return_value=MODULE.HELD_BOOT):
            result = self.setup_with(FakeSystem(self.root))
        self.assertEqual((result['boot'], result['restart_needed']), (MODULE.HELD_BOOT, False))
        self.assertEqual(result['status'], 'connected; boot updates remain held')
        self.assertNotIn('restart', result['status'])

    def test_held_boot_still_restarts_for_pending_stock_gpu(self):
        """A known alpha.1 GPU swap still needs a restart while boot updates stay held."""
        alpha1 = b'alpha.1 gpu module'
        with mock.patch.object(MODULE, 'GPU_STOCK', MODULE.hashlib.sha256(alpha1).hexdigest()), \
             mock.patch.object(MODULE.Machine, 'compose', return_value=MODULE.HELD_BOOT):
            (self.root / MODULE.GPU_MODULE).parent.mkdir(parents=True, exist_ok=True)
            (self.root / MODULE.GPU_MODULE).write_bytes(alpha1)
            self.put(MODULE.GPU_STAGED, 'fixed module')
            self.assertTrue(MODULE.gpu_pending(self.root))
            result = self.setup_with(FakeSystem(self.root))
        self.assertEqual(result['boot'], MODULE.HELD_BOOT)
        self.assertTrue(result['restart_needed'])
        self.assertEqual(result['status'],
                         'connected; restart once to apply the other update; boot updates remain held')

    def test_unresolved_boot_recovery_is_not_held_success(self):
        """Recovery wording is not an accepted boot status."""
        status = ('Recovery needed: boot updates are held but the video pin does not match '
                  'boot.bin. Complete the explicit rollback with sudo m4-boot-compose --rollback --apply')
        with mock.patch.object(MODULE.Machine, 'compose', return_value=status):
            with self.assertRaises(MODULE.Refused) as caught:
                self.setup_with(FakeSystem(self.root))
        self.assertIn('boot.bin is not in step', str(caught.exception))
        self.assertIn(status, str(caught.exception))
        self.assertNotIn(MODULE.HELD_BOOT, str(caught.exception))


if __name__ == '__main__':
    unittest.main()
