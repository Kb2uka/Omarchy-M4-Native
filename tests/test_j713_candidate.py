# SPDX-License-Identifier: MIT
import copy
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

REAL_RUN = subprocess.run
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from m4_audio_package import digest
import m4_video_package as video
from m4_board_identities import refused_identities


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'tools' / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


builder = load('candidate_builder', 'prepare-j713-candidate.py')
image = load('candidate_image', 'configure-j713-installer.py')
finalizer = load('candidate_finalizer', 'package-j713-candidate.py')


def write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def entry(data, mode=0o644):
    return dict(sha256=digest(data), size=len(data), mode=mode)


def dtb(compatible=b'apple,j713\0apple,t8132\0'):
    strings = b'compatible\0'
    body = struct.pack('>I', 1) + bytes(4) + struct.pack('>III', 3, len(compatible), 0)
    body += compatible + bytes((-len(compatible)) % 4) + struct.pack('>II', 2, 9)
    return struct.pack('>10I', 0xd00dfeed, 56 + len(body) + len(strings), 56,
                       56 + len(body), 40, 17, 16, 0, len(strings), len(body)) + bytes(16) + body + strings


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(os.umask, os.umask(0o022))
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.kit = self.base / 'baseline'
        self.audio = self.base / 'audio'
        self.browser = self.base / 'browser'
        self.output = self.base / 'successor'
        self.kernel = self.base / 'Image.gz'
        self.desktop = self.base / 'chromium.desktop'
        self.revision = 'b' * 40
        write(self.kernel, b'new kernel')
        self.desktop_raw = (b'[Desktop Entry]\nName=Chromium\nExec=/usr/bin/chromium %U\n'
                            b'Actions=new-window;private;\n[Desktop Action new-window]\n'
                            b'Name=New Window\nExec=/usr/bin/chromium --new-window\n'
                            b'[Desktop Action private]\nExec=/usr/bin/chromium --incognito\n')
        write(self.desktop, self.desktop_raw)
        write(self.kit / 'inputs/Image.gz', b'old kernel')
        old_payload = {'usr/lib/qualified-daemon': b'old daemon'}
        self.old_audio = dict(schema=1, board='apple,j713', source_revision='old',
                              file_metadata={n: entry(d, 0o755) for n, d in old_payload.items()},
                              files={'/' + n: digest(d) for n, d in old_payload.items()})
        old_raw = builder.serialized(self.old_audio)
        write(self.kit / 'packages/audio/manifest.json', old_raw)
        for name, data in old_payload.items():
            write(self.kit / 'packages/audio/root' / name, data, 0o755)
        new_audio = copy.deepcopy(self.old_audio)
        new_audio['source_revision'] = 'new'
        policy = {'etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf': b'policy',
                  'usr/share/wireplumber/scripts/default-nodes/j713-protected.lua': b'script'}
        self.source_policy = policy
        for name, data in {**old_payload, **policy}.items():
            mode = 0o755 if name in old_payload else 0o644
            write(self.audio / 'root' / name, data, mode)
            new_audio['file_metadata'][name] = entry(data, mode)
            new_audio['files']['/' + name] = digest(data)
        new_audio_raw = builder.serialized(new_audio)
        write(self.audio / 'manifest.json', new_audio_raw)
        self.audio_pin = digest(new_audio_raw)
        self.old_video = dict(board='apple,j713', kernel_release='exact',
            boot_kernel_sha256=digest(b'old kernel'), boot_dtb_sha256=digest(dtb()),
            files={'usr/local/bin/brave-m4-hardware': entry(b'old browser', 0o755),
                   'opt/qualified/module.ko': entry(b'unchanged module')},
            links={}, enable_links={}, conflicting_paths=[], required_image_files=[],
            external_firmware_destinations=['usr/lib/firmware/apple/external.bin'],
            firmware_sha256=digest(b'external firmware'), allowed_previous={}, redistributable=False)
        self.old_video_raw = builder.serialized(self.old_video)
        write(self.kit / 'packages/video/manifest.json', self.old_video_raw)
        write(self.kit / 'packages/video/root/usr/local/bin/brave-m4-hardware', b'old browser', 0o755)
        write(self.kit / 'packages/video/root/opt/qualified/module.ko', b'unchanged module')
        write(self.kit / 'source/video-package/qualified.json', self.old_video_raw)
        files = {}
        for name in builder.REQUIRED_BROWSER | {'usr/local/bin/brave'}:
            data = ('new ' + builder.BROWSER_SOURCES[name][0]).encode()
            write(self.browser / 'root' / name, data, builder.BROWSER_PATHS[name])
            files[name] = entry(data, builder.BROWSER_PATHS[name])
        self.bundle = dict(schema=1, source_revision=self.revision, files=files)
        self.bundle_raw = builder.serialized(self.bundle)
        write(self.browser / 'manifest.json', self.bundle_raw)
        files, links = builder.inventory(self.kit)
        baseline_raw = builder.serialized(dict(files=files, links=links, private_only=True,
                                               fresh_install_qualified=False, source_revision='historical'))
        write(self.kit / 'manifest.json', baseline_raw)
        for name, value in dict(BASE_KIT=digest(baseline_raw), OLD_AUDIO=digest(old_raw),
                                OLD_KERNEL=digest(b'old kernel'),
                                NEW_KERNEL=digest(b'new kernel')).items():
            patch = mock.patch.object(builder, name, value)
            patch.start(); self.addCleanup(patch.stop)
        # The supplied revision's source bytes are modeled here. A distinct test
        # proves a git-show/source mismatch refuses the entire join before output.
        patch = mock.patch.object(builder.subprocess, 'check_output',
                                  side_effect=self.source_bytes)
        patch.start(); self.addCleanup(patch.stop)

    def source_bytes(self, args):
        name = args[-1].split(':', 1)[1]
        if name.startswith('audio/j713/policy/'):
            return self.source_policy[name[len('audio/j713/policy/'):]]
        if name in {v[0] for v in builder.BROWSER_SOURCES.values()}:
            return ('new ' + name).encode()
        return (ROOT / name).read_bytes()

    def assemble(self, **changes):
        args = dict(baseline=self.kit, audio=self.audio, audio_pin=self.audio_pin, browser=self.browser,
                    browser_pin=digest(self.bundle_raw), kernel=self.kernel,
                    desktop=self.desktop, desktop_pin=digest(self.desktop_raw), output=self.output,
                    revision=self.revision, private_consent=True)
        args.update(changes)
        return builder.assemble(**args)

    def test_complete_join_preserves_baseline_and_verifies_new_kit(self):
        old = (self.kit / 'manifest.json').read_bytes()
        result = self.assemble()
        self.assertEqual((self.kit / 'manifest.json').read_bytes(), old)
        with mock.patch.object(image, 'HERE', self.output):
            manifest = image.verify_kit(result['kit_sha256'])
        self.assertEqual(manifest['source_revision'], self.revision)
        self.assertEqual(manifest['private_build'], 2)
        self.assertFalse(result['native_qualified'])
        self.assertFalse(result['final_delivery_created'])
        self.assertEqual((self.output / 'inputs/Image.gz').read_bytes(), b'new kernel')
        for profile in ('Brave-Browser', 'Brave-Browser-M4', 'Chromium'):
            p = self.output / ('packages/video/root/etc/skel/.config/omarchy/pending/widevine-' + profile + '.json')
            self.assertEqual(json.loads(p.read_bytes()), {'schema': 1, 'profile': profile, 'consent': 'owner-requested'})
            self.assertEqual(stat.S_IMODE(p.stat().st_mode), 0o600)
        desktop = self.output / 'packages/video/root/etc/skel/.local/share/applications/chromium.desktop'
        self.assertEqual(desktop.read_bytes(), self.desktop_raw.replace(b'Exec=/usr/bin/chromium', b'Exec=/usr/local/bin/chromium'))
        self.assertEqual((self.output / 'packages/video/root/opt/qualified/module.ko').read_bytes(), b'unchanged module')

    def video_image(self):
        result = self.assemble()
        root = self.base / 'image'
        for name in ('etc/m4-root-id', 'var/lib/omarchy/provisioning/pending'):
            write(root / name, b'pending')
        (root / 'usr/lib/modules/exact').mkdir(parents=True)
        write(root / video.MANIFEST, self.old_video_raw)
        write(root / 'usr/local/bin/brave-m4-hardware', b'old browser', 0o755)
        write(root / 'opt/qualified/module.ko', b'unchanged module')
        tree = self.base / 'tree.dtb'; tree.write_bytes(dtb())
        firmware = self.base / 'external'; firmware.write_bytes(b'external firmware')
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(video, 'HERE', self.output / 'source/video-package').start()
        return root, dict(root=root, package=self.output / 'packages/video', pin=result['video_package_sha256'],
                          boot_dtb=tree, boot_kernel=self.kernel, firmware=firmware)

    def test_actual_old_manifest_transition_apply_check_and_modes(self):
        root, args = self.video_image()
        with self.assertRaisesRegex(ValueError, 'Video image differs'):
            video.configure(**args)
        result = video.configure(**args, apply=True)
        self.assertIn(video.MANIFEST, result['changed_files'])
        self.assertEqual(video.configure(**args)['changed_files'], [])
        self.assertEqual(stat.S_IMODE((root / 'usr/local/bin/chromium').stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE((root / 'etc/skel/.config/omarchy/pending/widevine-Chromium.json').stat().st_mode), 0o600)
        self.assertEqual(json.loads((root / video.MANIFEST).read_text())['boot_kernel_sha256'], digest(b'new kernel'))

    def test_sibling_m4_boot_dtb_refuses_before_any_write(self):
        root, args = self.video_image()
        for label, compatible in refused_identities():
            with self.subTest(board=label):
                args['boot_dtb'].write_bytes(dtb(compatible))
                with self.assertRaisesRegex(ValueError, 'Unsupported boot DTB board'):
                    video.configure(**args, apply=True)
                self.assertFalse((root / 'usr/local/bin/chromium').exists())
                self.assertEqual((root / video.MANIFEST).read_bytes(), self.old_video_raw)

    def test_customized_old_runtime_refuses_before_any_write(self):
        root, args = self.video_image()
        write(root / 'usr/local/bin/brave-m4-hardware', b'custom choice', 0o755)
        with self.assertRaisesRegex(ValueError, 'Conflicting existing video file'):
            video.configure(**args, apply=True)
        self.assertFalse((root / 'usr/local/bin/chromium').exists())
        self.assertEqual((root / video.MANIFEST).read_bytes(), self.old_video_raw)

    def test_foreign_manifest_refuses_even_when_payload_matches(self):
        root, args = self.video_image()
        write(root / video.MANIFEST, b'foreign manifest')
        with self.assertRaisesRegex(ValueError, 'Conflicting existing video file'):
            video.configure(**args, apply=True)
        self.assertFalse((root / 'usr/local/bin/chromium').exists())

    def test_missing_consent_refuses_without_output(self):
        with self.assertRaisesRegex(ValueError, 'consent'):
            self.assemble(private_consent=False)
        self.assertFalse(self.output.exists())

    def test_stale_revision_refuses_without_output(self):
        with self.assertRaisesRegex(ValueError, 'Fresh full source'):
            self.assemble(revision='8fbe9b551ecfc9b749c42202bf26ed2115634f28')
        self.assertFalse(self.output.exists())

    def test_changed_kernel_refuses_without_output(self):
        self.kernel.write_bytes(b'wrong image')
        with self.assertRaisesRegex(ValueError, 'successor kernel'):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_changed_source_refuses_without_output(self):
        with mock.patch.object(builder.subprocess, 'check_output', return_value=b'other revision'):
            with self.assertRaisesRegex(ValueError, '(Assembly source|Browser payload|Audio policy) differs'):
                self.assemble()
        self.assertFalse(self.output.exists())

    def test_extra_cdm_bytes_refuse_without_output(self):
        write(self.browser / 'root/opt/WidevineCdm/libwidevinecdm.so', b'not permitted')
        with self.assertRaisesRegex(ValueError, 'Unexpected input members'):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_signed_map_cannot_expand_browser_scope(self):
        raw = copy.deepcopy(self.bundle)
        raw['files']['etc/unsafe'] = entry(b'unsafe')
        new = builder.serialized(raw)
        write(self.browser / 'manifest.json', new)
        write(self.browser / 'root/etc/unsafe', b'unsafe')
        with self.assertRaisesRegex(ValueError, 'code-only scope'):
            self.assemble(browser_pin=digest(new))
        self.assertFalse(self.output.exists())

    def test_symlink_browser_payload_refuses_without_output(self):
        name = sorted(builder.REQUIRED_BROWSER)[0]
        p = self.browser / 'root' / name
        data = p.read_bytes(); p.unlink()
        target = self.base / 'outside'; target.write_bytes(data)
        p.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.assemble()
        self.assertFalse(self.output.exists())

    def test_output_inside_reference_refused_before_copy(self):
        with self.assertRaisesRegex(ValueError, 'inside an input tree'):
            self.assemble(output=self.kit / 'child')
        self.assertFalse((self.kit / 'child').exists())

    def test_unknown_desktop_action_refuses(self):
        raw = self.desktop_raw.replace(b'Exec=/usr/bin/chromium --incognito', b'Exec=another-browser --incognito')
        with self.assertRaisesRegex(ValueError, 'Unexpected Chromium desktop Exec'):
            builder.chromium_desktop(raw)

    def test_audio_transition_cannot_change_preserved_runtime(self):
        raw = json.loads((self.audio / 'manifest.json').read_text())
        name = 'usr/lib/qualified-daemon'
        data = b'changed daemon'
        write(self.audio / 'root' / name, data, 0o755)
        raw['file_metadata'][name] = entry(data, 0o755)
        raw['files']['/' + name] = digest(data)
        changed = builder.serialized(raw)
        write(self.audio / 'manifest.json', changed)
        with self.assertRaisesRegex(ValueError, 'Closed audio default transition'):
            self.assemble(audio_pin=digest(changed))
        self.assertFalse(self.output.exists())

    def test_changing_baseline_copy_cannot_produce_verified_output(self):
        original = builder.shutil.copytree
        def changed(source, target, *args, **kwargs):
            result = original(source, target, *args, **kwargs)
            if Path(target) == self.output:
                write(self.output / 'inputs/Image.gz', b'changed during copy')
            return result
        with mock.patch.object(builder.shutil, 'copytree', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '(Hash/size|Baseline)'):
                self.assemble()
        self.assertFalse((self.output / 'inputs/chromium.desktop').exists())

    def test_changed_audio_pin_refuses_without_output(self):
        write(self.audio / 'manifest.json', (self.audio / 'manifest.json').read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'joined audio package'):
            self.assemble()
        self.assertFalse(self.output.exists())



class BrowserDefaultTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root, self.kit = self.base / 'root', self.base / 'kit'
        self.path = self.root / 'usr/bin/omarchy-provision-user'
        self.old = b'#!/bin/bash\n# fixture\nenv -u BROWSER xdg-settings set default-web-browser brave-m4-hardware.desktop\n'
        self.new = self.old.replace(b'brave-m4-hardware.desktop', b'brave-browser.desktop')
        self.desktop = b'[Desktop Entry]\nExec=brave %U\n[Desktop Action new]\nExec=brave\n[Desktop Action private]\nExec=brave --incognito\n'
        write(self.path, self.old, 0o755)
        write(self.root / 'usr/share/applications/brave-browser.desktop', self.desktop)
        for name in ('packages/audio/manifest.json', 'packages/video/manifest.json', 'manifest.json'):
            write(self.kit / name, b'{}')
        for root, name in ((self.root, 'usr/share/applications/chromium.desktop'),
                           (self.kit, 'inputs/chromium.desktop')):
            write(root / name, b'chromium desktop')
        self.calls = []
        def configure(*args, **kwargs):
            self.calls.append((args, kwargs, self.path.read_bytes()))
        components = {
            'desktop': SimpleNamespace(planned_files=lambda root: None, configure=configure),
            'audio': SimpleNamespace(configure=configure),
            'video': SimpleNamespace(configure=configure),
            'mpv_install': SimpleNamespace(operate=configure),
        }
        for patch in (mock.patch.object(image, 'HERE', self.kit),
                      mock.patch.object(image, 'owner_free'),
                      mock.patch.object(image, 'PREPARED_PROVISION_SHA', digest(self.old)),
                      mock.patch.object(image, 'ORDINARY_BRAVE_DESKTOP_SHA', digest(self.desktop)),
                      mock.patch.object(image, 'load', side_effect=lambda name, path: components[name])):
            patch.start(); self.addCleanup(patch.stop)

    def configure(self, apply=False):
        return image.configure(self.root, self.base / 'firmware', apply=apply)

    def test_exact_prepared_transition_idempotence_and_normal_profile(self):
        with self.assertRaisesRegex(ValueError, 'Browser finalization differs'):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.old)
        self.calls.clear()
        self.configure(apply=True)
        self.assertEqual(self.path.read_bytes(), self.new)
        self.assertTrue(self.calls)
        self.assertTrue(all(raw == self.old for _, _, raw in self.calls), 'Browser changed before component preflights/apply')
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o755)
        before = self.path.stat().st_mtime_ns
        self.configure(apply=True)
        self.configure()
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        self.assertIn(b'default-web-browser brave-browser.desktop\n', self.path.read_bytes())
        self.assertNotIn(b'--user-data-dir', self.desktop)
        self.assertNotIn(b'brave-m4-hardware', self.desktop)
        self.assertEqual((self.root / 'usr/share/applications/brave-browser.desktop').read_bytes(), self.desktop)

    def test_unknown_source_or_unprepared_default_refuses_before_components(self):
        for raw in (self.old + b'# customization\n', self.old.replace(b'brave-m4-hardware.desktop', b'chromium.desktop'),
                    self.old.replace(b'brave-m4-hardware.desktop', b'custom.desktop')):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaisesRegex(ValueError, '(Unknown owner|Unexpected browser)'):
                    self.configure(apply=True)
                self.assertEqual(self.calls, [])
                self.assertEqual(self.path.read_bytes(), raw)

    def test_wrong_ordinary_desktop_refuses_before_components(self):
        desktop = self.root / 'usr/share/applications/brave-browser.desktop'
        desktop.write_bytes(self.desktop.replace(b'Exec=brave', b'Exec=brave-m4-hardware'))
        with self.assertRaisesRegex(ValueError, 'Ordinary Brave desktop'):
            self.configure(apply=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), self.old)

    def test_component_preflight_failure_does_not_change_default(self):
        def load(name, path):
            if name == 'desktop':
                return SimpleNamespace(planned_files=lambda root: None)
            if name == 'audio':
                return SimpleNamespace(configure=mock.Mock(side_effect=ValueError('customized audio')))
            return SimpleNamespace()
        with mock.patch.object(image, 'load', side_effect=load):
            with self.assertRaisesRegex(ValueError, 'customized audio'):
                self.configure(apply=True)
        self.assertEqual(self.path.read_bytes(), self.old)

    def test_existing_owner_refusal_precedes_browser_mutation(self):
        with mock.patch.object(image, 'owner_free', side_effect=ValueError('Owner account remains')):
            with self.assertRaisesRegex(ValueError, 'Owner account remains'):
                self.configure(apply=True)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.path.read_bytes(), self.old)


class FinalizerTests(unittest.TestCase):
    setUp = CandidateTests.setUp
    source_bytes = CandidateTests.source_bytes
    assemble = CandidateTests.assemble

    def package_setup(self):
        result = self.assemble()
        self.kit_pin = result['kit_sha256']
        self.delivery = self.base / 'delivery'
        self.final = self.base / 'final'
        self.disk = self.base / 'root.ext4'
        raw = bytearray(4096)
        raw[1024 + 56:1024 + 58] = b'\x53\xef'
        raw[1024 + 58:1024 + 60] = (1).to_bytes(2, 'little')
        self.root_uuid = '11111111-2222-4333-8444-555555555555'
        raw[1024 + 104:1024 + 120] = __import__('uuid').UUID(self.root_uuid).bytes
        self.disk.write_bytes(raw)
        old = {'root': {'filesystem_uuid': 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'},
               'boot': {'bootargs': 'unchanged'}, 'source_revision': 'old', 'private_only': True}
        config = {'os_list': [{'package': 'old.zip', 'partitions': [{'rootfs_uuid': old['root']['filesystem_uuid']}]}]}
        for name, data in {'IMAGE.json': builder.serialized(old), 'installer_data.json': builder.serialized(config),
                           'START-HERE.md': b'private owner guide', 'source/integration-inputs.tar.gz': b'old sources',
                           'source/kernel-source.tar.gz': b'retained corresponding source', 'os/old.zip': b'old image'}.items():
            write(self.delivery / name, data)
        outer = {p.relative_to(self.delivery).as_posix(): dict(sha256=digest(p.read_bytes()), bytes=p.stat().st_size)
                 for p in self.delivery.rglob('*') if p.is_file()}
        raw_kit = builder.serialized({'files': outer, 'private_only': True, 'fresh_install_qualified': False})
        write(self.delivery / 'KIT.json', raw_kit)
        pins = {n: e['sha256'] for n,e in outer.items()}; pins['KIT.json'] = digest(raw_kit)
        sums = ''.join(pin + '  ' + n + '\n' for n,pin in sorted(pins.items())).encode()
        write(self.delivery / 'SHA256SUMS', sums)
        mock.patch.object(finalizer, 'BASE_SUMS', digest(sums)).start()
        self.addCleanup(mock.patch.stopall)
        # Add each portable boot input before resealing the actual kit verifier.
        for name in ('loader.bin', 'video-base.dtb', 'initramfs.cpio.gz'):
            write(self.output / 'inputs' / name, name.encode())
        manifest = json.loads((self.output / 'manifest.json').read_text())
        manifest['files'], manifest['links'] = builder.inventory(self.output)
        raw = builder.serialized(manifest); write(self.output / 'manifest.json', raw)
        self.kit_pin = digest(raw)
        self.approval = self.base / 'approval.json'
        self.checked = self.base / 'checked.json'
        write(self.approval, builder.serialized({'schema': 1, 'kind': 'j713-private-native-qualification',
               'kit_sha256': self.kit_pin, 'source_revision': self.revision, 'native_qualified': True,
               'passed': {'kernel-reboot-trackpad': True, 'audio-clean-default': True},
               'browsers': {'brave': {'passed': True, 'decoder': 'VaapiVideoDecoder',
                            'hardware_accelerated': True, 'evidence_sha256': 'c' * 64},
                            'chromium': {'passed': True, 'decoder': 'FFmpegVideoDecoder',
                            'hardware_accelerated': False, 'evidence_sha256': 'd' * 64}}}))
        write(self.checked, builder.serialized({'schema': 1, 'kit_sha256': self.kit_pin,
              'root_sha256': digest(self.disk.read_bytes()), 'root_uuid': self.root_uuid,
              'passed': dict.fromkeys(['offline-apply-check','owner-free','locale-utf8','sudo','base-packages','e2fsck'], True)}))
        def commands(args, **kwargs):
            if args[0] == 'losetup':
                return '{"loopdevices": []}'
            return self.source_bytes(args)
        mock.patch.object(finalizer.subprocess, 'check_output', side_effect=commands).start()
        mock.patch.object(finalizer.subprocess, 'run', return_value=__import__('subprocess').CompletedProcess([], 1)).start()

    def package(self):
        return finalizer.finalize(self.delivery, self.output, self.kit_pin, self.disk,
               self.approval, digest(self.approval.read_bytes()), self.checked,
               digest(self.checked.read_bytes()), self.final, ROOT)

    def test_final_metadata_uses_current_revision_and_boot_not_old_literals(self):
        self.package_setup()
        result = self.package()
        metadata = json.loads((self.final / 'IMAGE.json').read_text())
        self.assertEqual(metadata['source_revision'], self.revision)
        self.assertEqual(metadata['root']['filesystem_uuid'], self.root_uuid)
        self.assertEqual(metadata['boot']['kernel']['sha256'], digest(b'new kernel'))
        self.assertEqual(metadata['audio_manifest_sha256'], self.audio_pin)
        self.assertFalse(metadata['native_browser_results']['chromium']['hardware_accelerated'])
        self.assertEqual(metadata['native_browser_results']['chromium']['decoder'], 'FFmpegVideoDecoder')
        self.assertEqual(json.loads((self.final / 'KIT.json').read_text())['native_browser_results'],
                         metadata['native_browser_results'])
        self.assertEqual(result['private_build'], 2)
        self.assertFalse(result['fresh_install_qualified'])
        self.assertFalse((self.final / 'os/old.zip').exists())
        self.assertTrue((self.final / 'os/omarchy-j713-private-0.1.0-alpha.1-build2.zip').is_file())
        with __import__('zipfile').ZipFile(self.final / 'os/omarchy-j713-private-0.1.0-alpha.1-build2.zip') as z:
            self.assertEqual(z.read('root.ext4'), self.disk.read_bytes())
            self.assertEqual(z.read('boot/Image.gz'), b'new kernel')

    def test_unqualified_native_receipt_refuses_before_output(self):
        self.package_setup()
        raw = json.loads(self.approval.read_text()); raw['native_qualified'] = False
        self.approval.write_bytes(builder.serialized(raw))
        with self.assertRaisesRegex(ValueError, 'Native qualification'):
            self.package()
        self.assertFalse(self.final.exists())

    def test_image_changed_since_checks_refuses_before_output(self):
        self.package_setup()
        with self.disk.open('ab') as stream: stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'Image changed'):
            self.package()
        self.assertFalse(self.final.exists())

    def test_each_native_outcome_is_required_despite_overall_approval(self):
        self.package_setup()
        raw = json.loads(self.approval.read_text())
        for name in raw['passed']:
            with self.subTest(name=name):
                changed = copy.deepcopy(raw)
                changed['passed'][name] = False
                self.approval.write_bytes(builder.serialized(changed))
                with self.assertRaisesRegex(ValueError, 'Native qualification'):
                    self.package()
                self.assertFalse(self.final.exists())
        del raw['browsers']['chromium']
        self.approval.write_bytes(builder.serialized(raw))
        with self.assertRaisesRegex(ValueError, 'Both native browser'):
            self.package()
        self.assertFalse(self.final.exists())

    def test_browser_failure_or_missing_decoder_evidence_refuses(self):
        self.package_setup()
        raw = json.loads(self.approval.read_text())
        for name in ('brave', 'chromium'):
            for field, value in (('passed', False), ('decoder', ''), ('hardware_accelerated', 1),
                                 ('evidence_sha256', 'unverified')):
                with self.subTest(browser=name, field=field):
                    changed = copy.deepcopy(raw)
                    changed['browsers'][name][field] = value
                    self.approval.write_bytes(builder.serialized(changed))
                    with self.assertRaisesRegex(ValueError, 'Native browser qualification'):
                        self.package()
                    self.assertFalse(self.final.exists())

    def test_missing_validation_gate_refuses_before_output(self):
        self.package_setup()
        raw = json.loads(self.checked.read_text()); del raw['passed']['owner-free']
        self.checked.write_bytes(builder.serialized(raw))
        with self.assertRaisesRegex(ValueError, 'Incomplete image validation'):
            self.package()
        self.assertFalse(self.final.exists())

    def test_validation_gates_require_dictionary_and_true_values(self):
        self.package_setup()
        raw = json.loads(self.checked.read_text())
        names = list(raw['passed'])
        for index, passed in enumerate((dict.fromkeys(names, False), dict.fromkeys(names, 1), names,
                       {**dict.fromkeys(names, True), 'sudo': 'passed'})):
            with self.subTest(passed=passed):
                self.final = self.base / ('refused-output-' + str(index))
                changed = {**raw, 'passed': passed}
                self.checked.write_bytes(builder.serialized(changed))
                with self.assertRaisesRegex(ValueError, 'Incomplete image validation'):
                    self.package()
                self.assertFalse(self.final.exists())

    def test_attached_image_refuses_before_output(self):
        self.package_setup()
        with mock.patch.object(finalizer.subprocess, 'check_output', return_value='{"loopdevices": [{}]}'):
            with self.assertRaisesRegex(ValueError, 'still attached'):
                self.package()
        self.assertFalse(self.final.exists())

    def test_unattached_image_with_actual_losetup_output(self):
        self.package_setup()
        def commands(args, **kwargs):
            if args[0] == 'losetup':
                return REAL_RUN(args, check=True, capture_output=True, text=True).stdout
            return self.source_bytes(args)
        with mock.patch.object(finalizer.subprocess, 'check_output', side_effect=commands):
            result = self.package()
        self.assertEqual(result['private_build'], 2)


if __name__ == '__main__':
    unittest.main()
