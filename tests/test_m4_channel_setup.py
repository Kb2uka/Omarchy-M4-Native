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

    def __init__(self, root, fail=(), boot_status='current', change_boot=False, installed=('linux-m4-omarchy',)):
        self.root, self.fail, self.status, self.change_boot = root, fail, boot_status, change_boot
        self.installed = installed
        self.commands, self.refresh_config = [], None

    def __call__(self, command, check=False, capture_output=False, text=False, stdout=None, stderr=None):
        self.commands.append(command)
        if 'm4-boot-compose' in ' '.join(map(str, command)):
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

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

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

    def test_restart_reported_when_a_speaker_swap_is_pending(self):
        self.put(MODULE.SPEAKER_MODULE, 'alpha.1 module')
        self.put(MODULE.SPEAKER_STAGED, 'fixed module')
        result = self.setup_with(FakeSystem(self.root))
        self.assertEqual((result['restart_needed'], result['status']),
                         (True, 'connected; restart once to apply the update'))
        self.put(MODULE.SPEAKER_HOLD, '')
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'held: nothing applies')
        (self.root / MODULE.SPEAKER_HOLD).unlink()
        self.put(MODULE.SPEAKER_MODULE, 'fixed module')
        self.assertFalse(self.setup_with(FakeSystem(self.root))['restart_needed'], 'already swapped')

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


if __name__ == '__main__':
    unittest.main()
