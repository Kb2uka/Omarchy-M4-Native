# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Package policy for the retained-bounds boot kernel and the fixed MCA module.

The base linux-m4-omarchy package keeps the qualified image and its modules.
omarchy-m4-boot 1-18 carries the new image beside that package, and
omarchy-m4-speaker carries the fixed MCA module. Pins come from the shipped channel.
"""
import configparser
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
CHANNEL = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
MCA_DROP_IN = 'usr/lib/systemd/system/m4-j713-audio-register.service.d/25-m4-mca-module.conf'
MCA_DROP_IN_SOURCE = ROOT / 'packaging/omarchy-m4/omarchy-m4-speaker/25-m4-mca-module.conf'
# The leading '-' is systemd's ignore-failure prefix and is part of the command.
MCA_BOOT_COMMAND = '-/usr/bin/m4-speaker-module --component mca --boot'
BOOT_VERSION = '1-18'
SPEAKER_VERSION = '1-4'


def load(name):
    spec = importlib.util.spec_from_file_location(Path(name).stem.replace('-', '_'), ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECK = load('tools/check-m4-packages.py')
BUILD = load('tools/build-m4-packages.py')
COMPOSE = load('tools/m4-boot-compose.py')
SPEAKER = load('tools/m4-speaker-module.py')


def sha(data):
    return hashlib.sha256(data).hexdigest()


def staged(package, name):
    return (ROOT / BUILD.STAGED[package][name]).read_bytes()


def write_size(root, relative, size):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as handle:
        handle.truncate(size)
    return path


def mca_drop_in_bytes():
    """Production MCA drop-in. Absence fails; there is no substitute payload."""
    if not MCA_DROP_IN_SOURCE.is_file():
        raise AssertionError('production MCA drop-in is absent')
    return MCA_DROP_IN_SOURCE.read_bytes()


def service_option_values(text, option):
    """Active [Service] values for one option. Comments and other sections do not count.

    A second active assignment, including a reset line, is a duplicate and does not
    become the single required command.
    """
    parser = configparser.ConfigParser(interpolation=None, strict=True)
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.DuplicateOptionError as error:
        raise AssertionError(
            'duplicate active assignment: ' + error.section + '.' + error.option) from error
    if not parser.has_section('Service'):
        return []
    return [parser['Service'][key] for key in parser['Service'] if key == option]


def policy_channel():
    """Deep copy of the shipped channel. Tests mutate the copy; channel.json stays as published."""
    return json.loads(json.dumps(CHANNEL))


class HardeningPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.channel = policy_channel()
        cls.joins = CHECK.source_joins()
        cls.boot_pin = cls.channel['boot_kernel']
        cls.mca = cls.channel['mca_module']
        cls._temps = []

    @classmethod
    def tearDownClass(cls):
        for temp in cls._temps:
            temp.cleanup()

    def root(self):
        temp = tempfile.TemporaryDirectory()
        self._temps.append(temp)
        return Path(temp.name)

    def boot_package(self, files, info=None, root=None):
        info = info or self.boot_info()
        return (Path(f"omarchy-m4-boot-{BOOT_VERSION}-aarch64.pkg.tar.zst"), info, files,
                root or self.boot_root)

    def boot_info(self, depend=None):
        if depend is None:
            depend = ['python', 'pacman', 'omarchy-m4-gpu', 'omarchy-m4-headphones', 'omarchy-m4-cpu',
                      'omarchy-m4-evdi', 'omarchy-m4-backlight', 'omarchy-m4-speaker>=1-4']
        return {'pkgname': ['omarchy-m4-boot'], 'pkgver': [BOOT_VERSION], 'arch': ['aarch64'], 'depend': depend}

    def boot_tree(self, pin):
        """Minimal boot package whose scripts are the staged repository files."""
        root = self.root()
        composer = staged('omarchy-m4-boot', 'm4-boot-compose.py')
        notch = staged('omarchy-m4-boot', 'm4-notch.py')
        update = staged('omarchy-m4-boot', 'omarchy-m4-update')
        setup = staged('omarchy-m4-boot', 'm4-channel-setup.py')
        hook = (ROOT / 'packaging/omarchy-m4/omarchy-m4-boot/90-omarchy-m4-boot.hook').read_bytes()
        realtime = (ROOT / 'packaging/omarchy-m4/omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_bytes()
        unit = (ROOT / 'packaging/omarchy-m4/omarchy-m4-boot/omarchy-m4-notch.service').read_bytes()
        for relative, data in (('usr/bin/m4-boot-compose', composer), ('usr/bin/m4-notch', notch)):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        write_size(root, pin['path'], pin['image_bytes'])
        files = {path: (0o644, digest) for path, digest in self.channel['backlight_loaders'].items()}
        files.update({
            'usr/bin/m4-notch': (0o755, sha(notch)),
            CHECK.NOTCH_UNIT: (0o644, sha(unit)),
            'usr/bin/m4-boot-compose': (0o755, sha(composer)),
            'usr/share/libalpm/hooks/90-omarchy-m4-boot.hook': (0o644, sha(hook)),
            'usr/lib/omarchy-m4/j713.dtb': (0o644, self.channel['devicetree']['sha256']),
            'usr/bin/omarchy-m4-update': (0o755, sha(update)),
            'usr/lib/omarchy-m4/m4-channel-setup.py': (0o644, sha(setup)),
            CHECK.REALTIME: (0o644, sha(realtime)),
            pin['path']: (0o644, pin['image_sha256']),
        })
        return root, files

    def speaker_package(self, files, root=None):
        return (Path(f'omarchy-m4-speaker-{SPEAKER_VERSION}-aarch64.pkg.tar.zst'),
                {'pkgname': ['omarchy-m4-speaker'], 'pkgver': [SPEAKER_VERSION], 'arch': ['aarch64']},
                files, root or self.speaker_root)

    def speaker_tree(self, pins, size):
        root = self.root()
        tool = staged('omarchy-m4-speaker', 'm4-speaker-module.py')
        path = root / 'usr/bin/m4-speaker-module'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(tool)
        drop_in = (ROOT / 'packaging/omarchy-m4/omarchy-m4-speaker/20-m4-speaker-module.conf').read_bytes()
        mca_drop = mca_drop_in_bytes()
        files = {module: (0o644, digest) for module, digest in self.channel['speaker_module'].items()}
        files['usr/bin/m4-speaker-module'] = (0o755, sha(tool))
        files[CHECK.SPEAKER_DROP_IN] = (0o644, sha(drop_in))
        for module, digest in pins.items():
            write_size(root, module, size)
            files[module] = (0o644, digest)
        files[MCA_DROP_IN] = (0o644, sha(mca_drop))
        return root, files

    def kernel_package(self, files, info=None, root=None):
        version = '7.1.9.m4.20261008-2'
        info = info or {'pkgname': ['linux-m4-omarchy'], 'pkgver': [version], 'arch': ['aarch64'],
                        'conflict': ['linux-aarch64'],
                        'provides': ['linux-aarch64=7.1.9', 'linux=7.1.9'],
                        'depend': ['coreutils', 'kmod', 'omarchy-m4-boot']}
        return Path(f'linux-m4-omarchy-{version}-aarch64.pkg.tar.zst'), info, files, root or self.kernel_root

    def setUp(self):
        self.boot_root, self.boot_files = self.boot_tree(self.boot_pin)
        self.kernel_root = self.root()
        image = 'usr/lib/omarchy-m4/Image.gz'
        write_size(self.kernel_root, image, self.channel['kernel']['image_bytes'])
        module = f"usr/lib/modules/{self.channel['kernel']['release']}/kernel/x.ko"
        write_size(self.kernel_root, module, 1)
        self.kernel_files = {image: (0o644, self.channel['kernel']['image_sha256']), module: (0o644, 'ab' * 32)}

    def check_boot(self, files=None, channel=None, info=None, root=None):
        CHECK.check_package(channel or self.channel, *self.boot_package(files or self.boot_files, info, root),
                            joins=None)

    def test_base_kernel_package_keeps_the_old_image_and_modules(self):
        # Content only. The published kernel payload join is grandfathered and is not a positive here.
        CHECK.check_package(self.channel, *self.kernel_package(self.kernel_files), joins=None)
        image = 'usr/lib/omarchy-m4/Image.gz'
        swapped = dict(self.kernel_files)
        swapped[image] = (0o644, self.boot_pin['image_sha256'])
        with self.assertRaisesRegex(ValueError, 'pinned kernel'):
            CHECK.check_package(self.channel, *self.kernel_package(swapped))
        without_module = {image: self.kernel_files[image]}
        with self.assertRaisesRegex(ValueError, 'no modules'):
            CHECK.check_package(self.channel, *self.kernel_package(without_module))
        borrowed = dict(self.kernel_files)
        borrowed[self.boot_pin['path']] = (0o644, self.boot_pin['image_sha256'])
        with self.assertRaisesRegex(ValueError, 'unlisted path'):
            CHECK.check_package(self.channel, *self.kernel_package(borrowed))
        short = json.loads(json.dumps(self.channel))
        short['kernel']['image_bytes'] = self.channel['kernel']['image_bytes'] - 1
        with self.assertRaisesRegex(ValueError, 'Kernel image size differs from the pin'):
            CHECK.check_package(short, *self.kernel_package(self.kernel_files))

    def test_boot_package_accepts_one_retained_bounds_image(self):
        self.assertEqual(self.boot_pin['release'], self.channel['kernel']['release'])
        self.assertEqual(self.boot_pin['path'], COMPOSE.KERNEL)
        self.assertIn((self.channel['kernel']['image_sha256'], self.boot_pin['image_sha256']),
                      COMPOSE.KERNEL_TRANSITIONS)
        self.assertEqual(COMPOSE.KNOWN_KERNELS[self.boot_pin['image_sha256']], self.boot_pin['release'])
        self.assertEqual((self.boot_root / self.boot_pin['path']).stat().st_size, self.boot_pin['image_bytes'])
        self.check_boot()

    def test_boot_package_rejects_a_wrong_or_missing_image_without_touching_the_root(self):
        digest_before = sha((self.boot_root / self.boot_pin['path']).read_bytes())
        wrong = dict(self.boot_files)
        wrong[self.boot_pin['path']] = (0o644, '0' * 64)
        with self.assertRaisesRegex(ValueError, 'pinned boot kernel'):
            self.check_boot(wrong)
        missing = {path: meta for path, meta in self.boot_files.items() if path != self.boot_pin['path']}
        with self.assertRaisesRegex(ValueError, 'pinned boot kernel'):
            self.check_boot(missing)
        self.assertEqual(sha((self.boot_root / self.boot_pin['path']).read_bytes()), digest_before)

    def test_boot_package_rejects_a_different_size_or_release(self):
        sized = json.loads(json.dumps(self.channel))
        sized['boot_kernel']['image_bytes'] = self.boot_pin['image_bytes'] + 1
        with self.assertRaisesRegex(ValueError, 'size differs'):
            self.check_boot(channel=sized)
        released = json.loads(json.dumps(self.channel))
        released['boot_kernel']['release'] = self.channel['kernel']['release'] + '-other'
        with self.assertRaisesRegex(ValueError, 'release differs'):
            self.check_boot(channel=released)
        self.assertEqual((self.boot_root / self.boot_pin['path']).stat().st_size, self.boot_pin['image_bytes'])

    def test_composer_path_release_map_and_forward_transition_are_exact(self):
        real = CHECK.runpy.run_path

        def edited(change):
            def run(path, *args, **kwargs):
                namespace = real(path, *args, **kwargs)
                if str(path).endswith('m4-boot-compose'):
                    namespace = dict(namespace)
                    change(namespace)
                return namespace
            return mock.patch.object(CHECK.runpy, 'run_path', side_effect=run)

        def wrong_path(namespace):
            namespace['KERNEL'] = namespace['KERNEL'] + '.rejected'

        def wrong_release(namespace):
            namespace['KNOWN_KERNELS'] = dict(namespace['KNOWN_KERNELS'])
            namespace['KNOWN_KERNELS'][self.boot_pin['image_sha256']] = 'not-the-base-release'

        def reverse_only(namespace):
            namespace['KERNEL_TRANSITIONS'] = {(namespace['NEW_KERNEL'], namespace['OLD_KERNEL'])}

        def forward_only(namespace):
            namespace['KERNEL_TRANSITIONS'] = {(namespace['OLD_KERNEL'], namespace['NEW_KERNEL'])}

        with edited(wrong_path), self.assertRaisesRegex(ValueError, 'kernel path'):
            self.check_boot()
        with edited(wrong_release), self.assertRaisesRegex(ValueError, 'boot kernel release'):
            self.check_boot()
        with edited(reverse_only), self.assertRaisesRegex(ValueError, 'transition'):
            self.check_boot()
        with edited(forward_only), self.assertRaisesRegex(ValueError, 'transition'):
            self.check_boot()

    def test_boot_requires_the_versioned_speaker_package(self):
        base = [item for item in self.boot_info()['depend'] if not item.startswith('omarchy-m4-speaker')]
        for depend in (base, base + ['omarchy-m4-speaker'], base + ['omarchy-m4-speaker>=1-1'],
                       base + ['omarchy-m4-speaker>=1-2'], base + ['omarchy-m4-speaker>=1-3'],
                       base + ['omarchy-m4-speaker=1-2'],
                       base + ['omarchy-m4-speaker>1-2']):
            with self.subTest(depend=depend):
                with self.assertRaisesRegex(ValueError, r'omarchy-m4-speaker>=1-4'):
                    self.check_boot(info=self.boot_info(depend))

    def test_protected_paths_and_source_joins_still_bind_the_boot_package(self):
        channel = json.loads(json.dumps(self.channel))
        channel['allowed_paths']['omarchy-m4-boot'].append('usr/local/bin/brave')
        files = dict(self.boot_files)
        files['usr/local/bin/brave'] = (0o755, 'cd' * 32)
        with self.assertRaisesRegex(ValueError, 'protected path'):
            CHECK.check_package(channel, *self.boot_package(files))
        joins = json.loads(json.dumps(self.joins))
        filename = next(iter(CHECK.package_inputs('omarchy-m4-boot')))
        del joins['binaries'][filename]
        with self.assertRaisesRegex(ValueError, 'source join'):
            CHECK.check_package(self.channel, *self.boot_package(self.boot_files), joins=joins)

    def load_speaker(self):
        self.speaker_root, self.speaker_files = self.speaker_tree(self.mca, SPEAKER.MCA_FIXED['size'])

    def test_speaker_package_accepts_the_fixed_mca_module(self):
        self.load_speaker()
        self.assertEqual(self.mca, {SPEAKER.MCA_STAGED: SPEAKER.MCA_FIXED['sha256']})
        self.assertEqual(SPEAKER.MCA_FIXED['sha256'],
                         'fb941df4d55c4f2093b4595e0392aaf97ffe3a250228518b18a4f70568bf7454')
        self.assertEqual((self.speaker_root / SPEAKER.MCA_STAGED).stat().st_size, SPEAKER.MCA_FIXED['size'])
        self.assertEqual(self.channel['mca_identity'], {
            'sha256': SPEAKER.MCA_FIXED['sha256'], 'size': SPEAKER.MCA_FIXED['size'],
            'note': SPEAKER.MCA_FIXED['note']})
        self.assertEqual(self.channel['mca_module'][SPEAKER.MCA_STAGED], self.channel['mca_identity']['sha256'])
        CHECK.check_package(self.channel, *self.speaker_package(self.speaker_files), joins=None)

    def test_speaker_package_rejects_a_different_mca_identity(self):
        self.load_speaker()
        wrong_hash = 'ab' * 32
        files = dict(self.speaker_files)
        files[SPEAKER.MCA_STAGED] = (0o644, wrong_hash)
        channel = json.loads(json.dumps(self.channel))
        channel['mca_module'] = {SPEAKER.MCA_STAGED: wrong_hash}
        with self.assertRaisesRegex(ValueError, 'MCA'):
            CHECK.check_package(channel, *self.speaker_package(files))
        missing = {path: meta for path, meta in self.speaker_files.items() if path != SPEAKER.MCA_STAGED}
        with self.assertRaisesRegex(ValueError, 'MCA'):
            CHECK.check_package(self.channel, *self.speaker_package(missing))
        short_root, short_files = self.speaker_tree(self.mca, SPEAKER.MCA_FIXED['size'] - 1)
        with self.assertRaisesRegex(ValueError, 'MCA'):
            CHECK.check_package(self.channel, *self.speaker_package(short_files, short_root))

    def test_speaker_helper_note_must_match_the_fixed_mca_identity(self):
        self.load_speaker()
        real = CHECK.runpy.run_path

        def run(path, *args, **kwargs):
            namespace = real(path, *args, **kwargs)
            if Path(path).is_relative_to(self.speaker_root):
                namespace = dict(namespace)
                fixed = dict(namespace['MCA_FIXED'])
                fixed['note'] = 'note-does-not-match'
                namespace['MCA_FIXED'] = fixed
            return namespace

        before = (self.speaker_root / 'usr/bin/m4-speaker-module').read_bytes()
        expected = dict(self.channel['mca_identity'])
        with mock.patch.object(CHECK.runpy, 'run_path', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'MCA'):
                CHECK.check_package(self.channel, *self.speaker_package(self.speaker_files))
        self.assertEqual((self.speaker_root / 'usr/bin/m4-speaker-module').read_bytes(), before)
        self.assertEqual(self.channel['mca_identity'], expected)
        self.assertNotEqual(SPEAKER.MCA_FIXED['note'], 'note-does-not-match')

    def test_mca_drop_in_bytes_are_exact(self):
        commands = service_option_values(mca_drop_in_bytes().decode(), 'ExecStartPre')
        self.assertEqual(commands, [MCA_BOOT_COMMAND])
        self.load_speaker()
        files = dict(self.speaker_files)
        files[MCA_DROP_IN] = (0o644, '0' * 64)
        with self.assertRaisesRegex(ValueError, 'drop-in'):
            CHECK.check_package(self.channel, *self.speaker_package(files))
        missing = {path: meta for path, meta in self.speaker_files.items() if path != MCA_DROP_IN}
        with self.assertRaisesRegex(ValueError, 'drop-in'):
            CHECK.check_package(self.channel, *self.speaker_package(missing))


if __name__ == '__main__':
    unittest.main()
