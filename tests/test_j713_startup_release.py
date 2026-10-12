# SPDX-License-Identifier: MIT
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('startup_release', ROOT / 'tools/prepare-j713-release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class StartupReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / 'root'
        self.name = 'usr/lib/omarchy/m4-j713-audio-register.py'
        self.original = b'retained registration helper'
        self.pin = hashlib.sha256(self.original).hexdigest()
        self.unit = self.root / 'etc/systemd/system/m4-gpu.service'
        self.unit.parent.mkdir(parents=True)
        self.unit.write_bytes(b'retained graphics unit')
        self.helper = self.root / self.name
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(self.original)
        self.helper.chmod(0o644)
        self.manifest = {
            'files': {'/' + self.name: self.pin, '/untouched': 'f' * 64},
            'file_metadata': {self.name: dict(sha256=self.pin, size=len(self.original), mode=0o644)},
            'modules': {'unchanged': 'note'}, 'readiness_inputs': {'unchanged': True},
        }
        self.audio_unit_name = 'usr/lib/systemd/system/m4-j713-audio-register.service'
        self.audio_unit = self.root / self.audio_unit_name
        self.audio_unit.parent.mkdir(parents=True, exist_ok=True)
        self.audio_unit.write_bytes(b'retained audio unit')
        self.audio_unit.chmod(0o644)
        self.audio_unit_pin = hashlib.sha256(self.audio_unit.read_bytes()).hexdigest()
        self.manifest['files']['/' + self.audio_unit_name] = self.audio_unit_pin
        self.manifest['file_metadata'][self.audio_unit_name] = dict(
            sha256=self.audio_unit_pin, size=self.audio_unit.stat().st_size, mode=0o644)
        self.configure = mock.Mock()
        self.configure_owner = mock.Mock()

    def prepare(self):
        with mock.patch.object(release, 'BASE_GPU_UNIT', hashlib.sha256(b'retained graphics unit').hexdigest()), \
             mock.patch.object(release, 'BASE_AUDIO_REGISTER', self.pin), \
             mock.patch.object(release, 'BASE_AUDIO_UNIT', self.audio_unit_pin), \
             mock.patch.object(release.runpy, 'run_path', return_value={
                 'apply': self.configure, 'configure': self.configure_owner}):
            return json.loads(release.prepare_startup(self.root, ROOT, json.dumps(self.manifest).encode()))

    def test_replacement_matches_both_runtime_maps_and_frozen_source(self):
        before = copy.deepcopy(self.manifest)
        result = self.prepare()
        self.configure.assert_called_once_with(self.root, ROOT)
        self.configure_owner.assert_called_once_with(self.root, apply=True)
        raw = (ROOT / 'tools/m4-j713-audio-register.py').read_bytes()
        self.assertEqual(self.helper.read_bytes(), raw)
        self.assertEqual(self.unit.read_bytes(), (ROOT / 'packaging/j713-startup/m4-gpu.service').read_bytes())
        pin = hashlib.sha256(raw).hexdigest()
        expected = copy.deepcopy(before)
        expected['files']['/' + self.name] = pin
        expected['file_metadata'][self.name] = dict(sha256=pin, size=len(raw), mode=0o644)
        unit_raw = (ROOT / 'packaging/j713-audio/m4-j713-audio-register.service').read_bytes()
        self.assertEqual(self.audio_unit.read_bytes(), unit_raw)
        unit_pin = hashlib.sha256(unit_raw).hexdigest()
        expected['files']['/' + self.audio_unit_name] = unit_pin
        expected['file_metadata'][self.audio_unit_name] = dict(
            sha256=unit_pin, size=len(unit_raw), mode=0o644)
        self.assertEqual(result, expected)
        self.assertEqual(self.manifest, before)

    def test_retained_registration_tamper_refused_before_writes(self):
        self.helper.write_bytes(b'foreign')
        with self.assertRaisesRegex(RuntimeError, 'retained audio registration'):
            self.prepare()
        self.configure.assert_not_called()
        self.assertEqual(self.unit.read_bytes(), b'retained graphics unit')

    def test_real_configurators_are_carried_into_the_release_image(self):
        from tests.test_j713_first_boot import GETTY, AUTOVT, TTY2
        from tests.test_j713_owner_setup import RETAINED_HELPER, owner_setup
        for name, content in {
            'etc/m4-root-id': b'image-id',
            'var/lib/omarchy/provisioning/pending': b'',
            'usr/lib/systemd/system/getty@.service': GETTY.encode(),
            owner_setup.FRAMEWORK_HELPER: RETAINED_HELPER,
        }.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            path.chmod(0o644)
        with mock.patch.object(release, 'BASE_GPU_UNIT', hashlib.sha256(b'retained graphics unit').hexdigest()), \
             mock.patch.object(release, 'BASE_AUDIO_REGISTER', self.pin), \
             mock.patch.object(release, 'BASE_AUDIO_UNIT', self.audio_unit_pin):
            result = json.loads(release.prepare_startup(self.root, ROOT, json.dumps(self.manifest).encode()))
        self.assertTrue((self.root / AUTOVT).is_symlink())
        self.assertTrue((self.root / TTY2).is_symlink())
        self.assertEqual((self.root / owner_setup.FRAMEWORK_HELPER).read_bytes(),
                         owner_setup.ARM64_GUARD + RETAINED_HELPER)
        self.assertEqual((self.root / 'usr/lib/omarchy/m4-boot-status.py').read_bytes(),
                         (ROOT / 'tools/m4-boot-status.py').read_bytes())
        self.assertEqual(result['files']['/' + self.name],
                         hashlib.sha256(self.helper.read_bytes()).hexdigest())
        self.assertEqual(result['files']['/' + self.audio_unit_name],
                         hashlib.sha256(self.audio_unit.read_bytes()).hexdigest())

    def test_foreign_audio_unit_refused_before_any_startup_writes(self):
        self.audio_unit.write_bytes(b'foreign audio unit')
        with self.assertRaisesRegex(RuntimeError, 'retained audio registration'):
            self.prepare()
        self.configure.assert_not_called()
        self.assertEqual(self.helper.read_bytes(), self.original)
        self.assertEqual(self.unit.read_bytes(), b'retained graphics unit')

    def test_audio_unit_identity_mismatch_is_refused(self):
        self.manifest['files']['/' + self.audio_unit_name] = 'a' * 64
        with self.assertRaisesRegex(RuntimeError, 'maps disagree'):
            self.prepare()
        self.configure.assert_not_called()
        self.assertEqual(self.audio_unit.read_bytes(), b'retained audio unit')

    def test_inconsistent_audio_map_refused_before_writes(self):
        for name, value in [('sha256', 'e' * 64), ('size', 1), ('mode', 0o777)]:
            original = copy.deepcopy(self.manifest)
            self.manifest['file_metadata'][self.name][name] = value
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'maps disagree'):
                self.prepare()
            self.assertEqual(self.helper.read_bytes(), self.original)
            self.configure.assert_not_called()
            self.manifest = original

    def test_foreign_graphics_unit_refused(self):
        self.unit.write_bytes(b'custom')
        with self.assertRaisesRegex(RuntimeError, 'graphics unit'):
            self.prepare()
        self.assertEqual(self.helper.read_bytes(), self.original)

    def test_link_cannot_replace_registration_target(self):
        self.helper.unlink()
        self.helper.symlink_to(self.unit)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.prepare()
        self.assertEqual(self.unit.read_bytes(), b'retained graphics unit')


class DecoderReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.name = 'usr/local/bin/m4-video-start'
        self.helper = self.root / self.name
        self.helper.parent.mkdir(parents=True)
        self.helper.write_bytes(b'retained decoder helper')
        self.helper.chmod(0o755)
        self.pin = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        self.manifest = {'files': {self.name: dict(sha256=self.pin,
                          size=self.helper.stat().st_size, mode=0o755),
                          'untouched': dict(sha256='a' * 64, size=123, mode=0o644)},
                         'board': 'apple,j713', 'firmware_sha256': 'b' * 64}
        self.metadata = self.root / 'etc/omarchy/j713-video-manifest.json'
        self.metadata.parent.mkdir(parents=True)
        self.metadata.write_text(json.dumps(self.manifest))
        self.manifest_pin = hashlib.sha256(self.metadata.read_bytes()).hexdigest()

    def prepare(self):
        with mock.patch.object(release, 'BASE_VIDEO', self.manifest_pin), \
                mock.patch.object(release, 'BASE_VIDEO_START', self.pin):
            return release.prepare_decoder(self.root, ROOT)

    def test_installed_decoder_identity_and_outer_manifest_hash_match(self):
        result = self.prepare()
        raw = (ROOT / 'packaging/j713-startup/m4-video-start').read_bytes()
        self.assertEqual(self.helper.read_bytes(), raw)
        self.assertEqual(self.helper.stat().st_mode & 0o777, 0o755)
        expected = copy.deepcopy(self.manifest)
        expected['files'][self.name] = dict(sha256=hashlib.sha256(raw).hexdigest(),
                                           size=len(raw), mode=0o755)
        self.assertEqual(json.loads(self.metadata.read_bytes()), expected)
        self.assertEqual(result, hashlib.sha256(self.metadata.read_bytes()).hexdigest())
        self.assertNotEqual(result, self.manifest_pin)

    def test_changed_manifest_refuses_before_helper_write(self):
        self.metadata.write_text('{}')
        with self.assertRaisesRegex(RuntimeError, 'video manifest'):
            self.prepare()
        self.assertEqual(self.helper.read_bytes(), b'retained decoder helper')

    def test_changed_helper_or_mode_refuses_before_manifest_write(self):
        original = self.metadata.read_bytes()
        for mode, raw in ((0o755, b'changed'), (0o644, b'retained decoder helper')):
            self.helper.write_bytes(raw)
            self.helper.chmod(mode)
            with self.subTest(mode=mode), self.assertRaisesRegex(RuntimeError, 'decoder startup'):
                self.prepare()
            self.assertEqual(self.metadata.read_bytes(), original)

    def test_inconsistent_retained_helper_record_refuses(self):
        self.manifest['files'][self.name]['size'] = 1
        self.metadata.write_text(json.dumps(self.manifest))
        self.manifest_pin = hashlib.sha256(self.metadata.read_bytes()).hexdigest()
        with self.assertRaisesRegex(RuntimeError, 'identity map'):
            self.prepare()
        self.assertEqual(self.helper.read_bytes(), b'retained decoder helper')

    def test_link_cannot_replace_decoder_target(self):
        self.helper.unlink()
        self.helper.symlink_to(self.metadata)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.prepare()
        self.assertEqual(hashlib.sha256(self.metadata.read_bytes()).hexdigest(), self.manifest_pin)


if __name__ == '__main__':
    unittest.main()
