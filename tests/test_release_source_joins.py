# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_joins_release', ROOT / 'tools/prepare-j713-release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)

ELF = b'\x7fELF\x02\x01\x01' + bytes(9)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def joined(data, correspondence='rebuilt-identical', sources=None):
    return {'sha256': digest(data), 'correspondence': correspondence,
            'sources': sources or [{'archive': 'source/kernel.tar.gz'}]}


class UnpackagedBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_only_files_no_package_owns_are_reported(self):
        self.put('var/lib/pacman/local/tool-1-1/files',
                 b'%FILES%\nusr/\nusr/bin/\nusr/bin/owned\n\n%BACKUP%\nusr/bin/backup-only\tabc\n')
        self.put('usr/bin/owned', ELF + b'owned')
        backup = self.put('usr/bin/backup-only', ELF + b'backup')
        tool = self.put('opt/m4/tool', ELF + b'tool')
        module = self.put('opt/m4/driver.ko', b'not an ELF header but a module name')
        self.put('opt/m4/README', b'text')
        os.symlink('tool', self.root / 'opt/m4/link')
        self.put('proc/1/exe', ELF + b'virtual')
        self.assertEqual(release.unpackaged_binaries(self.root), {
            'usr/bin/backup-only': digest(backup.read_bytes()),
            'opt/m4/tool': digest(tool.read_bytes()),
            'opt/m4/driver.ko': digest(module.read_bytes()),
        })

    def test_a_root_without_a_package_database_owns_nothing(self):
        tool = self.put('usr/bin/tool', ELF)
        self.assertEqual(release.unpackaged_binaries(self.root), {'usr/bin/tool': digest(tool.read_bytes())})


class BinaryJoinTests(unittest.TestCase):
    def setUp(self):
        self.gpu, self.mesa = ELF + b'gpu', ELF + b'mesa'
        self.found = {'opt/m4-gpu/asahi_m4.ko': digest(self.gpu), 'opt/m4-mesa/lib/libEGL.so': digest(self.mesa)}
        self.joins = {'schema': 1, 'binaries': {'opt/m4-gpu/asahi_m4.ko': joined(self.gpu),
                                                'opt/m4-mesa/lib/libEGL.so': joined(self.mesa)}}

    def test_verified_joins_pass_and_report_nothing_unverified(self):
        self.assertEqual(release.check_binary_joins(self.found, self.joins), [])

    def test_an_unlisted_binary_is_named_in_the_refusal(self):
        found = {**self.found, 'opt/new/tool': digest(b'new')}
        with self.assertRaisesRegex(RuntimeError, 'without a source join: opt/new/tool'):
            release.check_binary_joins(found, self.joins)

    def test_a_join_for_a_binary_no_longer_shipped_is_refused(self):
        del self.found['opt/m4-mesa/lib/libEGL.so']
        with self.assertRaisesRegex(RuntimeError, 'no longer carries: opt/m4-mesa/lib/libEGL.so'):
            release.check_binary_joins(self.found, self.joins)

    def test_a_different_build_of_a_joined_binary_is_refused(self):
        self.found['opt/m4-gpu/asahi_m4.ko'] = digest(ELF + b'rebuilt elsewhere')
        with self.assertRaisesRegex(RuntimeError, 'different build of opt/m4-gpu/asahi_m4.ko'):
            release.check_binary_joins(self.found, self.joins)

    def test_unverified_correspondence_needs_explicit_permission(self):
        self.joins['binaries']['opt/m4-mesa/lib/libEGL.so']['correspondence'] = 'unverified'
        with self.assertRaisesRegex(RuntimeError, 'not verified by an identical rebuild: opt/m4-mesa'):
            release.check_binary_joins(self.found, self.joins)
        self.assertEqual(release.check_binary_joins(self.found, self.joins, allow_unverified=True),
                         ['opt/m4-mesa/lib/libEGL.so'])


class JoinSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name).resolve()
        self.archive = self.output / 'source/kernel.tar.gz'
        self.archive.parent.mkdir()
        self.archive.write_bytes(b'kernel tree')
        self.joins = {'schema': 1, 'binaries': {'opt/m4-gpu/asahi_m4.ko': joined(ELF, sources=[
            {'archive': 'source/kernel.tar.gz', 'sha256': digest(b'kernel tree')},
            {'tree': 'audio/j713/kernel'}])}}
        self.tracked = ['audio/j713/kernel/mca.c', 'tools/prepare-j713-release.py']

    def test_pinned_archives_and_tracked_trees_pass(self):
        release.check_join_sources(self.output, self.tracked, self.joins)

    def test_missing_or_changed_archive_is_refused(self):
        self.archive.write_bytes(b'another tree')
        with self.assertRaisesRegex(RuntimeError, 'archive changed for opt/m4-gpu/asahi_m4.ko'):
            release.check_join_sources(self.output, self.tracked, self.joins)
        self.archive.unlink()
        with self.assertRaisesRegex(RuntimeError, 'archive missing for opt/m4-gpu/asahi_m4.ko'):
            release.check_join_sources(self.output, self.tracked, self.joins)

    def test_a_tree_outside_the_release_integration_is_refused(self):
        for tracked in (['tools/prepare-j713-release.py'], ['audio/j713/kernel-old/mca.c']):
            with self.subTest(tracked=tracked), self.assertRaisesRegex(RuntimeError, 'audio/j713/kernel'):
                release.check_join_sources(self.output, tracked, self.joins)


class ManifestTests(unittest.TestCase):
    def load(self, joins):
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / 'packaging').mkdir()
            (tree / release.SOURCE_JOINS).write_text(json.dumps(joins))
            return release.source_joins(tree)

    def test_malformed_joins_are_refused(self):
        good = joined(ELF)
        for entry in ({**good, 'sha256': 'abc'}, {**good, 'correspondence': 'probably'},
                      {**good, 'sources': []}, {**good, 'sources': [{'archive': 'kernel.tar.gz'}]},
                      {**good, 'sources': [{'archive': 'source/nested/kernel.tar.gz'}]},
                      {**good, 'sources': [{'url': 'https://example.invalid/kernel.tar.gz'}]}):
            with self.subTest(entry=entry), self.assertRaises(RuntimeError):
                self.load({'schema': 1, 'binaries': {'opt/tool': entry}})
        with self.assertRaises(RuntimeError):
            self.load({'schema': 2, 'binaries': {}})

    def test_repository_joins_cover_alpha1_and_only_the_gpu_module_is_proven(self):
        joins = release.source_joins(ROOT)
        binaries = joins['binaries']
        self.assertEqual(len(binaries), 24)
        gpu = binaries['opt/m4-gpu/asahi_m4.ko']
        self.assertEqual(gpu['correspondence'], 'rebuilt-identical')
        self.assertEqual(gpu['sources'], [
            {'archive': 'source/kernel-controller.tar.gz',
             'sha256': '810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57'},
            {'archive': 'source/omarchy-j713-v0.1.0-alpha.1-gpu-air-source.tar.gz',
             'sha256': 'c19a7c97184b3f954795c50dfeff857e8baf685f901524845710da839cbd2562'}])
        documented = (ROOT / 'docs/j713-gpu-normal-root.md').read_text()
        self.assertIn('| GPU module | `' + gpu['sha256'] + '` |', documented)
        self.assertEqual({name for name, entry in binaries.items() if entry['correspondence'] == 'unverified'},
                         set(binaries) - {'opt/m4-gpu/asahi_m4.ko'})
        tracked = [path.relative_to(ROOT).as_posix() for path in (ROOT / 'audio').rglob('*') if path.is_file()]
        for name, entry in binaries.items():
            for source in entry['sources']:
                if 'tree' in source:
                    with self.subTest(name=name, tree=source['tree']):
                        self.assertTrue(any(path.startswith(source['tree'] + '/') for path in tracked))


class RootGateTests(unittest.TestCase):
    """prepare_root refuses an unjoined binary before the slow free-space fill."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.tree, self.root, self.image = base / 'tree', base / 'mounted', base / 'root.ext4'
        firs = {name: name.encode() for name in release.FIRS}
        manifest = {'reference_outputs': {name: {'sha256': digest(data)} for name, data in firs.items()}}
        self.write(self.tree, 'audio/j713/owner-fir/manifest.json', json.dumps(manifest).encode())
        for source in ('tools/m4-j713-owner-assets.py', 'packaging/j713-owner-assets.service',
                       'packaging/j713-owner-assets-audio.conf', 'packaging/j713-owner-assets-session.conf',
                       'tools/m4-channel-setup.py', 'packaging/j713-first-boot/m4-channel-connect.service',
                       'packaging/j713-first-boot/m4-channel-connect.timer'):
            self.write(self.tree, source, source.encode())
        self.tool = ELF + b'unjoined'
        self.write(self.tree, release.SOURCE_JOINS, json.dumps({'schema': 1, 'binaries': {
            'opt/m4/tool': joined(self.tool, 'unverified')}}).encode())
        self.firs = firs

    def write(self, base, name, data):
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def mount(self, *args):
        if args[0] != 'mount':
            return
        for name, data in self.firs.items():
            self.write(self.root, 'usr/share/asahi-audio/j713/' + name, data)
        for profile in release.PROFILES:
            self.write(self.root, 'etc/skel/.config/omarchy/pending/widevine-' + profile + '.json', json.dumps(
                {'schema': 1, 'profile': profile, 'consent': 'owner-requested'}).encode())
        self.write(self.root, 'etc/omarchy/j713-audio-manifest.json', b'{}')
        self.write(self.root, 'opt/m4/tool', self.tool)
        (self.root / 'etc/systemd/system/multi-user.target.wants').mkdir(parents=True)

    def prepare(self, **options):
        filled = mock.Mock()
        actual_sha = release.sha

        def sha(path):
            return release.BASE_ROOT if path == self.image else actual_sha(path)

        with mock.patch.object(release, 'sha', side_effect=sha), \
             mock.patch.object(release, 'command', side_effect=self.mount), \
             mock.patch.object(release, 'owner_audio_manifest', return_value=b'{}'), \
             mock.patch.object(release, 'prepare_startup', return_value=b'{}'), \
             mock.patch.object(release, 'prepare_decoder', return_value='v' * 64), \
             mock.patch.object(release, 'fill_free_space', filled):
            try:
                return release.prepare_root(self.image, self.image, self.root, self.tree, 'id', **options)
            finally:
                self.filled = filled.called

    def test_unverified_binary_stops_assembly_before_filling_free_space(self):
        with self.assertRaisesRegex(RuntimeError, 'not verified by an identical rebuild: opt/m4/tool'):
            self.prepare()
        self.assertFalse(self.filled)

    def test_permitted_unverified_binary_is_reported(self):
        self.assertEqual(self.prepare(allow_unverified=True)['unverified'], ['opt/m4/tool'])
        self.assertTrue(self.filled)


if __name__ == '__main__':
    unittest.main()
