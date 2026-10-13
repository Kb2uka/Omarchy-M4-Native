# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""M4 update notifier: read-only, M4 packages only, one notification per new version set."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tarfile
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_update_check', ROOT / 'tools/m4-update-check.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def database(**packages):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        for name, version in packages.items():
            data = f'%FILENAME%\n{name}-{version}.pkg.tar.zst\n\n%NAME%\n{name}\n\n%VERSION%\n{version}\n'.encode()
            info = tarfile.TarInfo(f'{name}-{version}/desc')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def vercmp(a, b):
    """Enough of pacman's ordering for these fixtures (numeric pkgrel)."""
    key = lambda v: [int(p) if p.isdigit() else p for p in v.replace('-', '.').split('.')]
    return (key(a) > key(b)) - (key(a) < key(b))


class FakeSystem:
    def __init__(self, installed):
        self.installed, self.notifications, self.commands = installed, [], []

    def __call__(self, command, capture_output=False, text=False, check=False):
        self.commands.append(command)
        if command[0] == 'pacman':
            lines = [f'{n} {self.installed[n]}' for n in command if n in self.installed]
            return types.SimpleNamespace(returncode=0, stdout='\n'.join(lines) + '\n')
        if command[0] == 'vercmp':
            return types.SimpleNamespace(returncode=0, stdout=str(vercmp(command[1], command[2])))
        if command[0] == 'notify-send':
            self.notifications.append(command)
            return types.SimpleNamespace(returncode=0, stdout='')
        raise AssertionError('unexpected command ' + ' '.join(command))


class UpdateCheckTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.snippet = self.root / 'omarchy-m4.conf'
        self.snippet.write_text('[omarchy-m4]\nSigLevel = Required DatabaseRequired\n'
                                'Server = https://example.invalid/packages\n')
        self.state = self.root / 'state/notified'

    def run_check(self, db, installed, *args):
        system = FakeSystem(installed)
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = db
        out = io.StringIO()
        with mock.patch.object(MODULE.subprocess, 'run', system), \
             mock.patch.object(MODULE.urllib.request, 'urlopen', return_value=response) as urlopen, \
             mock.patch.object(MODULE.shutil, 'which', return_value='/usr/bin/notify-send'), \
             contextlib.redirect_stdout(out):
            code = MODULE.main(['--snippet', str(self.snippet), '--state', str(self.state), *args])
        return code, out.getvalue(), system, urlopen

    def test_reports_newer_installed_packages_once(self):
        db = database(**{'linux-m4-omarchy': '7.1.9.m4.20261008-3', 'omarchy-m4-boot': '1-1',
                         'omarchy-m4-keyring': '20261012-1'})
        installed = {'linux-m4-omarchy': '7.1.9.m4.20261008-2', 'omarchy-m4-boot': '1-1',
                     'omarchy-m4-keyring': '20261012-1'}
        code, out, system, urlopen = self.run_check(db, installed, '--notify')
        self.assertEqual(code, 0)
        self.assertIn('linux-m4-omarchy 7.1.9.m4.20261008-3', out)
        self.assertEqual(urlopen.call_args[0][0], 'https://example.invalid/packages/omarchy-m4.db')
        self.assertEqual(len(system.notifications), 1)
        self.assertEqual(system.notifications[0][2], 'M4 update available')
        self.assertIn('Run omarchy-m4-update, then restart.', system.notifications[0][3])
        self.assertNotIn('omarchy-m4-boot', system.notifications[0][3])
        _, _, again, _ = self.run_check(db, installed, '--notify')
        self.assertEqual(again.notifications, [])

    def test_up_to_date_clears_the_reminder(self):
        db = database(**{'omarchy-m4-boot': '1-1'})
        self.state.parent.mkdir(parents=True)
        self.state.write_text('omarchy-m4-boot 1-1\n')
        code, out, system, _ = self.run_check(db, {'omarchy-m4-boot': '1-1'}, '--notify')
        self.assertEqual((code, out.strip(), system.notifications), (0, 'M4 packages are up to date.', []))
        self.assertFalse(self.state.exists())

    def test_never_offers_packages_that_are_not_installed(self):
        db = database(**{'omarchy-m4-boot': '2-1'})
        code, out, system, _ = self.run_check(db, {}, '--notify')
        self.assertEqual((out.strip(), system.notifications), ('M4 packages are up to date.', []))

    def test_only_reads(self):
        db = database(**{'omarchy-m4-boot': '2-1'})
        _, _, system, _ = self.run_check(db, {'omarchy-m4-boot': '1-1'}, '--notify')
        for command in system.commands:
            self.assertIn(command[0], ('pacman', 'vercmp', 'notify-send'))
            if command[0] == 'pacman':
                self.assertEqual(command[1], '-Q')

    def test_not_connected_or_offline_is_quiet(self):
        self.snippet.unlink()
        code, out, system, urlopen = self.run_check(database(), {}, '--notify')
        self.assertEqual((code, out.strip()), (0, 'Not connected to the M4 package channel.'))
        urlopen.assert_not_called()
        self.snippet.write_text('[omarchy-m4]\nServer = https://example.invalid/packages\n')
        with mock.patch.object(MODULE.urllib.request, 'urlopen', side_effect=OSError('offline')), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(MODULE.main(['--snippet', str(self.snippet), '--state', str(self.state)]), 0)
        self.assertIn('offline', err.getvalue())

    def test_only_https_servers(self):
        self.snippet.write_text('[omarchy-m4]\nServer = http://example.invalid/packages\n')
        code, out, _, urlopen = self.run_check(database(), {})
        self.assertEqual(out.strip(), 'Not connected to the M4 package channel.')
        urlopen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
