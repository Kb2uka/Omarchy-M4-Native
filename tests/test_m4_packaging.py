# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""M4 channel recipes, pins and package policy agree with the qualified release."""
import hashlib
import importlib.util
import json
from pathlib import Path
import io
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch

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


def file_sha(name):
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def recipe(name):
    return (PACKAGING / name / 'PKGBUILD').read_text()


def field(text, key):
    match = re.search(rf'^{key}=(.*)$', text, re.M)
    return match.group(1).strip("'\"") if match else None


def covers_protected(path, protected):
    """An allowlisted directory that contains a protected path would let a package reach it."""
    return path.endswith('/') and any(p.startswith(path) for p in protected)


class RecipeTests(unittest.TestCase):
    def test_a_directory_over_a_protected_path_is_caught(self):
        self.assertTrue(covers_protected('usr/lib/', CHANNEL['protected_paths']))
        self.assertFalse(covers_protected('usr/lib/gstreamer-1.0/', CHANNEL['protected_paths']))
        self.assertFalse(covers_protected('usr/lib/omarchy-m4/j713.dtb', CHANNEL['protected_paths']))

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
        # The channel ships a devicetree later in the composer's lineage than the
        # installer's update-1 one, so connecting moves an Air forward, never back.
        lineage = list(load('tools/m4-boot-compose.py').KNOWN_DTBS)
        self.assertGreater(lineage.index(CHANNEL['devicetree']['sha256']), lineage.index(release.POWEROFF_DTB))
        text = recipe('linux-m4-omarchy')
        self.assertEqual(field(text, 'pkgver'), '7.1.9.m4.20261008')
        self.assertGreaterEqual(int(field(text, 'pkgrel')), 2, 'must upgrade the installed alpha.1 -1 package')
        self.assertIn("depends=('coreutils' 'kmod' 'omarchy-m4-boot')", text)
        self.assertIn("conflicts=('linux-aarch64')", text)
        self.assertIn("provides=('linux=7.1.9' 'linux-aarch64=7.1.9')", text)

    def test_boot_package_carries_the_front_usb_devicetree(self):
        text = recipe('omarchy-m4-boot')
        self.assertIn('releases/download/package-inputs/j713-usb0.dtb', text)
        self.assertIn(CHANNEL['devicetree']['sha256'], text)
        self.assertEqual(load('tools/m4-boot-compose.py').KNOWN_DTBS[CHANNEL['devicetree']['sha256']],
                         'j713-usb0')
        self.assertIn("depends=('python' 'pacman' 'omarchy-m4-gpu' 'omarchy-m4-headphones' 'omarchy-m4-cpu' "
                      "'omarchy-m4-evdi')\n", text, 'gst-plugins-good must be able to replace the sinks; the GPU '
                      'fix, the headphone jack, CPU idle and USB displays must come with this version')

    def test_evdi_recipe_pins_the_channel_builds(self):
        text = recipe('omarchy-m4-evdi')
        self.assertEqual((field(text, 'pkgver'), field(text, 'pkgrel')), ('1.15.1', '1'))
        for pins, name in ((CHANNEL['evdi_module'], 'evdi-1.15.1'), (CHANNEL['evdi_library'], 'libevdi-1.15.1')):
            for path, digest in pins.items():
                self.assertIn(f"'{digest}'", text)
                self.assertIn(f'package-inputs/{name}-{digest[:8]}.', text)
                self.assertIn(f'"$pkgdir/{path}"', text)
        self.assertIn("provides=('evdi=1.15.1')", text)
        self.assertIn("conflicts=('evdi' 'evdi-dkms' 'evdi-git')", text)
        build = (ROOT / 'display/evdi/build.sh').read_text()
        # The module is built on the alpha.1 kernel tree the channel kernel came from.
        self.assertIn('tree_sha=810339d85aa3171fb1a982b71d01c0bf4de816f81ebbe9149d0dd3268e98ce57', build)
        self.assertIn(f"/usr/lib/modules/{CHANNEL['kernel']['release']}/updates/evdi.ko", text)

    def test_evdi_build_refuses_each_unpinned_input_before_building(self):
        build = ROOT / 'display/evdi/build.sh'
        text = build.read_text()
        tree_pin = re.search(r'^tree_sha=([0-9a-f]{64})$', text, re.M).group(1)
        evdi_pin = re.search(r'^evdi_sha=([0-9a-f]{64})$', text, re.M).group(1)
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            # A sha256sum stand-in: the files named "pinned-tree" and "pinned-evdi" hash to the
            # pins, so each check is exercised alone with a well-formed but unpinned partner.
            shim = temp / 'bin/sha256sum'
            shim.parent.mkdir()
            shim.write_text('#!/bin/sh\ncase "$(cat)" in\n'
                            f'  pinned-tree) echo "{tree_pin}  -" ;;\n'
                            f'  pinned-evdi) echo "{evdi_pin}  -" ;;\n'
                            '  *) echo "0000000000000000000000000000000000000000000000000000000000000000  -" ;;\n'
                            'esac\n')
            shim.chmod(0o755)
            tree, evdi, other = temp / 'tree.tar.gz', temp / 'evdi.tar.gz', temp / 'other.tar.gz'
            tree.write_text('pinned-tree')
            evdi.write_text('pinned-evdi')
            with tarfile.open(other, 'w:gz') as archive:  # a valid archive that is not the pin
                archive.addfile(tarfile.TarInfo('evdi-1.15.1/README.md'), io.BytesIO(b''))
            env = {**os.environ, 'PATH': f"{shim.parent}:{os.environ['PATH']}"}
            # Each case names the archive that must be refused; the message proves which check
            # stopped the build, whatever state the host's /job is in.
            for name, args in (('module: unpinned tree', ['module', other, evdi]),
                               ('module: unpinned evdi', ['module', tree, other]),
                               ('library: unpinned evdi', ['library', other])):
                with self.subTest(name):
                    out = temp / ('out-' + name.replace(' ', '-').replace(':', ''))
                    result = subprocess.run(['bash', str(build), *map(str, args), str(out)],
                                            capture_output=True, text=True, env=env)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f'refusing {other}: not the pinned input', result.stderr)
                    self.assertFalse(out.exists(), 'nothing is built from an unpinned archive')

    def test_gpu_recipe_pins_the_channel_module(self):
        text = recipe('omarchy-m4-gpu')
        for digest in CHANNEL['gpu_module'].values():
            self.assertIn(digest, text)
            self.assertIn(f'package-inputs/asahi_m4-{digest[:8]}.ko', text)
        tool = load('tools/m4-gpu-module.py')
        self.assertEqual(set(CHANNEL['gpu_module']), {tool.STAGED})
        self.assertEqual(set(CHANNEL['gpu_module'].values()), {tool.FIXED['sha256']})
        setup = load('tools/m4-channel-setup.py')
        self.assertEqual((setup.GPU_STAGED, setup.GPU_MODULE, setup.GPU_STOCK),
                         (tool.STAGED, tool.MODULE, tool.STOCK['sha256']))
        self.assertEqual(setup.GPU_HOLD, tool.STATE + '/hold')
        helper = (ROOT / 'gpu/j713/m4-gpu-test-module').read_text()
        self.assertIn('stock=' + tool.STOCK['sha256'], helper)

    def test_realtime_setting_matches_the_image_unit(self):
        drop_in = (PACKAGING / 'omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_text()
        unit = (ROOT / 'packaging/j713-audio/speakersafetyd-j713.service').read_text()
        for line in ('CPUSchedulingPolicy=fifo', 'CPUSchedulingPriority=20'):
            self.assertIn(line + '\n', drop_in)
            self.assertIn(line + '\n', unit)

    def test_gst_audio_recipe_pins_the_channel_sinks(self):
        text = recipe('omarchy-m4-gst-audio')
        for digest in CHANNEL['gst_audio'].values():
            self.assertIn(digest, text)
        self.assertIn("conflicts=('gst-plugins-good')", text)

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
        exceptions = CHANNEL['protected_exceptions']
        for name, paths in CHANNEL['allowed_paths'].items():
            for path in paths:
                if path in exceptions:
                    continue
                self.assertFalse(CHECK.allowed(path, CHANNEL['protected_paths']), f'{name}: {path}')
                self.assertFalse(covers_protected(path, CHANNEL['protected_paths']), f'{name}: {path}')
        for path, reason in exceptions.items():
            # An exception is one exact file, inside a protected prefix, owned by one package, with a reason.
            self.assertFalse(path.endswith('/'), path)
            self.assertTrue(CHECK.allowed(path, CHANNEL['protected_paths']), path)
            self.assertEqual(sum(path in paths for paths in CHANNEL['allowed_paths'].values()), 1, path)
            self.assertTrue(reason.strip(), path)
        for protected in ('opt/m4-video/20261011/vaapi/v4l2_request_drv_video.so', 'usr/local/bin/brave',
                          'opt/m4-gpu/asahi_m4.ko', 'opt/m4-gpu/runtime.sha256',
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

    def test_only_the_listed_protected_exception_passes(self):
        realtime = (PACKAGING / 'omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_bytes()
        boot = self.boot_files()
        boot[CHECK.REALTIME] = (0o644, hashlib.sha256(realtime).hexdigest())
        CHECK.check_package(CHANNEL, *self.boot_package(boot))
        for path in ('usr/lib/systemd/system/speakersafetyd-j713.service.d/60-other.conf',
                     'usr/lib/systemd/system/speakersafetyd-j713.service'):
            with self.subTest(path):
                channel = json.loads(json.dumps(CHANNEL))
                channel['allowed_paths']['omarchy-m4-boot'].append(path)
                with self.assertRaisesRegex(ValueError, 'protected path'):
                    CHECK.check_package(channel, *self.boot_package({**boot, path: (0o644, 'x')}))

    def test_boot_package_needs_the_realtime_setting_not_the_sinks(self):
        realtime = (PACKAGING / 'omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_bytes()
        cases = (({**self.boot_files()}, self.boot_info(), 'real-time speaker setting'),
                 ({**self.boot_files(), CHECK.REALTIME: (0o644, hashlib.sha256(b'other').hexdigest())},
                  self.boot_info(), 'real-time speaker setting'),
                 ({**self.boot_files(), CHECK.REALTIME: (0o644, hashlib.sha256(realtime).hexdigest())},
                  {**self.boot_info(), 'depend': ['python', 'pacman', 'omarchy-m4-gst-audio']},
                  'must not require the GStreamer sinks'),
                 ({**self.boot_files(), CHECK.REALTIME: (0o644, hashlib.sha256(realtime).hexdigest())},
                  {**self.boot_info(), 'depend': ['python', 'pacman', 'omarchy-m4-gst-audio>=1.28.7']},
                  'must not require the GStreamer sinks'))
        for files, info, message in cases:
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, *self.boot_package(files, info))

    def test_gst_audio_package_must_be_the_pin(self):
        good = {path: (0o755, digest) for path, digest in CHANNEL['gst_audio'].items()}
        info = {'pkgname': ['omarchy-m4-gst-audio'], 'pkgver': ['1.28.7-1'], 'arch': ['aarch64'],
                'conflict': ['gst-plugins-good'],
                'depend': ['gstreamer>=1.28.7', 'gst-plugins-base-libs>=1.28.7', 'libpulse']}
        CHECK.check_package(CHANNEL, *self.package('omarchy-m4-gst-audio', good, info=info, version='1.28.7-1'))
        path = next(iter(good))
        for files, case_info, message in (({**good, path: (0o755, '0' * 64)}, info, 'differs from the pin'),
                                          (good, {**info, 'conflict': []}, 'conflict with gst-plugins-good'),
                                          (good, {**info, 'depend': ['gstreamer>=1.28.7']}, 'dependencies changed')):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, *self.package('omarchy-m4-gst-audio', files, info=case_info,
                                                               version='1.28.7-1'))

    def test_speaker_package_must_be_the_pin(self):
        good = {path: (0o644, digest) for path, digest in CHANNEL['speaker_module'].items()}
        good['usr/bin/m4-speaker-module'] = (0o755, file_sha('tools/m4-speaker-module.py'))
        good[CHECK.SPEAKER_DROP_IN] = (0o644, file_sha('packaging/omarchy-m4/omarchy-m4-speaker/20-m4-speaker-module.conf'))
        CHECK.check_package(CHANNEL, *self.package('omarchy-m4-speaker', good))
        staged = next(iter(CHANNEL['speaker_module']))
        for files, message in (({**good, staged: (0o644, '0' * 64)}, 'differs from the pin'),
                               ({**good, 'usr/bin/m4-speaker-module': (0o755, '0' * 64)}, 'tool differs'),
                               ({**good, CHECK.SPEAKER_DROP_IN: (0o644, '0' * 64)}, 'drop-in differs')):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, *self.package('omarchy-m4-speaker', files))

    def test_boot_package_must_bring_the_gpu_headphone_cpu_and_evdi_packages(self):
        realtime = (PACKAGING / 'omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_bytes()
        files = {**self.boot_files(), CHECK.REALTIME: (0o644, hashlib.sha256(realtime).hexdigest())}
        CHECK.check_package(CHANNEL, *self.boot_package(files))
        with self.assertRaisesRegex(ValueError, 'must require the GPU module package'):
            CHECK.check_package(CHANNEL, *self.boot_package(files, {**self.boot_info(), 'depend': ['python', 'pacman']}))
        with self.assertRaisesRegex(ValueError, 'must require the headphone jack package'):
            CHECK.check_package(CHANNEL, *self.boot_package(
                files, {**self.boot_info(), 'depend': ['python', 'pacman', 'omarchy-m4-gpu']}))
        without_evdi = [d for d in self.boot_info()['depend'] if d != 'omarchy-m4-evdi']
        with self.assertRaisesRegex(ValueError, 'must require the USB display package'):
            CHECK.check_package(CHANNEL, *self.boot_package(files, {**self.boot_info(), 'depend': without_evdi}))

    def evdi_info(self):
        return {'pkgname': ['omarchy-m4-evdi'], 'pkgver': ['1.15.1-1'], 'arch': ['aarch64'],
                'provides': ['evdi=1.15.1'], 'conflict': ['evdi', 'evdi-dkms', 'evdi-git'],
                'depend': ['linux-m4-omarchy', 'kmod']}

    def test_evdi_package_must_be_the_pin_with_arch_evdi_relations(self):
        good = {**{p: (0o644, d) for p, d in CHANNEL['evdi_module'].items()},
                **{p: (0o755, d) for p, d in CHANNEL['evdi_library'].items()},
                'usr/share/licenses/omarchy-m4-evdi/GPL-2.0-only.txt': (0o644, file_sha('LICENSES/GPL-2.0-only.txt'))}
        package = lambda files, info=None: self.package('omarchy-m4-evdi', files, info or self.evdi_info(),
                                                         version='1.15.1-1')
        CHECK.check_package(CHANNEL, *package(good))
        module, library = next(iter(CHANNEL['evdi_module'])), next(iter(CHANNEL['evdi_library']))
        other_release = module.replace(CHANNEL['kernel']['release'], '7.2.0-other')
        for files, info, message in (
                ({**good, module: (0o644, '0' * 64)}, None, 'evdi module differs from the pin'),
                ({k: v for k, v in good.items() if k != library}, None, 'evdi library differs from the pin'),
                ({**good, 'usr/lib/libevdi.so.1': (0o644, 'x')}, None, 'unlisted path'),
                ({**good, other_release: (0o644, 'x')}, None, 'unlisted path'),
                (good, {**self.evdi_info(), 'provides': []}, 'relations changed'),
                (good, {**self.evdi_info(), 'provides': ['evdi=1.16.0']}, 'relations changed'),
                (good, {**self.evdi_info(), 'conflict': ['evdi']}, 'relations changed')):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                CHECK.check_package(CHANNEL, *package(files, info))
        channel = json.loads(json.dumps(CHANNEL))
        channel['evdi_module'] = {other_release: '0' * 64}
        channel['allowed_paths']['omarchy-m4-evdi'].append(other_release)
        with self.assertRaisesRegex(ValueError, 'not installed for the channel kernel'):
            CHECK.check_package(channel, *package({**good, other_release: (0o644, '0' * 64)}))

    def test_gpu_package_must_be_the_pin(self):
        good = {path: (0o644, digest) for path, digest in CHANNEL['gpu_module'].items()}
        good['usr/bin/m4-gpu-module'] = (0o755, file_sha('tools/m4-gpu-module.py'))
        good[CHECK.GPU_DROP_IN] = (0o644, file_sha('packaging/omarchy-m4/omarchy-m4-gpu/20-m4-gpu-module.conf'))
        CHECK.check_package(CHANNEL, *self.package('omarchy-m4-gpu', good))
        staged = next(iter(CHANNEL['gpu_module']))
        for files, message in (({**good, staged: (0o644, '0' * 64)}, 'differs from the pin'),
                               ({**good, 'usr/bin/m4-gpu-module': (0o755, '0' * 64)}, 'tool differs'),
                               ({**good, CHECK.GPU_DROP_IN: (0o644, '0' * 64)}, 'drop-in differs'),
                               ({**good, 'opt/m4-gpu/asahi_m4.ko': (0o644, '0' * 64)}, 'unlisted path')):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, *self.package('omarchy-m4-gpu', files))

    def test_gpu_drop_in_cannot_block_graphics_startup(self):
        text = (PACKAGING / 'omarchy-m4-gpu/20-m4-gpu-module.conf').read_text()
        self.assertIn('ExecStartPre=-/usr/bin/m4-gpu-module --boot\n', text)

    def test_gpu_start_output_reaches_the_journal(self):
        # The failure notice points at journalctl -u m4-gpu.service; /tmp is gone after a restart.
        for path in (PACKAGING / 'omarchy-m4-gpu/20-m4-gpu-module.conf',
                     ROOT / 'packaging/j713-startup/m4-gpu.service'):
            with self.subTest(path.name):
                text = path.read_text()
                self.assertIn('[Service]\n', text)
                self.assertIn('StandardOutput=journal\n', text)
                self.assertIn('StandardError=journal\n', text)
                self.assertNotIn('append:', text)

    def test_speaker_drop_in_cannot_block_registration(self):
        text = (PACKAGING / 'omarchy-m4-speaker/20-m4-speaker-module.conf').read_text()
        self.assertIn('ExecStartPre=-/usr/bin/m4-speaker-module --boot\n', text)

    def test_headphone_package_must_be_the_pin(self):
        good = {path: (0o644, digest) for path, digest in CHANNEL['headphone_modules'].items()}
        good['usr/bin/m4-headphones'] = (0o755, file_sha('tools/m4-headphones.py'))
        good[CHECK.HEADPHONES_DROP_IN] = (0o644, file_sha('packaging/omarchy-m4/omarchy-m4-headphones/30-m4-headphones.conf'))
        for path in CHECK.HEADPHONES_POLICY:
            good[path] = (0o644, file_sha('audio/j713/headphones/policy/' + path))
        CHECK.check_package(CHANNEL, *self.package('omarchy-m4-headphones', good))
        staged = next(iter(CHANNEL['headphone_modules']))
        speaker_policy = 'usr/share/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf'
        for files, message in (({**good, staged: (0o644, '0' * 64)}, 'differs from the pin'),
                               ({**good, 'usr/bin/m4-headphones': (0o755, '0' * 64)}, 'tool differs'),
                               ({**good, CHECK.HEADPHONES_DROP_IN: (0o644, '0' * 64)}, 'drop-in differs'),
                               ({**good, CHECK.HEADPHONES_POLICY[0]: (0o644, '0' * 64)}, 'policy differs'),
                               ({**good, speaker_policy: (0o644, 'x')}, 'unlisted path')):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, *self.package('omarchy-m4-headphones', files))

    def test_cpu_idle_package_must_be_the_pin(self):
        good = {path: (0o644, digest)
                for path, digest in {**CHANNEL['cpu_idle_module'], **CHANNEL['cpu_guard_module']}.items()}
        good['usr/bin/m4-cpu-idle'] = (0o755, file_sha('tools/m4-cpu-idle.py'))
        good[CHECK.CPU_IDLE_UNIT] = (0o644, file_sha('packaging/omarchy-m4/omarchy-m4-cpu/m4-cpu-idle.service'))
        package, info, files, root = self.package('omarchy-m4-cpu', good)
        (root / 'usr/bin').mkdir(parents=True, exist_ok=True)
        (root / 'usr/bin/m4-cpu-idle').write_bytes((ROOT / 'tools/m4-cpu-idle.py').read_bytes())
        CHECK.check_package(CHANNEL, package, info, files, root)
        staged = next(iter(CHANNEL['cpu_idle_module']))
        guard = next(iter(CHANNEL['cpu_guard_module']))
        other_unit = 'usr/lib/systemd/system/m4-gpu.service'
        for changed, message in (({**good, staged: (0o644, '0' * 64)}, 'differs from the pin'),
                                 ({**good, guard: (0o644, '0' * 64)}, 'boost guard differs from the pin'),
                                 ({**good, 'usr/bin/m4-cpu-idle': (0o755, '0' * 64)}, 'tool differs'),
                                 ({**good, CHECK.CPU_IDLE_UNIT: (0o644, '0' * 64)}, 'unit differs'),
                                 ({**good, other_unit: (0o644, 'x')}, 'unlisted path')):
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, message):
                    CHECK.check_package(CHANNEL, package, info, changed, root)

    def test_cpu_idle_tool_pin_must_match_the_channel(self):
        for key in ('cpu_idle_module', 'cpu_guard_module'):
            with self.subTest(key):
                self.tool_pin_refused(next(iter(CHANNEL[key].values())))

    def tool_pin_refused(self, pinned):
        good = {path: (0o644, digest)
                for path, digest in {**CHANNEL['cpu_idle_module'], **CHANNEL['cpu_guard_module']}.items()}
        tool = (ROOT / 'tools/m4-cpu-idle.py').read_text()
        other = tool.replace(pinned, '1' * 64)
        self.assertNotEqual(other, tool)
        good['usr/bin/m4-cpu-idle'] = (0o755, hashlib.sha256(other.encode()).hexdigest())
        good[CHECK.CPU_IDLE_UNIT] = (0o644, file_sha('packaging/omarchy-m4/omarchy-m4-cpu/m4-cpu-idle.service'))
        package, info, files, root = self.package('omarchy-m4-cpu', good)
        (root / 'usr/bin').mkdir(parents=True, exist_ok=True)
        (root / 'usr/bin/m4-cpu-idle').write_text(other)
        with patch.object(CHECK, 'ROOT', root.parent / 'fake-repo'):
            fake = root.parent / 'fake-repo'
            for name in ('tools/m4-cpu-idle.py', 'packaging/omarchy-m4/omarchy-m4-cpu/m4-cpu-idle.service'):
                (fake / name).parent.mkdir(parents=True, exist_ok=True)
            (fake / 'tools/m4-cpu-idle.py').write_text(other)
            (fake / 'packaging/omarchy-m4/omarchy-m4-cpu/m4-cpu-idle.service').write_bytes(
                (PACKAGING / 'omarchy-m4-cpu/m4-cpu-idle.service').read_bytes())
            with self.assertRaisesRegex(ValueError, 'pins a different module'):
                CHECK.check_package(CHANNEL, package, info, files, root)

    def test_every_channel_package_comes_with_the_update(self):
        # One update path: omarchy-m4-update installs every channel package, none is opt-in.
        self.assertNotIn('opt_in_packages', CHANNEL)
        self.assertEqual(list(load('tools/m4-channel-setup.py').PACKAGES), CHANNEL['packages'])

    def test_headphone_drop_in_runs_after_registration_and_cannot_fail_it(self):
        text = (PACKAGING / 'omarchy-m4-headphones/30-m4-headphones.conf').read_text()
        self.assertIn('ExecStartPost=-/usr/bin/m4-headphones --boot\n', text)
        self.assertNotIn('ExecStartPre', text)

    def test_headphone_pins_agree(self):
        tool = load('tools/m4-headphones.py')
        self.assertEqual({f'{tool.STAGED}/{filename}': pin['sha256'] for _, filename, pin in tool.MODULES},
                         CHANNEL['headphone_modules'])
        sums = re.search(r'^sha256sums=\((.*?)\)', recipe('omarchy-m4-headphones'), re.M | re.S).group(1)
        for digest in CHANNEL['headphone_modules'].values():
            self.assertIn(f"'{digest}'", sums)

    def boot_package(self, files, info=None):
        return Path('omarchy-m4-boot-1-4-aarch64.pkg.tar.zst'), info or self.boot_info(), files, self.boot_root

    def boot_info(self):
        return {'pkgname': ['omarchy-m4-boot'], 'pkgver': ['1-4'], 'arch': ['aarch64'],
                'depend': ['python', 'pacman', 'omarchy-m4-gpu', 'omarchy-m4-headphones', 'omarchy-m4-cpu',
                           'omarchy-m4-evdi']}

    def boot_files(self):
        """A boot package that passes every check except the ones under test, unpacked on disk."""
        composer = (ROOT / 'tools/m4-boot-compose.py').read_bytes()
        hook = (PACKAGING / 'omarchy-m4-boot/90-omarchy-m4-boot.hook').read_bytes()
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.boot_root = Path(temp.name)
        (self.boot_root / 'usr/bin').mkdir(parents=True)
        (self.boot_root / 'usr/bin/m4-boot-compose').write_bytes(composer)
        notch = (ROOT / 'tools/m4-notch.py').read_bytes()
        (self.boot_root / 'usr/bin/m4-notch').write_bytes(notch)
        return {**{path: (0o644, digest) for path, digest in CHANNEL['notch_loader'].items()},
                'usr/bin/m4-notch': (0o755, hashlib.sha256(notch).hexdigest()),
                CHECK.NOTCH_UNIT: (0o644, file_sha('packaging/omarchy-m4/omarchy-m4-boot/omarchy-m4-notch.service')),
                'usr/bin/m4-boot-compose': (0o755, hashlib.sha256(composer).hexdigest()),
                'usr/share/libalpm/hooks/90-omarchy-m4-boot.hook': (0o644, hashlib.sha256(hook).hexdigest()),
                'usr/lib/omarchy-m4/j713.dtb': (0o644, CHANNEL['devicetree']['sha256']),
                'usr/bin/omarchy-m4-update': (0o755, file_sha('tools/omarchy-m4-update')),
                'usr/lib/omarchy-m4/m4-channel-setup.py': (0o644, file_sha('tools/m4-channel-setup.py'))}

    def test_boot_package_must_carry_the_pinned_notch_switch(self):
        realtime = (PACKAGING / 'omarchy-m4-boot/speakersafetyd-j713-realtime.conf').read_bytes()
        good = {**self.boot_files(), CHECK.REALTIME: (0o644, hashlib.sha256(realtime).hexdigest())}
        CHECK.check_package(CHANNEL, *self.boot_package(good))
        staged = next(iter(CHANNEL['notch_loader']))
        for files, message in (({**good, staged: (0o644, '0' * 64)}, 'notch stage 2 differs'),
                               ({k: v for k, v in good.items() if k != staged}, 'notch stage 2 differs'),
                               ({**good, 'usr/bin/m4-notch': (0o755, '0' * 64)}, 'notch tool differs'),
                               ({**good, CHECK.NOTCH_UNIT: (0o644, '0' * 64)}, 'login unit differs')):
            with self.subTest(message), self.assertRaisesRegex(ValueError, message):
                CHECK.check_package(CHANNEL, *self.boot_package(files))
        channel = json.loads(json.dumps(CHANNEL))
        channel['notch_loader'] = {staged: '1' * 64}
        files = {**good, staged: (0o644, '1' * 64)}
        with self.assertRaisesRegex(ValueError, 'pins a different stage 2'):
            CHECK.check_package(channel, *self.boot_package(files))

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
                self.assertFalse(outside.exists() or (root / 'escape').exists(), 'nothing is written outside')

    def test_other_module_release_is_rejected(self):
        files = {'usr/lib/modules/7.2.0-other/kernel/x.ko': (0o644, '1' * 64)}
        with self.assertRaisesRegex(ValueError, 'unlisted path'):
            CHECK.check_package(CHANNEL, *self.package('linux-m4-omarchy', files, version='7.2.0-1'))

    def test_package_inputs_must_have_source_joins(self):
        joins = CHECK.source_joins()
        for name in CHANNEL['packages']:
            inputs = CHECK.package_inputs(name)
            for filename, digest in inputs.items():
                self.assertIn(filename, joins['binaries'])
                self.assertEqual(joins['binaries'][filename]['sha256'], digest)
                self.assertIn(joins['binaries'][filename]['correspondence'], CHECK.CORRESPONDENCE)


if __name__ == '__main__':
    unittest.main()
