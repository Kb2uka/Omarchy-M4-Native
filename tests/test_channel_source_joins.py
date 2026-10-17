# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Channel package binaries must be joined to pinned corresponding source."""
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


def joined(data, correspondence='unverified', sources=None):
    return {'sha256': digest(data), 'license': 'GPL-2.0-only', 'correspondence': correspondence,
            'sources': sources or [{'archive': 'source/kernel.tar.gz', 'sha256': digest(b'kernel')}]}


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
                      {**good, 'sources': [{'archive': 'kernel.tar.gz', 'sha256': digest(b'k')}]},
                      {**good, 'sources': [{'archive': 'source/nested/kernel.tar.gz', 'sha256': digest(b'k')}]},
                      {**good, 'sources': [{'archive': 'source/kernel.tar.gz'}]},  # missing sha256
                      {**good, 'sources': [{'url': 'https://example.invalid/kernel.tar.gz'}]},
                      {**good, 'sources': [{'tree': 'audio/j713/kernel'}]},  # tree without a commit pin
                      {**good, 'sources': [{'tree': 'audio/j713/kernel', 'commit': 'ee98743a'}]},
                      {**good, 'sources': [{'archive': 'source/kernel.tar.gz', 'sha256': 'abc'}]},
                      {**good, 'sources': [{'archive': 'source/kernel.tar.gz', 'commit': 'e' * 40}]}):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.load({'schema': 1, 'binaries': {'tool.ko': entry}})
        with self.assertRaises(ValueError):
            self.load({'schema': 2, 'binaries': {}})
        self.load({'schema': 1, 'binaries': {'tool.ko': {
            **good, 'sources': [{'tree': 'audio/j713/kernel', 'commit': 'e' * 40}]}}})

    def test_missing_joins_file_is_refused_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(ValueError, 'Missing channel source joins'):
                CHECK.source_joins(Path(temp) / 'absent.json')

    def test_repository_channel_joins_pass_schema_and_match_pins(self):
        joins = CHECK.source_joins()
        binaries = joins['binaries']
        self.assertEqual(len(binaries), 13)
        for name, entry in binaries.items():
            with self.subTest(name=name):
                self.assertIn(entry['correspondence'], CHECK.CORRESPONDENCE)
                self.assertTrue(entry.get('license'), f'{name} must specify a license')
                self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', entry['sha256']))
                for source in entry['sources']:
                    if 'tree' in source:
                        self.assertTrue((ROOT / source['tree']).exists(),
                                        f'{name} source tree missing: {source["tree"]}')
                        if 'commit' in source:
                            pinned = CHECK.git_tree_id(ROOT, source['commit'], source['tree'])
                            self.assertIsNotNone(pinned, f'Commit {source["commit"]} missing tree {source["tree"]}')
                            self.assertEqual(pinned, CHECK.git_tree_id(ROOT, 'HEAD', source['tree']),
                                             f'{name}: pinned tree differs from HEAD')
                    if 'archive' in source:
                        self.assertTrue(source['archive'].startswith(('source/', 'package-inputs/')))
                        self.assertTrue(re.fullmatch(r'[0-9a-f]{64}', source['sha256']))


class ChannelBinaryJoinTests(unittest.TestCase):
    def setUp(self):
        self.joins = CHECK.source_joins()

    def test_every_package_inputs_binary_in_channel_packages_is_joined_with_matching_hash(self):
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
                    self.assertIn(entry['correspondence'], CHECK.CORRESPONDENCE)
        self.assertTrue(found_any, 'Expected at least one package-inputs binary across channel packages')

    def test_check_channel_joins_for_every_package_refuses_missing_binary(self):
        verified_joins = json.loads(json.dumps(self.joins))
        for entry in verified_joins['binaries'].values():
            entry['correspondence'] = 'rebuilt-identical'

        tested_any = False
        for pkg_name in CHANNEL['packages']:
            inputs = CHECK.package_inputs(pkg_name)
            for filename in inputs:
                tested_any = True
                fake_joins = json.loads(json.dumps(verified_joins))
                del fake_joins['binaries'][filename]
                with self.subTest(pkg=pkg_name, missing=filename):
                    with self.assertRaisesRegex(ValueError, f'{pkg_name} downloads binary without a source join: {re.escape(filename)}'):
                        CHECK.check_channel_joins(pkg_name, fake_joins)
        self.assertTrue(tested_any, 'Expected package-inputs binaries across packages')

    def test_unverified_correspondence_is_refused_for_every_downloading_package(self):
        unverified = json.loads(json.dumps(self.joins))
        for entry in unverified['binaries'].values():
            entry['correspondence'] = 'unverified'
        tested_any = False
        for pkg_name in CHANNEL['packages']:
            inputs = CHECK.package_inputs(pkg_name)
            if inputs:
                tested_any = True
                with self.subTest(pkg=pkg_name):
                    with self.assertRaisesRegex(ValueError, 'not verified by an identical rebuild'):
                        CHECK.check_channel_joins(pkg_name, unverified)
        self.assertTrue(tested_any)

    def test_different_build_of_downloaded_binary_is_refused(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'Source join names a different build of'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_missing_joined_source_tree_is_refused(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['correspondence'] = 'rebuilt-identical'
        fake_joins['binaries'][filename]['sources'] = [{'tree': 'nonexistent/path/for/test', 'commit': '0' * 40}]
        with self.assertRaisesRegex(ValueError, 'Joined source tree missing for'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_commit_pin_differing_from_head_is_refused(self):
        older_commit = '7d315920e24c6823d112683610da662c8d60fae6'
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['correspondence'] = 'rebuilt-identical'
        fake_joins['binaries'][filename]['sources'] = [
            {'tree': 'gpu/j713', 'commit': older_commit}
        ]
        with self.assertRaisesRegex(ValueError, 'differs from'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_nonexistent_commit_is_refused(self):
        fake_joins = json.loads(json.dumps(self.joins))
        inputs = CHECK.package_inputs('omarchy-m4-gpu')
        filename = next(iter(inputs))
        fake_joins['binaries'][filename]['correspondence'] = 'rebuilt-identical'
        fake_joins['binaries'][filename]['sources'] = [
            {'tree': 'gpu/j713', 'commit': '0' * 40}
        ]
        with self.assertRaisesRegex(ValueError, 'does not exist or does not contain'):
            CHECK.check_channel_joins('omarchy-m4-gpu', fake_joins)

    def test_stale_join_is_refused(self):
        fake_joins = json.loads(json.dumps(self.joins))
        fake_joins['binaries']['j713-cputemp.dtb'] = {
            'sha256': '08e8e719381f88a1e50d821c145dad5b2d97fd772609e13d7778e19795e9cad1',
            'license': 'GPL-2.0-only',
            'correspondence': 'unverified',
            'sources': [{'tree': 'patches/j713-cpu-temperature.dtso', 'commit': '09998c5268e86e0b5304a071d956949f824b3ba6'}]
        }
        with self.assertRaisesRegex(ValueError, 'Source joins for binaries the channel no longer ships: j713-cputemp.dtb'):
            CHECK.check_stale_joins(CHANNEL, fake_joins)

    def test_archive_without_sha256_is_refused(self):
        bad_joins = {
            'schema': 1,
            'binaries': {
                'asahi_m4-3cf47fa7.ko': {
                    'sha256': '3cf47fa706630ddc5fc55a3bce00ab31e8a4c5ee48e1ba563e26a0406da196f8',
                    'license': 'GPL-2.0-only',
                    'correspondence': 'unverified',
                    'sources': [{'archive': 'source/kernel-controller.tar.gz'}]
                }
            }
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'joins.json'
            path.write_text(json.dumps(bad_joins))
            with self.assertRaisesRegex(ValueError, 'Unknown source reference'):
                CHECK.source_joins(path)

    def test_pkgbuild_mentions_package_inputs_without_parsed_inputs_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pkg_dir = Path(temp) / 'packaging/omarchy-m4/bad-pkg'
            pkg_dir.mkdir(parents=True)
            (pkg_dir / 'PKGBUILD').write_text('# mentions package-inputs/ in comments\nsource=()\nsha256sums=()\n')
            with mock.patch.object(CHECK, 'ROOT', Path(temp)):
                with self.assertRaisesRegex(ValueError, 'bad-pkg: PKGBUILD mentions package-inputs/ but no inputs were parsed'):
                    CHECK.package_inputs('bad-pkg')

    def test_pkgbuild_with_arch_suffixed_source_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pkg_dir = Path(temp) / 'packaging/omarchy-m4/bad-pkg'
            pkg_dir.mkdir(parents=True)
            (pkg_dir / 'PKGBUILD').write_text('source_aarch64=("package-inputs/x.ko")\nsha256sums_aarch64=(\'1\'*64)\n')
            with mock.patch.object(CHECK, 'ROOT', Path(temp)):
                with self.assertRaisesRegex(ValueError, 'bad-pkg: arch-suffixed source_\\* array is not allowed'):
                    CHECK.package_inputs('bad-pkg')

    def test_pkgbuild_with_arch_suffixed_sha256sums_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            pkg_dir = Path(temp) / 'packaging/omarchy-m4/bad-pkg'
            pkg_dir.mkdir(parents=True)
            (pkg_dir / 'PKGBUILD').write_text('source=("package-inputs/x.ko")\nsha256sums_aarch64=(\'1\'*64)\n')
            with mock.patch.object(CHECK, 'ROOT', Path(temp)):
                with self.assertRaisesRegex(ValueError, 'bad-pkg: arch-suffixed sha256sums_\\* array is not allowed'):
                    CHECK.package_inputs('bad-pkg')

    def test_missing_pkgbuild_for_channel_package_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            with mock.patch.object(CHECK, 'ROOT', Path(temp)):
                with self.assertRaisesRegex(ValueError, 'Missing PKGBUILD for package: nonexistent-pkg'):
                    CHECK.package_inputs('nonexistent-pkg')

    def test_self_referencing_variable_expansion_terminates(self):
        with tempfile.TemporaryDirectory() as temp:
            pkg_dir = Path(temp) / 'packaging/omarchy-m4/loop-pkg'
            pkg_dir.mkdir(parents=True)
            (pkg_dir / 'PKGBUILD').write_text('_x="${_x}y"\nsource=()\nsha256sums=()\n')
            with mock.patch.object(CHECK, 'ROOT', Path(temp)):
                self.assertEqual(CHECK.package_inputs('loop-pkg'), {})

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

    def test_check_enforces_stale_and_per_package_joins_on_every_package(self):
        joins = {'schema': 1, 'binaries': {}}
        names = {name + '-1-1-aarch64.pkg.tar.zst': name for name in CHANNEL['packages']}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for filename in names:
                (directory / filename).write_bytes(b'')
            with mock.patch.object(CHECK, 'source_joins', return_value=joins), \
                    mock.patch.object(CHECK, 'check_stale_joins') as stale, \
                    mock.patch.object(CHECK, 'extract', return_value=({}, {})), \
                    mock.patch.object(CHECK, 'check_package',
                                      side_effect=lambda channel, package, *rest, **kw: names[package.name]) as package:
                CHECK.check(directory, compare_published=False)
        stale.assert_called_once()
        self.assertIs(stale.call_args.args[1], joins)
        self.assertEqual(package.call_count, len(names))
        for call in package.call_args_list:
            self.assertIs(call.kwargs.get('joins'), joins)

    def test_joins_bind_every_version_not_yet_published(self):
        joins = {'schema': 1, 'binaries': {}}
        names = {name + '-1-1-aarch64.pkg.tar.zst': name for name in CHANNEL['packages']}
        shipped = sorted(names)[:2]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for filename in names:
                (directory / filename).write_bytes(b'')
            with mock.patch.object(CHECK, 'source_joins', return_value=joins), \
                    mock.patch.object(CHECK, 'check_stale_joins'), \
                    mock.patch.object(CHECK, 'extract', return_value=({}, {})), \
                    mock.patch.object(CHECK, 'published',
                                      side_effect=lambda channel, filename, scratch:
                                      scratch / filename if filename in shipped else None), \
                    mock.patch.object(CHECK, 'check_package',
                                      side_effect=lambda channel, package, *rest, **kw: names[package.name]) as package:
                CHECK.check(directory, compare_published=True)
        by_name = {call.args[1].name: call.kwargs.get('joins') for call in package.call_args_list}
        self.assertEqual(len(by_name), len(names))
        for filename, given in by_name.items():
            with self.subTest(package=filename):
                if filename in shipped:
                    self.assertIsNone(given, 'an identical published version is already on Airs')
                else:
                    self.assertIs(given, joins, 'an unpublished version must be rebuilt-identical')

    def test_published_name_with_other_contents_is_still_refused(self):
        joins = {'schema': 1, 'binaries': {}}
        filename = CHANNEL['packages'][0] + '-1-1-aarch64.pkg.tar.zst'
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / filename).write_bytes(b'')
            extracted = iter([({'pkgver': ['1-1']}, {'a': (0o644, '1')}), ({'pkgver': ['1-1']}, {'a': (0o644, '2')})])
            with mock.patch.object(CHECK, 'source_joins', return_value=joins), \
                    mock.patch.object(CHECK, 'check_stale_joins'), \
                    mock.patch.object(CHECK, 'extract', side_effect=lambda *a: next(extracted)), \
                    mock.patch.object(CHECK, 'published', side_effect=lambda channel, name, scratch: scratch / name), \
                    mock.patch.object(CHECK, 'check_package') as package:
                with self.assertRaisesRegex(ValueError, 'already published with different contents'):
                    CHECK.check(directory, compare_published=True)
        package.assert_not_called()

    def test_no_ci_override_flag_exists_in_check_tool(self):
        # Issue 93 specifies: "refuse a channel package whose downloaded binary has no rebuilt-identical join, with no override in CI."
        code = (ROOT / 'tools/check-m4-packages.py').read_text()
        self.assertNotIn('--allow-unverified-source', code)
        self.assertNotIn('--allow-unverified', code)


if __name__ == '__main__':
    unittest.main()
