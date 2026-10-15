# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Channel package binaries must be joined to pinned corresponding source with rebuilt-identical correspondence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CHECK = importlib.util.module_from_spec(
    importlib.util.spec_from_file_location('check_m4_packages', ROOT / 'tools/check-m4-packages.py'))
CHECK.__spec__.loader.exec_module(CHECK)
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())


def digest(data):
    return hashlib.sha256(data).hexdigest()


def joined(data, correspondence='rebuilt-identical', sources=None):
    return {'sha256': digest(data), 'license': 'GPL-2.0-only', 'correspondence': correspondence,
            'sources': sources or [{'archive': 'source/kernel.tar.gz'}]}


class ManifestTests(unittest.TestCase):
    def load(self, joins):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'joins.json'
            path.write_text(json.dumps(joins))
            return CHECK.source_joins(path)

    def test_malformed_joins_are_refused(self):
        good = joined(b'test-binary')
        for entry in ({**good, 'sha256': 'abc'},
                      {**good, 'correspondence': 'probably'},
                      {**good, 'sources': []},
                      {**good, 'sources': [{'archive': 'kernel.tar.gz'}]},
                      {**good, 'sources': [{'archive': 'source/nested/kernel.tar.gz'}]},
                      {**good, 'sources': [{'url': 'https://example.invalid/kernel.tar.gz'}]},
                      {**good, 'sources': [{'tree': 'audio/j713/kernel', 'commit': 'ee98743a'}]},
                      {**good, 'sources': [{'archive': 'source/kernel.tar.gz', 'commit': 'e' * 40}]}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.load({'schema': 1, 'binaries': {'tool.ko': entry}})
        with self.assertRaises(ValueError):
            self.load({'schema': 2, 'binaries': {}})
        self.load({'schema': 1, 'binaries': {'tool.ko': {
            **good, 'sources': [{'tree': 'audio/j713/kernel', 'commit': 'e' * 40}]}}})

    def test_repository_channel_joins_pass_schema_and_every_binary_is_rebuilt_identical(self):
        joins = CHECK.source_joins()
        binaries = joins['binaries']
        self.assertGreaterEqual(len(binaries), 7)
        for name, entry in binaries.items():
            with self.subTest(name=name):
                self.assertEqual(entry['correspondence'], 'rebuilt-identical',
                                 f'{name} must be rebuilt-identical')
                self.assertTrue(entry.get('license'), f'{name} must specify a license')
                for source in entry['sources']:
                    if 'tree' in source:
                        self.assertTrue((ROOT / source['tree']).exists(),
                                        f'{name} source tree missing: {source["tree"]}')


class ChannelBinaryJoinTests(unittest.TestCase):
    def setUp(self):
        self.joins = CHECK.source_joins()

    def test_every_package_inputs_binary_in_channel_packages_is_joined(self):
        found_any = False
        for pkg_name in CHANNEL['packages']:
            inputs = CHECK.package_inputs(pkg_name)
            for filename, sha256 in inputs.items():
                found_any = True
                with self.subTest(pkg=pkg_name, binary=filename):
                    self.assertIn(filename, self.joins['binaries'],
                                  f'{pkg_name} downloads {filename} but it has no source join')
                    entry = self.joins['binaries'][filename]
                    self.assertEqual(entry['sha256'], sha256,
                                     f'Hash mismatch for {filename} in {pkg_name}')
                    self.assertEqual(entry['correspondence'], 'rebuilt-identical',
                                     f'{filename} in {pkg_name} is not rebuilt-identical')
        self.assertTrue(found_any, 'Expected at least one package-inputs binary across channel packages')

    def test_unlisted_downloaded_binary_is_refused(self):
        fake_joins = {'schema': 1, 'binaries': {}}
        with self.assertRaisesRegex(ValueError, 'downloads binary without a source join'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_different_build_of_downloaded_binary_is_refused(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Source join names a different build of'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_unverified_correspondence_is_refused_with_no_override(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['correspondence'] = 'unverified'
        with self.assertRaisesRegex(ValueError, 'not verified by an identical rebuild'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_missing_joined_source_tree_is_refused(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['sources'] = [{'tree': 'nonexistent/path/for/test'}]
        with self.assertRaisesRegex(ValueError, 'Joined source tree missing for'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_check_package_enforces_channel_joins(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-speaker')
        filename = next(iter(inputs))
        del fake_joins['binaries'][filename]
        good = {path: (0o644, digest) for path, digest in CHANNEL['speaker_module'].items()}
        good['usr/bin/m4-speaker-module'] = (0o755, hashlib.sha256((ROOT / 'tools/m4-speaker-module.py').read_bytes()).hexdigest())
        good[CHECK.SPEAKER_DROP_IN] = (0o644, hashlib.sha256((ROOT / 'packaging/omarchy-m4/omarchy-m4-speaker/20-m4-speaker-module.conf').read_bytes()).hexdigest())
        package = Path('omarchy-m4-speaker-1-1-aarch64.pkg.tar.zst')
        info = {'pkgname': ['omarchy-m4-speaker'], 'pkgver': ['1-1'], 'arch': ['aarch64']}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, 'downloads binary without a source join'):
                CHECK.check_package(CHANNEL, package, info, good, root, joins=fake_joins)

    def test_no_ci_override_flag_exists_in_check_tool(self):
        # Issue 93 specifies: "refuse a channel package whose downloaded binary has no rebuilt-identical join, with no override in CI."
        code = (ROOT / 'tools/check-m4-packages.py').read_text()
        self.assertNotIn('--allow-unverified-source', code)
        self.assertNotIn('--allow-unverified', code)


if __name__ == '__main__':
    unittest.main()
