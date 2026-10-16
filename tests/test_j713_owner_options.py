# SPDX-License-Identifier: MIT
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('owner_options', ROOT / 'tools/m4-j713-owner-assets.py')
owner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(owner)


class InstallerChoiceTests(unittest.TestCase):
    def test_prepare_for_installer_with_explicit_choice_returns_consent(self):
        for choice, expected in (('yes', True), ('no', False)):
            with self.subTest(choice=choice), mock.patch.dict(
                    os.environ, {'M4_OWNER_WIDEVINE': choice}, clear=True):
                self.assertIs(owner.prepare_for_installer(), expected)

    def test_prepare_for_installer_without_explicit_choice_refuses(self):
        for choice in ('', 'true', 'YES'):
            with self.subTest(choice=choice), mock.patch.dict(
                    os.environ, {'M4_OWNER_WIDEVINE': choice}, clear=True):
                with self.assertRaisesRegex(ValueError, 'prepare owner options'):
                    owner.prepare_for_installer()

    def test_add_to_firmware_with_explicit_choice_carries_only_owner_options(self):
        core = types.ModuleType('gravity_firmware.core')
        core.FWFile = lambda name, data: data
        package = mock.Mock()
        with mock.patch.dict('sys.modules', {'gravity_firmware.core': core}):
            owner.add_to_firmware(package, False)
        package.add_file.assert_called_once_with(
            owner.OPTIONS, owner.encoded({'schema': 1, 'widevine': False}))

    def test_owner_options_with_malformed_consent_refuses(self):
        for value in ({'schema': 1, 'widevine': 'yes'}, {'schema': True, 'widevine': True},
                      {'schema': 1}, {'schema': 1, 'widevine': True, 'extra': True}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, 'Invalid owner options'):
                    owner.owner_options(json.dumps(value))


class OwnerOptionInstallationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.root.chmod(0o755)
        self.pending = self.root / 'etc/skel/.config/omarchy/pending'
        self.paths = [self.pending / ('widevine-' + profile + '.json') for profile in owner.PROFILES]
        original_lstat = Path.lstat
        ancestors = set(self.root.parents)

        def root_owned_lstat(path):
            """Model root ownership without requiring privileged test execution."""
            fields = list(original_lstat(path))
            fields[4] = 0
            if path in ancestors:
                fields[0] &= ~0o022
            return os.stat_result(fields)

        patcher = mock.patch.object(Path, 'lstat', root_owned_lstat)
        patcher.start()
        self.addCleanup(patcher.stop)

    def request(self, profile):
        return owner.encoded({'schema': 1, 'profile': profile, 'consent': 'owner-requested'})

    def test_install_options_with_consent_writes_private_requests(self):
        owner.install_options(True, self.pending)
        for profile, path in zip(owner.PROFILES, self.paths):
            with self.subTest(profile=profile):
                self.assertEqual(path.read_bytes(), self.request(profile))
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(set(self.pending.iterdir()), set(self.paths))

    def test_install_options_with_installed_requests_performs_no_further_writes(self):
        owner.install_options(True, self.pending)
        with mock.patch.object(owner.os, 'fsync') as sync, \
                mock.patch.object(owner.os, 'replace') as replace:
            owner.install_options(True, self.pending)
        sync.assert_not_called()
        replace.assert_not_called()

    def test_install_options_without_consent_creates_no_requests(self):
        owner.install_options(False, self.pending)
        self.assertFalse(self.pending.exists())

    def test_install_options_with_unconsented_existing_request_refuses_without_deleting_it(self):
        self.pending.mkdir(parents=True)
        self.paths[-1].write_bytes(self.request(owner.PROFILES[-1]))
        with self.assertRaisesRegex(ValueError, 'without owner consent'):
            owner.install_options(False, self.pending)
        self.assertEqual(list(self.pending.iterdir()), [self.paths[-1]])
        self.assertEqual(self.paths[-1].read_bytes(), self.request(owner.PROFILES[-1]))

    def test_install_options_with_conflicting_last_request_refuses_before_installing_any(self):
        self.pending.mkdir(parents=True)
        self.paths[-1].write_bytes(b'custom request')
        with self.assertRaisesRegex(ValueError, 'Existing owner asset differs'):
            owner.install_options(True, self.pending)
        self.assertEqual(list(self.pending.iterdir()), [self.paths[-1]])
        self.assertEqual(self.paths[-1].read_bytes(), b'custom request')

    def test_install_options_after_interrupted_install_completes_on_retry(self):
        original_replace = os.replace
        installed = []

        def interrupt_second_file(source, destination):
            if installed:
                raise OSError('interrupted installation')
            original_replace(source, destination)
            installed.append(destination)

        with mock.patch.object(owner.os, 'replace', side_effect=interrupt_second_file):
            with self.assertRaisesRegex(OSError, 'interrupted installation'):
                owner.install_options(True, self.pending)
        self.assertEqual(list(self.pending.iterdir()), [self.paths[0]])
        owner.install_options(True, self.pending)
        for profile, path in zip(owner.PROFILES, self.paths):
            self.assertEqual(path.read_bytes(), self.request(profile))
        self.assertEqual(set(self.pending.iterdir()), set(self.paths))

    def test_install_options_with_symlinked_destination_directory_refuses_without_escape(self):
        outside = self.root / 'outside'
        outside.mkdir()
        self.pending.parent.mkdir(parents=True)
        self.pending.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Untrusted destination directory'):
            owner.install_options(True, self.pending)
        self.assertEqual(list(outside.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
