# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Channel setup: trust the key, add the repository first, install without a system upgrade."""
import importlib.util
import io
import json
from pathlib import Path
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

# Synthetic module bytes. Scenario tests patch the SHA constants to these digests.
SPEAKER_STOCK_BYTES = b'speaker-stock-module'
SPEAKER_FIXED_BYTES = b'speaker-fixed-module'
MCA_STOCK_BYTES = b'mca-stock-module'
MCA_FIXED_BYTES = b'mca-fixed-module'
UNKNOWN_AUDIO = b'unknown-audio-module'
APPLY_STATUS = 'connected; restart once to apply the update'
QUIET_STATUS = 'connected; future M4 fixes arrive with omarchy-m4-update'


def audio_sha(data):
    return MODULE.hashlib.sha256(data).hexdigest()

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
    """Records commands; answers like a machine with alpha.1 installed."""

    def __init__(self, root, fail=(), boot_status='current', change_boot=False, installed=('linux-m4-omarchy',),
                 boot_then_fail=()):
        self.root, self.fail, self.status, self.change_boot = root, fail, boot_status, change_boot
        self.installed, self.boot_then_fail = installed, boot_then_fail
        self.commands, self.refresh_config = [], None

    def __call__(self, command, check=False, capture_output=False, text=False, stdout=None, stderr=None):
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
        failed = any(word in command for word in self.fail)
        if '-Q' in command and command[-1] not in self.installed:
            failed = True
        return types.SimpleNamespace(returncode=1 if failed else 0, stdout='', stderr='')


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

    def test_connects_without_a_system_upgrade(self):
        system = FakeSystem(self.root)
        result = self.setup_with(system)
        self.assertEqual((result['status'], result['restart_needed']),
                         ('connected; future M4 fixes arrive with omarchy-m4-update', False))
        pacman = [c for c in system.commands if c[0] == 'pacman']
        self.assertEqual(pacman[0][-2:], ['-Q', 'linux-m4-omarchy'])
        self.assertEqual(pacman[1][-2:], ['-Q', 'gst-plugins-good'])
        self.assertEqual(pacman[2][-1], '-Sy')
        expected = ['-S', '--needed', '--noconfirm', *CHANNEL['packages'], '--noscriptlet']
        self.assertEqual(pacman[3][-len(expected):], expected)
        for command in pacman:
            self.assertFalse(any(arg.startswith('-') and not arg.startswith('--') and 'u' in arg for arg in command),
                             'setup must never run a system upgrade: ' + ' '.join(command))
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

    def _patch_audio_identities(self):
        """Patch only the four SHA identities. Path constants stay as shipped."""
        return mock.patch.multiple(
            MODULE, create=True,
            SPEAKER_STOCK=audio_sha(SPEAKER_STOCK_BYTES),
            SPEAKER_FIXED=audio_sha(SPEAKER_FIXED_BYTES),
            MCA_STOCK=audio_sha(MCA_STOCK_BYTES),
            MCA_FIXED=audio_sha(MCA_FIXED_BYTES))

    def _audio_identities(self, name):
        """Read the SHA constants on each call so a fixture patch is visible."""
        if name == 'speaker':
            stock_bytes, fixed_bytes = SPEAKER_STOCK_BYTES, SPEAKER_FIXED_BYTES
            stock_sha, fixed_sha = MODULE.SPEAKER_STOCK, MODULE.SPEAKER_FIXED
        elif name == 'mca':
            stock_bytes, fixed_bytes = MCA_STOCK_BYTES, MCA_FIXED_BYTES
            stock_sha, fixed_sha = MODULE.MCA_STOCK, MODULE.MCA_FIXED
        else:
            raise AssertionError(name)
        return stock_bytes, fixed_bytes, stock_sha, fixed_sha

    def _assert_patched_identities(self):
        for name in ('speaker', 'mca'):
            stock_bytes, fixed_bytes, stock_sha, fixed_sha = self._audio_identities(name)
            self.assertEqual(stock_sha, audio_sha(stock_bytes))
            self.assertEqual(fixed_sha, audio_sha(fixed_bytes))
            self.assertNotEqual(stock_sha, fixed_sha)
            self.assertNotIn(audio_sha(UNKNOWN_AUDIO), (stock_sha, fixed_sha))

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

    def _write_files_pin(self, rel, module_rel, pin, release, pin_path=None, merge=False):
        """Write the files pin selected by exact path. A second call keeps the other pin."""
        key = '/' + (module_rel if pin_path is None else pin_path)
        path = self.root / rel
        document = {'kernel_release': release, 'files': {key: pin}}
        if merge and path.is_file() and not path.is_symlink():
            try:
                existing = json.loads(path.read_text())
            except (ValueError, UnicodeError):
                existing = None
            files = existing.get('files') if isinstance(existing, dict) else None
            if isinstance(files, dict):
                document['files'] = dict(files)
                document['files'][key] = pin
        self.put(rel, json.dumps(document, indent=2, sort_keys=True) + '\n')

    def _plant_backup(self, name, kind, module_rel, stock_bytes, stock_sha, fixed_sha, release):
        """Stock backup is the module hash plus that module's files pin. Not a full manifest copy."""
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
            self._write_files_pin(manifest_backup, module_rel, stock_sha, release)
            return
        if kind == 'stock':
            data, pin = stock_bytes, stock_sha
        elif kind == 'wrong-module':
            data, pin = b'not-the-stock-module', stock_sha
        elif kind == 'wrong-pin':
            data, pin = stock_bytes, fixed_sha
        else:
            raise AssertionError(kind)
        self.put_bytes(module_backup, data)
        self._write_files_pin(manifest_backup, module_rel, pin, release)

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

    def plant_audio_state(self, name, installed, pin, staged=None, release=None, backup=None,
                          hold=False, rollback=False, manifest='present', module='file', pin_path=None):
        """Plant one component's bytes, files pin, stage, and optional stock backup."""
        module_rel, staged_rel, hold_rel, rollback_rel = self.component_paths(name)
        stock_bytes, fixed_bytes, stock_sha, fixed_sha = self._audio_identities(name)
        payloads = {'stock': stock_bytes, 'fixed': fixed_bytes, 'unknown': UNKNOWN_AUDIO}
        if installed not in payloads:
            raise AssertionError(installed)
        pins = {'stock': stock_sha, 'fixed': fixed_sha, 'unknown': audio_sha(b'not-a-known-pin'),
                'matches': audio_sha(payloads[installed])}
        if pin not in pins:
            raise AssertionError(pin)
        chosen_release = release or self._release_of(module_rel)
        self._place_bytes(module_rel, payloads[installed], module)
        self._plant_staged(staged_rel, staged, stock_bytes, fixed_bytes)
        manifest_rel = MODULE.MANIFESTS[1]
        if manifest == 'missing':
            (self.root / manifest_rel).unlink(missing_ok=True)
        elif manifest == 'malformed':
            self.put(manifest_rel, '{')
        elif manifest == 'present':
            self._write_files_pin(manifest_rel, module_rel, pins[pin], chosen_release,
                                  pin_path=pin_path, merge=True)
        else:
            raise AssertionError(manifest)
        self._plant_backup(name, backup, module_rel, stock_bytes, stock_sha, fixed_sha, chosen_release)
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

        Fixture digests stand in for the two SHA identities. Fresh stock does not
        need a backup. Forward interruption is pending only with a stock backup
        and a regular staged file of the fixed identity. Reverse (stock module,
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

    def test_audio_sha_identities_match_the_speaker_helper(self):
        """Updater stock and fixed SHA constants are the helper's shipped pins.

        Paths and the release are compared to the helper unchanged. The SHA
        comparison is last: a missing constant is an AttributeError.
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
        self.assertEqual(MODULE.SPEAKER_STOCK, helper.STOCK['sha256'])
        self.assertEqual(MODULE.SPEAKER_FIXED, helper.FIXED['sha256'])
        self.assertEqual(MODULE.MCA_STOCK, helper.MCA_STOCK['sha256'])
        self.assertEqual(MODULE.MCA_FIXED, helper.MCA_FIXED['sha256'])

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
