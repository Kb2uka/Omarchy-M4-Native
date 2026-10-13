# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""M4 channel recipes, pins and package policy agree with the qualified release."""
import hashlib
import importlib.util
import json
from pathlib import Path
import io
import re
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / 'packaging/omarchy-m4'
CHANNEL = json.loads((PACKAGING / 'channel.json').read_text())


def load(name):
    spec = importlib.util.spec_from_file_location(Path(name).stem.replace('-', '_'), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load('tools/build-m4-packages.py')
CHECK = load('tools/check-m4-packages.py')


def recipe(name):
    return (PACKAGING / name / 'PKGBUILD').read_text()


def field(text, key):
    match = re.search(rf'^{key}=(.*)$', text, re.M)
    return match.group(1).strip("'\"") if match else None


class RecipeTests(unittest.TestCase):
    def test_local_sources_match_their_hashes(self):
        for name in CHANNEL['packages']:
            text = recipe(name)
            sources = re.search(r'^source=\((.*?)\)', text, re.M | re.S).group(1)
            sums = re.findall(r"'([0-9a-f]{64})'", re.search(r'^sha256sums=\((.*?)\)', text, re.M | re.S).group(1))
            entries = re.findall(r"""['"]([^'"]+)['"]""", sources)
            self.assertEqual(len(entries), len(sums), name)
            for entry, digest in zip(entries, sums):
                if '::' in entry or '$' in entry:  # remote, pinned by URL and hash
                    continue
                staged = BUILD.STAGED.get(name, {}).get(entry)
                path = ROOT / staged if staged else PACKAGING / name / entry
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, f'{name}: {entry}')

    def test_kernel_pin_is_the_qualified_alpha1_kernel(self):
        release = load('tools/prepare-j713-release.py')
        identity = release.KERNEL_IDENTITY
        self.assertEqual(CHANNEL['kernel'], {'release': identity['build']['kernel_release'],
                                             'image_sha256': identity['kernel']['sha256'],
                                             'image_bytes': identity['kernel']['bytes']})
        self.assertEqual(CHANNEL['devicetree']['sha256'], release.POWEROFF_DTB)
        text = recipe('linux-m4-omarchy')
        self.assertEqual(field(text, 'pkgver'), '7.1.9.m4.20261008')
        self.assertGreaterEqual(int(field(text, 'pkgrel')), 2, 'must upgrade the installed alpha.1 -1 package')
        self.assertIn("depends=('coreutils' 'kmod' 'omarchy-m4-boot')", text)
        self.assertIn("conflicts=('linux-aarch64')", text)
        self.assertIn("provides=('linux=7.1.9' 'linux-aarch64=7.1.9')", text)

    def test_boot_package_carries_the_update1_devicetree(self):
        text = recipe('omarchy-m4-boot')
        self.assertIn('_dtb_release=v0.1.0-alpha.1-update.1', text)
        self.assertIn(CHANNEL['devicetree']['sha256'], text)

    def test_hook_covers_every_boot_input(self):
        hook = (PACKAGING / 'omarchy-m4-boot/90-omarchy-m4-boot.hook').read_text()
        compose = load('tools/m4-boot-compose.py')
        targets = set(re.findall(r'^Target = (.+)$', hook, re.M))
        self.assertEqual(targets, {compose.KERNEL, compose.DTB, 'usr/bin/m4-boot-compose'})
        self.assertIn('When = PostTransaction', hook)
        self.assertIn('Exec = /usr/bin/m4-boot-compose --apply\n', hook)
        for target in targets:
            owners = [n for n, paths in CHANNEL['allowed_paths'].items() if CHECK.allowed(target, paths)]
            self.assertEqual(len(owners), 1, target)

    def test_allowlists_never_reach_protected_paths(self):
        for name, paths in CHANNEL['allowed_paths'].items():
            for path in paths:
                self.assertFalse(CHECK.allowed(path, CHANNEL['protected_paths']), f'{name}: {path}')
                self.assertFalse(any(p.startswith(path) for p in CHANNEL['protected_paths'] if path.endswith('/')))
        for protected in ('opt/m4-video/20261011/vaapi/v4l2_request_drv_video.so', 'usr/local/bin/brave',
                          'etc/omarchy/j713-video-manifest.json', 'usr/share/asahi-audio/j713/graph.json',
                          'usr/lib/omarchy/j713-audio/x/modules/snd-soc-tas2764.ko',
                          'usr/share/speakersafetyd/apple/j713.conf', 'boot/efi/omarchy/boot.bin',
                          'etc/skel/.config/omarchy/pending/widevine-Brave-Browser.json'):
            self.assertTrue(CHECK.allowed(protected, CHANNEL['protected_paths']), protected)


class PolicyTests(unittest.TestCase):
    def package(self, name, files, info=None, version='1-1', arch='aarch64'):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        info = info or {'pkgname': [name], 'pkgver': [version], 'arch': [arch]}
        return Path(f'{name}-{version}-{arch}.pkg.tar.zst'), info, files, root

    def test_protected_unlisted_and_writable_files_are_rejected(self):
        cases = (
            ({'opt/m4-video/20261011/mesa/lib/libEGL.so.1.0.0': (0o644, 'x')}, 'unlisted path'),
            ({'usr/bin/m4-boot-compose': (0o777, 'x')}, 'writable or setuid'),
            ({'usr/bin/m4-boot-compose': (0o4755, 'x')}, 'writable or setuid'),
            ({}, 'Empty package'),
        )
        for files, message in cases:
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, *self.package('omarchy-m4-boot', files))

    def test_protected_beats_an_allowlist_mistake(self):
        channel = json.loads(json.dumps(CHANNEL))
        channel['allowed_paths']['omarchy-m4-boot'].append('usr/local/')
        with self.assertRaisesRegex(ValueError, 'protected path'):
            CHECK.check_package(channel, *self.package('omarchy-m4-boot', {'usr/local/bin/brave': (0o755, 'x')}))

    def test_name_must_match_metadata(self):
        path, info, files, root = self.package('omarchy-m4-boot', {'usr/bin/m4-boot-compose': (0o755, 'x')})
        with self.assertRaisesRegex(ValueError, 'does not match its metadata'):
            CHECK.check_package(CHANNEL, Path('omarchy-m4-boot-2-1-aarch64.pkg.tar.zst'), info, files, root)

    def test_kernel_must_be_the_pin(self):
        files = {'usr/lib/omarchy-m4/Image.gz': (0o644, '0' * 64),
                 f"usr/lib/modules/{CHANNEL['kernel']['release']}/kernel/x.ko": (0o644, '1' * 64)}
        with self.assertRaisesRegex(ValueError, 'pinned kernel'):
            CHECK.check_package(CHANNEL, *self.package('linux-m4-omarchy', files, version='7.1.9.m4.20261008-2'))

    def test_republish_identity_covers_scripts_and_metadata(self):
        info = {'pkgname': ['omarchy-m4-boot'], 'depend': ['python'], 'builddate': ['1'], 'packager': ['A'],
                '.INSTALL': ['a' * 64]}
        files = {'usr/bin/m4-boot-compose': (0o755, 'x')}
        same = dict(info, builddate=['2'], packager=['B'])
        self.assertEqual(CHECK.release_identity(info, files), CHECK.release_identity(same, files))
        for change in ({'.INSTALL': ['b' * 64]}, {'depend': ['python', 'pacman']}):
            self.assertNotEqual(CHECK.release_identity(info, files), CHECK.release_identity(dict(info, **change), files))

    def test_archive_reading_records_scripts_metadata_and_modes(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)

        def archive(script):
            path = root / ('package-%d.tar' % len(list(root.glob('*.tar'))))
            with tarfile.open(path, 'w') as tar:
                for name, data, mode in (('.PKGINFO', b'pkgname = omarchy-m4-boot\ndepend = python\n', 0o644),
                                         ('.INSTALL', script, 0o644), ('.MTREE', b'ignored', 0o644),
                                         ('usr/bin/m4-boot-compose', b'composer', 0o755)):
                    info = tarfile.TarInfo(name)
                    info.size, info.mode = len(data), mode
                    tar.addfile(info, io.BytesIO(data))
            out = root / (path.stem + '-root')
            out.mkdir()
            result = CHECK.read_archive(path, out, path.name)
            self.assertEqual((out / 'usr/bin/m4-boot-compose').read_bytes(), b'composer',
                             'payload files are written out for the content checks')
            self.assertFalse((out / '.INSTALL').exists() or (out / '.MTREE').exists())
            return result

        info, files = archive(b'post_install() { :; }\n')
        self.assertEqual(info['depend'], ['python'])
        self.assertEqual(info['.INSTALL'], [hashlib.sha256(b'post_install() { :; }\n').hexdigest()])
        self.assertEqual(files, {'usr/bin/m4-boot-compose': (0o755, hashlib.sha256(b'composer').hexdigest())})
        self.assertNotEqual(CHECK.release_identity(info, files),
                            CHECK.release_identity(*archive(b'post_install() { exit 1; }\n')))

    def test_unsafe_archive_members_are_rejected(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        outside = root / 'outside-target'
        for name, kind, message in (('../escape', tarfile.REGTYPE, 'Unsafe member path'),
                                    (str(outside), tarfile.REGTYPE, 'Unsafe member path'),
                                    ('usr/bin/link', tarfile.SYMTYPE, 'Only regular files')):
            with self.subTest(name):
                path = root / 'bad.tar'
                with tarfile.open(path, 'w') as tar:
                    for member, data, member_kind in (('.PKGINFO', b'pkgname = x\n', tarfile.REGTYPE),
                                                      (name, b'payload', kind)):
                        info = tarfile.TarInfo(member)
                        info.type, info.size = member_kind, len(data) if member_kind == tarfile.REGTYPE else 0
                        tar.addfile(info, io.BytesIO(data))
                out = root / 'out'
                out.mkdir(exist_ok=True)
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.read_archive(path, out, 'bad')
                self.assertFalse(outside.exists())

    def test_other_module_release_is_rejected(self):
        files = {'usr/lib/modules/7.2.0-other/kernel/x.ko': (0o644, '1' * 64)}
        with self.assertRaisesRegex(ValueError, 'unlisted path'):
            CHECK.check_package(CHANNEL, *self.package('linux-m4-omarchy', files, version='7.2.0-1'))


if __name__ == '__main__':
    unittest.main()
