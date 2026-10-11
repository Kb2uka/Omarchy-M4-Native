# SPDX-License-Identifier: MIT
"""Exercise the closed default-policy addition against the real image installer."""
import copy
import json
import unittest
from unittest import mock

import test_j713_audio_installer as fixtures

ROOT, digest, installer, put = fixtures.ROOT, fixtures.digest, fixtures.installer, fixtures.put


ADDITIONS = {
    'etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf',
    'usr/share/wireplumber/scripts/default-nodes/j713-protected.lua',
}


class AudioPackageJoinTests(unittest.TestCase):
    setUp = fixtures.ImageTests.setUp
    write_manifest = fixtures.ImageTests.write_manifest
    configure = fixtures.ImageTests.configure

    def prepare_join(self):
        self.manifest['source_revision'] = 'original source receipt'
        self.write_manifest()
        self.configure()
        self.previous_pin = self.pin
        self.previous_raw = (self.image / installer.MANIFEST).read_bytes()
        self.previous = copy.deepcopy(self.manifest)
        for name in ADDITIONS:
            data = (ROOT / 'audio/j713/policy' / name).read_bytes()
            self.manifest['file_metadata'][name] = dict(sha256=digest(data), size=len(data), mode=0o644)
            self.manifest['files']['/' + name] = digest(data)
            put(self.package / 'root' / name, data)
        self.manifest['source_revision'] = 'original transport plus default policy candidate'
        self.write_manifest()

    def assert_unchanged(self):
        self.assertEqual((self.image / installer.MANIFEST).read_bytes(), self.previous_raw)
        for name in ADDITIONS:
            self.assertFalse((self.image / name).exists(), name)

    def test_explicit_join_apply_check_and_retry(self):
        self.prepare_join()
        with self.assertRaisesRegex(ValueError, 'Conflicting existing audio file'):
            self.configure()
        self.assert_unchanged()
        with self.assertRaisesRegex(ValueError, 'differs'):
            self.configure(previous_package_sha256=self.previous_pin, apply=False)
        self.assert_unchanged()
        result = self.configure(previous_package_sha256=self.previous_pin)
        self.assertEqual(set(result['changed_files']), ADDITIONS | {installer.MANIFEST})
        for name in self.previous['file_metadata']:
            self.assertEqual((self.image / name).read_bytes(), (self.package / 'root' / name).read_bytes())
        self.assertEqual((self.image / installer.MANIFEST).read_bytes(),
                         (self.package / 'manifest.json').read_bytes())
        self.assertEqual(self.configure(previous_package_sha256=self.previous_pin)['changed_files'], [])
        self.assertEqual(self.configure(previous_package_sha256=self.previous_pin, apply=False)['changed_files'], [])
        self.assertEqual(self.configure(apply=False)['changed_files'], [])

    def test_wrong_previous_pin_refuses_before_write(self):
        self.prepare_join()
        with self.assertRaisesRegex(ValueError, 'Previous package manifest pin'):
            self.configure(previous_package_sha256='0' * 64)
        self.assert_unchanged()

    def test_partial_addition_retry_publishes_manifest_last(self):
        self.prepare_join()
        existing = sorted(ADDITIONS)[0]
        put(self.image / existing, (self.package / 'root' / existing).read_bytes())
        order = []
        replace = installer.os.replace

        def record(source, destination):
            order.append(str(destination.relative_to(self.image)))
            replace(source, destination)

        with mock.patch.object(installer.os, 'replace', side_effect=record):
            self.configure(previous_package_sha256=self.previous_pin)
        self.assertEqual(order, sorted(ADDITIONS - {existing}) + [installer.MANIFEST])
        self.assertEqual(self.configure(apply=False)['changed_files'], [])

    def test_join_requires_existing_previous_manifest(self):
        self.prepare_join()
        (self.image / installer.MANIFEST).unlink()
        with self.assertRaisesRegex(ValueError, 'Previous package manifest missing'):
            self.configure(previous_package_sha256=self.previous_pin)
        for name in ADDITIONS:
            self.assertFalse((self.image / name).exists())

    def test_join_refuses_changed_or_removed_payload_and_metadata(self):
        self.prepare_join()
        joined = copy.deepcopy(self.manifest)
        cases = []
        name = next(iter(self.previous['file_metadata']))
        changed = copy.deepcopy(joined)
        changed['file_metadata'][name]['mode'] = 0o600
        cases.append(changed)
        removed = copy.deepcopy(joined)
        del removed['file_metadata'][name]
        del removed['files']['/' + name]
        cases.append(removed)
        for key, value in [('redistributable', True), ('source_extra', 'unreviewed'),
                           ('symlinks', {}), ('abi_files', {})]:
            changed = copy.deepcopy(joined)
            changed[key] = value
            cases.append(changed)
        for index, changed in enumerate(cases):
            with self.subTest(case=index):
                self.manifest = changed
                self.write_manifest()
                with self.assertRaisesRegex(ValueError, 'Closed audio default transition'):
                    self.configure(previous_package_sha256=self.previous_pin)
                self.assert_unchanged()

    def test_both_policy_files_required_and_no_extra_additions(self):
        self.prepare_join()
        joined = copy.deepcopy(self.manifest)
        for omitted in ADDITIONS:
            self.manifest = copy.deepcopy(joined)
            del self.manifest['file_metadata'][omitted]
            del self.manifest['files']['/' + omitted]
            self.write_manifest()
            with self.subTest(omitted=omitted), self.assertRaisesRegex(ValueError, 'Closed audio default transition'):
                self.configure(previous_package_sha256=self.previous_pin)
            self.assert_unchanged()
        self.manifest = joined
        name = 'etc/unreviewed.conf'
        put(self.package / 'root' / name, b'unreviewed')
        self.manifest['file_metadata'][name] = dict(sha256=digest(b'unreviewed'), size=10, mode=0o644)
        self.manifest['files']['/' + name] = digest(b'unreviewed')
        self.write_manifest()
        with self.assertRaisesRegex(ValueError, 'Closed audio default transition'):
            self.configure(previous_package_sha256=self.previous_pin)
        self.assert_unchanged()

    def test_existing_owner_payload_change_is_not_allowed_by_transition(self):
        self.prepare_join()
        name = next(iter(self.previous['file_metadata']))
        put(self.image / name, b'owner customization')
        with self.assertRaisesRegex(ValueError, 'Conflicting existing audio file'):
            self.configure(previous_package_sha256=self.previous_pin)
        self.assert_unchanged()
        self.assertEqual((self.image / name).read_bytes(), b'owner customization')

    def test_real_source_spec_includes_both_pinned_policy_files(self):
        spec = json.loads((ROOT / 'audio/j713/qualified.json').read_text())
        for name in ADDITIONS:
            with self.subTest(name=name):
                self.assertIn(name, spec['files'])
                entry = spec['files'][name]
                data = (ROOT / 'audio/j713' / entry['source']).read_bytes()
                self.assertEqual(entry['source'], 'policy/' + name)
                self.assertEqual(entry['mode'], 0o644)
                self.assertEqual(entry['size'], len(data))
                self.assertEqual(entry['sha256'], digest(data))
                self.assertEqual(entry['license'], 'MIT')


if __name__ == '__main__':
    unittest.main()
