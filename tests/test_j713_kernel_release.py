# SPDX-License-Identifier: MIT
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import tarfile
from types import SimpleNamespace
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('kernel_release', ROOT / 'tools/prepare-j713-release.py')
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def digest(data):
    return hashlib.sha256(data).hexdigest()


class KernelReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.tree = self.base / 'source'
        self.baseline = self.base / 'baseline'
        self.tree.mkdir()
        self.baseline.mkdir()
        self.revision = 'a' * 40
        self.original_kernel = b'original kernel'
        self.kernel_data = b'\x1f\x8bsynthetic candidate kernel'
        self.kernel = self.base / 'Image.gz'
        self.kernel.write_bytes(self.kernel_data)
        self.identity = {
            'kernel': {'sha256': digest(self.kernel_data), 'bytes': len(self.kernel_data)},
            'build': {
                'kernel_release': '7.1.9-m4-gpu-backport-20261008',
                'config_sha256': '1' * 64, 'dockchannel_hid_sha256': '2' * 64,
                'macsmc_sha256': '3' * 64, 'module_symvers_sha256': '4' * 64,
                'vmlinux_symvers_sha256': '4' * 64, 'export_rows': 16412,
                'exports_unchanged': True,
            },
        }
        self.patch_names = (
            'patches-kernel/j713-input/dockchannel-start-retry.patch',
            'patches-kernel/j713-input/macsmc-key-info-lock.patch',
            'patches-kernel/j713-input/dockchannel-afe-action.patch',
        )
        self.patches = {}
        for name in self.patch_names:
            data = ('synthetic source ' + name).encode()
            self.write(self.tree, name, data)
            self.patches[name] = digest(data)
        self.receipt = {
            'schema': 1, 'kind': 'j713-afe-action-kernel',
            'board': 'apple,j713', 'soc': 'apple,t8132',
            'source_revision': self.revision, **copy.deepcopy(self.identity),
            'patches': self.patches.copy(), 'native_startup_qualified': True,
            'native_checks': {name: True for name in (
                'boot', 'touch_mt_ready', 'keyboard_registered', 'trackpad_registered',
                'gpu_service_integrity', 'video_service_integrity',
                'audio_service_integrity', 'repeat_boot')},
            'evidence_sha256': 'e' * 64, 'fresh_install_qualified': False,
            'physical_acceptance': 'not_performed',
        }
        self.receipt_path = self.base / 'receipt.json'
        self.save_receipt()
        for name in ('tools/m4-j713-owner-assets.py', 'tools/m4-j713-fir-owner.py',
                     'packaging/Install.command', 'docs/install.md', 'docs/release-limitations.md',
                     'docs/updates.md', 'docs/j713-poweroff.md', 'docs/release-source.md',
                     'docs/j713-afe-native-action.md', 'audio/j713/owner-fir/README.md',
                     'audio/j713/owner-fir/.gitattributes', 'audio/j713/owner-fir/licenses/LICENSE.txt'):
            self.write(self.tree, name, ('fixture ' + name).encode())
        self.sources = {name: ('current ' + name).encode()
                        for name in ('run-transfer.py', 'run-memory.py', 'measure-memory.c')}
        for name, data in self.sources.items():
            self.write(self.tree, 'audio/j713/owner-fir/src/' + name, data)
        self.profile = {'outputs': {}}
        for index, name in enumerate(release.FIRS):
            data = bytes([index]) * 16
            self.write(self.tree, 'audio/j713/filters/' + name, data)
            self.profile['outputs'][name] = {'size': len(data), 'sha256': digest(data)}
        self.save_profile()
        self.write(self.tree, 'packaging/release-source-joins.json', b'{"schema": 1, "binaries": {}}')
        self.boot = {'bootargs': 'console=tty0 dockchannel_hid.m4_power_v2=1'}
        self.parts = {'kernel': self.original_kernel, 'dtb': b'old DTB',
                      'loader': b'preserved loader', 'initramfs': b'preserved initramfs'}
        for name, data in self.parts.items():
            self.boot[name] = {'file': 'boot/' + name, 'sha256': digest(data)}
        self.config = {'os_list': [{'package': 'old.zip', 'm4_boot': self.boot,
                       'partitions': [{'rootfs_uuid': 'old', 'size': '1B'}]}]}
        self.old_image = {'boot': self.boot, 'audio_manifest_sha256': release.BASE_AUDIO,
                          'video_manifest_sha256': release.BASE_VIDEO}
        self.write(self.baseline, 'installer_data.json', json.dumps(self.config).encode())
        self.write(self.baseline, 'IMAGE.json', json.dumps(self.old_image).encode())
        self.write(self.baseline, 'SHA256SUMS', b'')
        (self.baseline / 'os').mkdir()
        with zipfile.ZipFile(self.baseline / 'os/old.zip', 'w') as archive:
            archive.writestr('root.ext4', b'old root')
            for name, data in self.parts.items():
                archive.writestr(self.boot[name]['file'], data)
        self.fixed_inputs = {}
        for name, pin in {
            'cargo_source': 'faf50f21db4b622900bd275441bc321d1a01933afb555e84d1446d38d6027a34',
            'sound_exports': '9f3e2cf8c83045e3a89b94ef7c8526c4dfb10b386d872669819cd8f4f35d4362',
            'reconstruction': '47c144a606c13f218b1bd84d8a1b86d6d9ac281a58a04d0c191e0508221b1e7c',
        }.items():
            path = self.base / name
            path.write_bytes(name.encode())
            self.fixed_inputs[path] = pin
        self.fixed_inputs[self.baseline / 'SHA256SUMS'] = release.BASE_SUMS
        self.dtb = self.base / 'poweroff.dtb'
        self.dtb.write_bytes(b'preserved poweroff DTB')
        self.args = SimpleNamespace(source_tree=self.tree, baseline=self.baseline,
            root_image=self.base / 'retained.ext4', cargo_source=self.base / 'cargo_source',
            sound_exports=self.base / 'sound_exports', reconstruction=self.base / 'reconstruction',
            poweroff_dtb=self.dtb, output=self.base / 'output', kernel=self.kernel,
            kernel_receipt=self.receipt_path, gpu_module=self.base / 'asahi_m4-fixed.ko')
        self.gpu_data = b'fixed Air GPU module'.ljust(96, b'\x02')
        self.args.gpu_module.write_bytes(self.gpu_data)
        self.gpu_given = []
        self.untracked = set()
        self.prepared = []
        self.allowed = []
        self.repacked = []
        self.stage1_data = b'\x00fixed stage 1'.ljust(64, b'\x01')
        self.stage1 = self.base / 'm1n1-stage1-ans1.bin'
        self.stage1.write_bytes(self.stage1_data)
        self.write(self.tree, release.STAGE1_PATCH, b'synthetic stage 1 patch')
        self.committed = {}

    def write(self, root, name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def save_receipt(self):
        self.receipt_path.write_text(json.dumps(self.receipt))

    def save_profile(self):
        self.write(self.tree, 'audio/j713/owner-fir/manifest.json', json.dumps(self.profile).encode())

    def git_output(self, command, **kwargs):
        if 'ls-files' in command:
            return b'\0'.join(p.relative_to(self.tree).as_posix().encode()
                              for p in sorted(self.tree.rglob('*'))
                              if p.is_file() and p.relative_to(self.tree).as_posix() not in self.untracked) + b'\0'
        if 'show' in command:
            revision, name = command[-1].split(':', 1)
            self.assertEqual(revision, self.revision)
            return self.committed.get(name, (self.tree / name).read_bytes())
        self.fail('Unexpected git call: ' + repr(command))

    def assemble(self):
        actual_sha = release.sha

        def sha(path):
            return self.fixed_inputs.get(path) or actual_sha(path)

        def root(source, image, mount, tree, identity, kernel=None, allow_unverified=False, gpu_module=None):
            self.prepared.append(kernel)
            self.gpu_given.append(gpu_module)
            self.allowed.append(allow_unverified)
            image.write_bytes(b'new root')
            return {'audio': 'a' * 64, 'video': 'v' * 64, 'unverified': []}

        def copy_archive(source, destination, *args, **kwargs):
            self.repacked.append((destination.name, kwargs.get('stage1')))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(b'retained archive')

        with mock.patch.object(release, 'KERNEL_IDENTITY', self.identity, create=True), \
             mock.patch.object(release, 'KERNEL_PATCHES', self.patches, create=True), \
             mock.patch.object(release, 'BASE_KERNEL', digest(self.original_kernel), create=True), \
             mock.patch.object(release, 'POWEROFF_DTB', digest(self.dtb.read_bytes())), \
             mock.patch.object(release, 'STAGE1_ANS1', digest(self.stage1_data)), \
             mock.patch.object(release, 'STAGE1_BYTES', len(self.stage1_data)), \
             mock.patch.object(release, 'GPU_FIXED', digest(self.gpu_data)), \
             mock.patch.object(release, 'GPU_FIXED_BYTES', len(self.gpu_data)), \
             mock.patch.object(release, 'source_revision', return_value=self.revision), \
             mock.patch.object(release, 'sha', side_effect=sha), \
             mock.patch.object(release, 'prepare_root', side_effect=root), \
             mock.patch.object(release, 'repack_installer', side_effect=copy_archive), \
             mock.patch.object(release, 'clean_integration', side_effect=copy_archive), \
             mock.patch.object(release.os, 'geteuid', return_value=0), \
             mock.patch.object(release.sys, 'version_info', (3, 14)), \
             mock.patch.object(release.shutil, 'disk_usage', return_value=SimpleNamespace(free=100 * 1024**3)), \
             mock.patch.object(release.subprocess, 'check_output', side_effect=self.git_output):
            release.assemble(self.args)
        return self.args.output / 'delivery'

    def test_candidate_reaches_zip_both_boot_maps_and_source_without_claiming_fresh_install(self):
        delivery = self.assemble()
        image = json.loads((delivery / 'IMAGE.json').read_bytes())
        config = json.loads((delivery / 'installer_data.json').read_bytes())
        boot = config['os_list'][0]['m4_boot']
        self.assertEqual(boot, image['boot'])
        self.assertEqual(boot['kernel']['sha256'], digest(self.kernel_data))
        self.assertEqual(boot['bootargs'], self.boot['bootargs'] + ' dockchannel_hid.m4_afe_action=1')
        with zipfile.ZipFile(delivery / 'os' / config['os_list'][0]['package']) as archive:
            self.assertEqual(archive.read(boot['kernel']['file']), self.kernel_data)
            for name in ('loader', 'initramfs'):
                self.assertEqual(archive.read(boot[name]['file']), self.parts[name])
            self.assertEqual(archive.read(boot['dtb']['file']), self.dtb.read_bytes())
        self.assertIsNotNone(self.prepared[0])
        self.assertFalse(image['fresh_install_qualified'])
        self.assertTrue(image['kernel_native_startup_qualified'])
        self.assertEqual(image['kernel_receipt_sha256'], digest(self.receipt_path.read_bytes()))
        self.assertEqual((delivery / 'source/j713-kernel-receipt.json').read_bytes(), self.receipt_path.read_bytes())
        with tarfile.open(delivery / 'source/release-integration.tar.gz') as archive:
            for name in self.patch_names:
                self.assertEqual(archive.extractfile(name).read(), (self.tree / name).read_bytes())
            for name in release.FIRS:
                self.assertEqual(archive.extractfile('audio/j713/filters/' + name).read(),
                                 (self.tree / 'audio/j713/filters' / name).read_bytes())
        kit = json.loads((delivery / 'KIT.json').read_bytes())
        self.assertEqual(kit['files']['IMAGE.json']['sha256'], digest((delivery / 'IMAGE.json').read_bytes()))

    def test_without_stage1_the_installer_keeps_alpha1s_loader(self):
        delivery = self.assemble()
        image = json.loads((delivery / 'IMAGE.json').read_bytes())
        self.assertEqual(image['stage1'], {'file': 'installer-macos.tar.gz:boot/m1n1.bin',
                                           'sha256': release.STAGE1_ALPHA1})
        self.assertEqual(self.repacked[:2], [('installer-macos.tar.gz', None), ('installer-source.tar.gz', None)])

    def test_stage1_reaches_only_the_macos_installer_and_the_image_records_it(self):
        self.args.stage1 = self.stage1
        delivery = self.assemble()
        self.assertEqual(self.repacked[:2], [('installer-macos.tar.gz', self.stage1_data),
                                             ('installer-source.tar.gz', None)])
        image = json.loads((delivery / 'IMAGE.json').read_bytes())
        self.assertEqual(image['stage1']['sha256'], digest(self.stage1_data))
        self.assertEqual(image['stage1']['replaces_sha256'], release.STAGE1_ALPHA1)
        self.assertEqual(image['stage1']['source'], release.STAGE1_PATCH)
        self.assertEqual(image['stage1']['issue'], 82)
        with tarfile.open(delivery / 'source/release-integration.tar.gz') as archive:
            self.assertEqual(archive.extractfile(release.STAGE1_PATCH).read(),
                             (self.tree / release.STAGE1_PATCH).read_bytes())

    def test_unknown_stage1_refuses_before_any_output(self):
        for data in (self.stage1_data[:-1] + b'\x02', self.stage1_data + b'\x00'):
            with self.subTest(size=len(data)):
                self.stage1.write_bytes(data)
                self.args.stage1 = self.stage1
                with self.assertRaisesRegex(RuntimeError, 'Unknown stage 1 input'):
                    self.assemble()
                self.assertFalse(self.args.output.exists())
                self.assertEqual(self.prepared, [])

    def test_stage1_patch_must_be_committed(self):
        self.args.stage1 = self.stage1
        self.committed[release.STAGE1_PATCH] = b'another patch'
        with self.assertRaisesRegex(RuntimeError, 'Stage 1 source is not frozen'):
            self.assemble()
        self.assertFalse(self.args.output.exists())

    def test_cli_accepts_stage1(self):
        with mock.patch.object(release, 'assemble') as assemble, \
             mock.patch.object(release.sys, 'argv', ['prepare', *(f'--{n}=x' for n in (
                 'source-tree', 'baseline', 'root-image', 'cargo-source', 'sound-exports',
                 'reconstruction', 'poweroff-dtb', 'gpu-module', 'output')), '--stage1=/k/ans1.bin']):
            release.main()
        self.assertEqual(assemble.call_args.args[0].stage1, Path('/k/ans1.bin'))

    def test_fixed_gpu_module_reaches_the_image_and_is_recorded(self):
        delivery = self.assemble()
        self.assertEqual(self.gpu_given, [self.gpu_data])
        image = json.loads((delivery / 'IMAGE.json').read_bytes())
        self.assertEqual(image['gpu_module'], {
            'file': 'opt/m4-gpu/asahi_m4.ko', 'sha256': digest(self.gpu_data),
            'replaces_sha256': release.GPU_ALPHA1, 'source': 'gpu/j713', 'issues': [57, 118],
            'rollback': 'var/lib/omarchy-m4/gpu-module/alpha1-module.ko'})

    def test_unknown_gpu_module_refuses_before_any_output(self):
        for data in (self.gpu_data[:-1] + b'\x03', self.gpu_data + b'\x00', b''):
            with self.subTest(size=len(data)):
                self.args.gpu_module.write_bytes(data)
                with self.assertRaisesRegex(RuntimeError, 'Unknown GPU module input'):
                    self.assemble()
                self.assertFalse(self.args.output.exists())
                self.assertEqual(self.prepared, [])

    def test_linked_gpu_module_refuses(self):
        link = self.base / 'linked.ko'
        link.symlink_to(self.args.gpu_module)
        self.args.gpu_module = link
        with self.assertRaisesRegex(RuntimeError, 'Unknown GPU module input'):
            self.assemble()
        self.assertFalse(self.args.output.exists())

    def test_cli_requires_gpu_module(self):
        argv = ['prepare', *(f'--{n}=x' for n in ('source-tree', 'baseline', 'root-image', 'cargo-source',
                                                  'sound-exports', 'reconstruction', 'poweroff-dtb', 'output'))]
        with mock.patch.object(release, 'assemble') as assemble, \
             mock.patch.object(release.sys, 'argv', argv), \
             mock.patch('sys.stderr'), self.assertRaises(SystemExit):
            release.main()
        assemble.assert_not_called()
        with mock.patch.object(release, 'assemble') as assemble, \
             mock.patch.object(release.sys, 'argv', argv + ['--gpu-module=/k/asahi_m4.ko']):
            release.main()
        self.assertEqual(assemble.call_args.args[0].gpu_module, Path('/k/asahi_m4.ko'))

    def test_untracked_static_filter_refuses_before_image_preparation(self):
        self.untracked.add('audio/j713/filters/' + release.FIRS[-1])
        with self.assertRaisesRegex(RuntimeError, 'Static FIR assets must be tracked'):
            self.assemble()
        self.assertEqual(self.prepared, [])
        self.assertFalse(self.args.output.exists())

    def join(self, archive, pin):
        joins = {'schema': 1, 'binaries': {'opt/m4-gpu/asahi_m4.ko': {
            'sha256': 'b' * 64, 'correspondence': 'rebuilt-identical',
            'sources': [{'archive': 'source/' + archive, 'sha256': pin}]}}}
        return self.write(self.tree, 'packaging/release-source-joins.json', json.dumps(joins).encode())

    def test_pinned_join_source_reaches_the_kit_and_image_records_the_joins(self):
        packet = self.write(self.base, 'gpu-air-source.tar.gz', b'GPU source packet')
        joins = self.join(packet.name, digest(packet.read_bytes()))
        self.args.join_source = [packet]
        delivery = self.assemble()
        self.assertEqual((delivery / 'source' / packet.name).read_bytes(), packet.read_bytes())
        image = json.loads((delivery / 'IMAGE.json').read_bytes())
        self.assertEqual(image['source_joins_sha256'], digest(joins.read_bytes()))
        self.assertEqual(image['source_correspondence_unverified'], [])
        self.assertEqual(self.allowed, [False])

    def test_a_join_naming_an_absent_source_archive_refuses_before_any_output(self):
        self.join('gpu-air-source.tar.gz', 'c' * 64)
        with self.assertRaisesRegex(RuntimeError, 'will not be in the kit for opt/m4-gpu/asahi_m4.ko'):
            self.assemble()
        self.assertFalse(self.args.output.exists(), 'refused before root preparation and repacking')

    def test_an_unpinned_join_source_refuses_before_any_output(self):
        packet = self.write(self.base, 'gpu-air-source.tar.gz', b'another packet')
        self.join(packet.name, digest(b'GPU source packet'))
        self.args.join_source = [packet]
        with self.assertRaisesRegex(RuntimeError, 'not a pinned archive'):
            self.assemble()
        self.assertFalse(self.args.output.exists())

    def test_unverified_permission_reaches_root_preparation(self):
        self.args.allow_unverified_source = True
        self.assemble()
        self.assertEqual(self.allowed, [True])

    def test_absent_pair_preserves_retained_kernel_and_bootargs(self):
        self.args.kernel = self.args.kernel_receipt = None
        delivery = self.assemble()
        image = json.loads((delivery / 'IMAGE.json').read_bytes())
        self.assertEqual(image['boot']['kernel'], self.boot['kernel'])
        self.assertEqual(image['boot']['bootargs'], self.boot['bootargs'])
        self.assertNotIn('kernel_receipt_sha256', image)
        self.assertEqual(self.prepared, [None])

    def test_optional_inputs_are_an_indivisible_pair(self):
        for field in ('kernel', 'kernel_receipt'):
            original = getattr(self.args, field)
            setattr(self.args, field, None)
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, 'together'):
                self.assemble()
            self.assertFalse(self.args.output.exists())
            setattr(self.args, field, original)

    def test_unqualified_or_incomplete_native_receipt_refuses_before_root_creation(self):
        changes = [('native_startup_qualified', False), ('native_startup_qualified', 1),
                   ('fresh_install_qualified', True), ('physical_acceptance', 'passed'),
                   ('native_checks', {}), ('evidence_sha256', 'not-a-hash')]
        changes += [('native_checks', {**self.receipt['native_checks'], key: False})
                    for key in self.receipt['native_checks']]
        for key, value in changes:
            previous = self.receipt[key]
            self.receipt[key] = value
            self.save_receipt()
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                self.assemble()
            self.assertFalse(self.prepared)
            self.assertFalse(self.args.output.exists())
            self.receipt[key] = previous

    def test_foreign_binary_board_source_abi_and_patch_records_refuse(self):
        changes = [('board', 'apple,j715'), ('soc', 'apple,t8103'), ('source_revision', 'b' * 40),
                   ('kernel', {**self.receipt['kernel'], 'sha256': 'f' * 64}),
                   ('kernel', {**self.receipt['kernel'], 'bytes': 1}), ('patches', {})]
        changes += [('build', {**self.receipt['build'], key: value}) for key, value in (
            ('kernel_release', 'another-kernel'), ('config_sha256', 'f' * 64),
            ('dockchannel_hid_sha256', 'f' * 64), ('macsmc_sha256', 'f' * 64),
            ('module_symvers_sha256', 'f' * 64), ('vmlinux_symvers_sha256', 'f' * 64),
            ('export_rows', 16411), ('exports_unchanged', False))]
        for key, value in changes:
            previous = self.receipt[key]
            self.receipt[key] = value
            self.save_receipt()
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                self.assemble()
            self.assertFalse(self.args.output.exists())
            self.receipt[key] = previous

    def test_kernel_and_patch_byte_changes_are_not_accepted_by_receipt(self):
        for path in (self.kernel, self.tree / self.patch_names[-1]):
            previous = path.read_bytes()
            path.write_bytes(bytes([previous[0] ^ 1]) + previous[1:])
            with self.subTest(path=path), self.assertRaises(RuntimeError):
                self.assemble()
            self.assertFalse(self.args.output.exists())
            path.write_bytes(previous)

    def test_missing_corresponding_source_document_refuses(self):
        for name in ('docs/j713-afe-native-action.md', 'docs/release-source.md'):
            path = self.tree / name
            raw = path.read_bytes()
            path.unlink()
            with self.subTest(name=name), self.assertRaisesRegex(RuntimeError, 'Missing kernel source'):
                self.assemble()
            self.assertFalse(self.args.output.exists())
            path.write_bytes(raw)

    def test_receipt_extra_fields_and_nonboolean_checks_refuse(self):
        self.receipt['private_owner_name'] = 'unexpected'
        self.save_receipt()
        with self.assertRaisesRegex(RuntimeError, 'schema'):
            self.assemble()
        del self.receipt['private_owner_name']
        self.receipt['native_checks']['repeat_boot'] = 1
        self.save_receipt()
        with self.assertRaisesRegex(RuntimeError, 'startup evidence'):
            self.assemble()
        self.assertFalse(self.args.output.exists())

    def test_duplicate_receipt_keys_refuse_in_every_object(self):
        for index, name in enumerate((None, 'kernel', 'build', 'patches', 'native_checks')):
            value = self.receipt if name is None else self.receipt[name]
            encoded = json.dumps(value)
            duplicate = ('{' + json.dumps(next(iter(value))) + ': '
                         '"private receipt value must not be archived", ' + encoded[1:])
            raw = json.dumps(self.receipt).replace(encoded, duplicate, 1)
            self.receipt_path.write_text(raw)
            self.args.output = self.base / ('duplicate-output-' + str(index))
            self.prepared.clear()
            with self.subTest(object=name):
                with self.assertRaisesRegex(RuntimeError, 'Duplicate kernel receipt key'):
                    self.assemble()
                self.assertFalse(self.prepared)
                self.assertFalse(self.args.output.exists())

    def assert_receipt_integer_counter(self, section, key):
        original = self.receipt[section][key]
        for index, value in enumerate((float(original), str(original), True, False, None)):
            self.receipt[section][key] = value
            self.save_receipt()
            self.args.output = self.base / ('counter-output-' + str(index))
            self.prepared.clear()
            with self.subTest(value=value):
                with self.assertRaisesRegex(RuntimeError, 'kernel build identity'):
                    self.assemble()
                self.assertFalse(self.prepared)
                self.assertFalse(self.args.output.exists())
        self.receipt[section][key] = original

    def test_kernel_byte_count_requires_json_integer(self):
        self.assert_receipt_integer_counter('kernel', 'bytes')

    def test_export_row_count_requires_json_integer(self):
        self.assert_receipt_integer_counter('build', 'export_rows')

    def test_malformed_kernel_build_objects_refuse(self):
        for section in ('kernel', 'build'):
            original = self.receipt[section]
            for value in (None, [], 'invalid', {}):
                self.receipt[section] = value
                self.save_receipt()
                with self.subTest(section=section, value=value):
                    with self.assertRaisesRegex(RuntimeError, 'kernel build identity'):
                        self.assemble()
                    self.assertFalse(self.prepared)
                    self.assertFalse(self.args.output.exists())
            self.receipt[section] = original

    def test_stale_or_uncommitted_source_refuses(self):
        original = self.git_output
        for missing in (False, True):
            def git(command, **kwargs):
                if 'show' in command and command[-1].endswith(self.patch_names[-1]):
                    if missing:
                        raise subprocess.CalledProcessError(128, command)
                    return b'older committed patch'
                return original(command, **kwargs)
            self.git_output = git
            error = subprocess.CalledProcessError if missing else RuntimeError
            with self.subTest(missing=missing), self.assertRaises(error):
                self.assemble()
            self.assertFalse(self.args.output.exists())

    def test_wrong_base_kernel_or_conflicting_bootargs_refuse_before_root_creation(self):
        changes = [('kernel', {'file': 'boot/kernel', 'sha256': 'f' * 64}),
                   ('bootargs', 'console=tty0'),
                   ('bootargs', self.boot['bootargs'] + ' dockchannel_hid.m4_power_v2=0'),
                   ('bootargs', self.boot['bootargs'] + ' dockchannel_hid.m4_afe_action=0'),
                   ('bootargs', self.boot['bootargs'] + ' dockchannel_hid.m4_afe_power=1')]
        for key, value in changes:
            original = self.boot[key]
            self.boot[key] = value
            self.write(self.baseline, 'installer_data.json', json.dumps(self.config).encode())
            self.write(self.baseline, 'IMAGE.json', json.dumps(self.old_image).encode())
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                self.assemble()
            self.assertFalse(self.prepared)
            self.assertFalse(self.args.output.exists())
            self.boot[key] = original

    def test_symlink_inputs_refuse(self):
        for field in ('kernel', 'kernel_receipt'):
            original = getattr(self.args, field)
            link = self.base / (field + '.link')
            link.symlink_to(original)
            setattr(self.args, field, link)
            with self.subTest(field=field), self.assertRaises(RuntimeError):
                self.assemble()
            self.assertFalse(self.args.output.exists())
            setattr(self.args, field, original)

    def test_replaced_video_manifest_records_kernel_and_preserves_every_payload(self):
        root = self.base / 'root'
        name = 'usr/local/bin/m4-video-start'
        helper = self.write(root, name, b'old helper')
        helper.chmod(0o755)
        replacement = b'new helper'
        self.write(self.tree, 'packaging/j713-startup/m4-video-start', replacement)
        video = {'board': 'apple,j713', 'boot_kernel_sha256': digest(self.original_kernel),
                 'files': {name: {'sha256': digest(b'old helper'), 'size': 10, 'mode': 0o755},
                           'retained-browser': {'sha256': 'f' * 64, 'size': 10, 'mode': 0o755}},
                 'kernel_release': self.identity['build']['kernel_release'], 'protection': 'unchanged'}
        manifest = self.write(root, 'etc/omarchy/j713-video-manifest.json', json.dumps(video).encode())
        # The fallback lets the same regression demonstrate the old retained-kernel behavior.
        with mock.patch.object(release, 'BASE_VIDEO', digest(manifest.read_bytes())), \
             mock.patch.object(release, 'BASE_VIDEO_START', digest(helper.read_bytes())), \
             mock.patch.object(release, 'BASE_KERNEL', digest(self.original_kernel), create=True):
            if 'kernel' in release.prepare_decoder.__code__.co_varnames:
                pin = release.prepare_decoder(root, self.tree, kernel={'receipt': self.receipt})
            else:
                pin = release.prepare_decoder(root, self.tree)
        expected = copy.deepcopy(video)
        expected['boot_kernel_sha256'] = digest(self.kernel_data)
        expected['files'][name] = {'sha256': digest(replacement), 'size': len(replacement), 'mode': 0o755}
        self.assertEqual(json.loads(manifest.read_bytes()), expected)
        self.assertEqual(pin, digest(manifest.read_bytes()))

    def test_retained_decoder_kernel_identity_refuses_without_changes(self):
        name = 'usr/local/bin/m4-video-start'
        original_helper = b'old helper'
        self.write(self.tree, 'packaging/j713-startup/m4-video-start', b'new helper')
        video = {'board': 'apple,j713', 'boot_kernel_sha256': digest(self.original_kernel),
                 'files': {name: {'sha256': digest(original_helper),
                                  'size': len(original_helper), 'mode': 0o755}},
                 'kernel_release': self.identity['build']['kernel_release']}
        for field, value in (('board', 'apple,j715'), ('boot_kernel_sha256', 'f' * 64),
                             ('kernel_release', 'another-kernel')):
            with self.subTest(field=field):
                root = self.base / ('decoder-' + field)
                helper = self.write(root, name, original_helper)
                helper.chmod(0o755)
                invalid = {**video, field: value}
                original_manifest = json.dumps(invalid).encode()
                manifest = self.write(root, 'etc/omarchy/j713-video-manifest.json', original_manifest)
                original_modes = (helper.stat().st_mode, manifest.stat().st_mode)
                with mock.patch.object(release, 'BASE_VIDEO', digest(original_manifest)), \
                     mock.patch.object(release, 'BASE_VIDEO_START', digest(original_helper)), \
                     mock.patch.object(release, 'BASE_KERNEL', digest(self.original_kernel)):
                    with self.assertRaisesRegex(RuntimeError, 'Retained decoder kernel identity mismatch'):
                        release.prepare_decoder(root, self.tree, kernel={'receipt': self.receipt})
                self.assertEqual(helper.read_bytes(), original_helper)
                self.assertEqual(manifest.read_bytes(), original_manifest)
                self.assertEqual((helper.stat().st_mode, manifest.stat().st_mode), original_modes)

    def test_root_preparation_passes_candidate_to_decoder_inside_image(self):
        class StopAtDecoder(Exception):
            pass

        root = self.base / 'mounted-root'
        source = self.write(self.base, 'retained.ext4', b'synthetic image')
        image = self.base / 'new.ext4'
        candidate = {'receipt': self.receipt}

        def command(*args):
            if args[0] == 'mount':
                self.write(root, 'etc/omarchy/j713-audio-manifest.json', b'audio')

        with mock.patch.object(release, 'BASE_ROOT', digest(source.read_bytes())), \
             mock.patch.object(release, 'command', side_effect=command), \
             mock.patch.object(release, 'owner_audio_manifest', return_value=b'audio'), \
             mock.patch.object(release, 'prepare_startup', return_value=b'audio'), \
             mock.patch.object(release, 'prepare_decoder', side_effect=StopAtDecoder) as decoder:
            with self.assertRaises(StopAtDecoder):
                release.prepare_root(source, image, root, self.tree, 'new-id', kernel=candidate)
        decoder.assert_called_once_with(root, self.tree, kernel=candidate)

    def test_cli_accepts_optional_pair_and_keeps_it_absent_by_default(self):
        args = ['prepare-j713-release.py']
        for name in ('source-tree', 'baseline', 'root-image',
                     'cargo-source', 'sound-exports', 'reconstruction', 'poweroff-dtb', 'gpu-module', 'output'):
            args += ['--' + name, '/fixture/' + name]
        with mock.patch.object(release.sys, 'argv', args), mock.patch.object(release, 'assemble') as assemble:
            release.main()
        self.assertIsNone(assemble.call_args.args[0].kernel)
        self.assertIsNone(assemble.call_args.args[0].kernel_receipt)
        args += ['--kernel', str(self.kernel), '--kernel-receipt', str(self.receipt_path)]
        with mock.patch.object(release.sys, 'argv', args), mock.patch.object(release, 'assemble') as assemble:
            release.main()
        self.assertEqual(assemble.call_args.args[0].kernel, self.kernel)
        self.assertEqual(assemble.call_args.args[0].kernel_receipt, self.receipt_path)


class InstallerStage1Tests(unittest.TestCase):
    SEAMS = ('import system, osenum, stub, diskutil, osinstall, gravity_firmware, m1n1\n'
             'class Installer:\n    def __init__(self, version):\n        pass\n'
             '    def a(self):\n        self.ins.collect_firmware(fw_pkg)\n'
             '    def b(self):\n            self.ins.collect_firmware(pkg)\n')

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.old_loader, self.new_loader = b'alpha.1 stage 1' * 4, b'fixed stage 1!!' * 4

    def installer(self, members, name='installer.tar.gz'):
        path = self.base / name
        with tarfile.open(path, 'w:gz') as archive:
            for member, data in members.items():
                item = tarfile.TarInfo(member)
                item.size, item.mode = len(data), 0o755
                archive.addfile(item, io.BytesIO(data))
        return path

    def repack(self, source, stage1=None, source_archive=False):
        output = self.base / ('out-' + str(len(list(self.base.iterdir()))) + '.tar.gz')
        with mock.patch.object(release, 'STAGE1_ALPHA1', digest(self.old_loader)):
            release.repack_installer(source, output, b'module', source_archive, stage1=stage1)
        with tarfile.open(output) as archive:
            return {item.name: archive.extractfile(item).read() for item in archive if item.isfile()}

    def test_stage1_replaces_only_the_loader(self):
        source = self.installer({'./main.py': self.SEAMS.encode(), './boot/m1n1.bin': self.old_loader,
                                 './m1n1.py': b'builder'})
        before = self.repack(source)
        after = self.repack(source, stage1=self.new_loader)
        self.assertEqual(before['./boot/m1n1.bin'], self.old_loader)
        self.assertEqual(after['./boot/m1n1.bin'], self.new_loader)
        self.assertEqual({k: v for k, v in after.items() if k != './boot/m1n1.bin'},
                         {k: v for k, v in before.items() if k != './boot/m1n1.bin'})
        self.assertIn('j713_owner_assets.py', after)

    def test_unknown_or_missing_installer_loader_refuses(self):
        for members, message in (
                ({'./main.py': self.SEAMS.encode(), './boot/m1n1.bin': b'another loader'}, 'Unknown installer stage 1'),
                ({'./main.py': self.SEAMS.encode()}, 'Missing installer stage 1')):
            with self.subTest(message=message):
                with self.assertRaisesRegex(RuntimeError, message):
                    self.repack(self.installer(members), stage1=self.new_loader)

    def test_source_archive_never_takes_stage1(self):
        source = self.installer({'src/main.py': self.SEAMS.encode()}, 'source.tar.gz')
        self.assertIn('src/j713_owner_assets.py', self.repack(source, source_archive=True))
        with self.assertRaisesRegex(RuntimeError, 'macOS installer only'):
            self.repack(source, stage1=self.new_loader, source_archive=True)


if __name__ == '__main__':
    unittest.main()
