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
        self.configure = mock.Mock()
        self.configure_owner = mock.Mock()

    def prepare(self):
        with mock.patch.object(release, 'BASE_GPU_UNIT', hashlib.sha256(b'retained graphics unit').hexdigest()), \
             mock.patch.object(release, 'BASE_AUDIO_REGISTER', self.pin), \
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
             mock.patch.object(release, 'BASE_AUDIO_REGISTER', self.pin):
            result = json.loads(release.prepare_startup(self.root, ROOT, json.dumps(self.manifest).encode()))
        self.assertTrue((self.root / AUTOVT).is_symlink())
        self.assertTrue((self.root / TTY2).is_symlink())
        self.assertEqual((self.root / owner_setup.FRAMEWORK_HELPER).read_bytes(),
                         owner_setup.ARM64_GUARD + RETAINED_HELPER)
        self.assertEqual((self.root / 'usr/lib/omarchy/m4-boot-status.py').read_bytes(),
                         (ROOT / 'tools/m4-boot-status.py').read_bytes())
        self.assertEqual(result['files']['/' + self.name],
                         hashlib.sha256(self.helper.read_bytes()).hexdigest())

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


if __name__ == '__main__':
    unittest.main()
