# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Copyright (c) 2026 Kazuhide Takahashi
"""The 15-inch M4 Air tester kit: derived from a miniature J713 kit with a real ext4 root."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('j715_test_kit', 'tools/derive-j715-test-kit.py')
PACKAGER = load('j713_download', 'tools/package-j713-download.py')
TOOLS = all(shutil.which(tool) for tool in ('mkfs.ext4', 'debugfs', 'e2fsck', 'patch'))
REVISION = 'a' * 40
digest = lambda data: hashlib.sha256(data).hexdigest()

GPU_OLD, GPU_NEW = b'J713 GPU module', b'J715 GPU module'
GPU_SUMS = (f'{digest(b"mesa")}  /opt/m4-mesa/lib/libgallium.so\n'
            f'{digest(GPU_OLD)}  {MODULE.GPU_MODULE}\n').encode()
GPU_START = b'#!/bin/sh\nset -e\n' + MODULE.GPU_START_LINE[0] + b'exec /usr/bin/true\n'
PLACEMENT = b'# J713 session placement\n'
BOOT = {name: f'{name} bytes'.encode() for name in MODULE.KEPT_BOOT}
OWNER_DROPINS = {
    '/usr/lib/systemd/system/omarchy-provision-owner.service.d/10-owner-assets.conf':
        ROOT / 'packaging/j713-owner-assets-provision.conf',
    '/usr/lib/systemd/system/systemd-user-sessions.service.d/10-owner-assets.conf':
        ROOT / 'packaging/j713-owner-assets-session.conf',
}
AUDIO_GATE = '/etc/systemd/user/wireplumber.service.d/80-j713-audio-ready.conf'
AUDIO_SOURCE = ROOT / 'packaging/j713-audio/wireplumber.service.d/80-j713-audio-ready.conf'
OLD_DTB, NEW_DTB = b'J713 devicetree', b'J715 devicetree'
OLD_KERNEL, NEW_KERNEL = b'J713 kernel', b'J715 kernel'
INSTALLER_OLD = {name: f'update 4 {name}\nj713ap\n'.encode() for name in MODULE.INSTALLER_FILES}
INSTALLER_NEW = {name: f'update 4 {name}\nj713ap j715ap\n'.encode() for name in MODULE.INSTALLER_FILES}
# Installer members the patch leaves alone; the long name needs a PAX header, as in update 4.
INSTALLER_KEPT = {'install.sh': b'#!/bin/sh\n', 'boot/m1n1.bin': b'stage1',
                  'boot/' + 'framework/' * 12 + 'PythonInterpreter.icns': b'icon'}


def put(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(data)


def installer_patch(old=INSTALLER_OLD, new=INSTALLER_NEW):
    """A unified diff, in installer-j715.patch's layout, from old to new installer files."""
    text = ''
    for name in MODULE.INSTALLER_FILES:
        path = MODULE.PATCH_PATHS.get(name, name)
        first, second = old[name].decode().splitlines(), new[name].decode().splitlines()
        text += f'--- a/{path}\n+++ b/{path}\n@@ -1,2 +1,2 @@\n {first[0]}\n-{first[1]}\n+{second[1]}\n'
    return text.encode()


def installer_tar(path, files):
    with gzip.GzipFile(path, 'wb', mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for directory in ('.', './boot', './gravity_firmware'):
            item = tarfile.TarInfo(directory)
            item.type, item.mode, item.uname = tarfile.DIRTYPE, 0o755, 'kb2uka'
            archive.addfile(item)
        for name, data in files.items():
            item = tarfile.TarInfo('./' + name)
            item.size, item.mode, item.uname, item.mtime = len(data), 0o644, 'kb2uka', 1791562823
            archive.addfile(item, io.BytesIO(data))


def tar_headers(path):
    """Each member's raw header blocks, PAX headers included."""
    data = gzip.open(path).read()
    with tarfile.open(path) as archive:
        return {item.name: data[item.offset:item.offset_data] for item in archive.getmembers()}


def tar_files(path):
    with tarfile.open(path) as archive:
        return {item.name: (archive.extractfile(item).read() if item.isfile() else None, item.mode, item.mtime)
                for item in archive.getmembers()}


class Kit:
    """A J713-shaped kit: real ext4 root, OS ZIP, installer, definition and closed inventory."""

    def __init__(self, base, root_files=None):
        self.base = base
        self.kit = base / 'j713'
        tree = base / 'tree'
        self.root_files = root_files or {MODULE.GPU_MODULE: GPU_OLD, MODULE.GPU_SUMS: GPU_SUMS,
                                         MODULE.GPU_START: GPU_START, MODULE.PLACEMENT: PLACEMENT}
        for path, data in self.root_files.items():
            put(tree / path.lstrip('/'), data)
        for link in MODULE.ROOT_LINKS:
            (tree / link.lstrip('/')).parent.mkdir(parents=True, exist_ok=True)
            os.symlink('/usr/lib/systemd/system/' + link.rsplit('/', 1)[1], tree / link.lstrip('/'))
        for path, source in OWNER_DROPINS.items():
            put(tree / path.lstrip('/'), source.read_bytes())
        put(tree / AUDIO_GATE.lstrip('/'), AUDIO_SOURCE.read_bytes())
        put(tree / 'etc/hostname', b'omarchy\n')
        self.root = base / 'root.ext4'
        subprocess.run(['mkfs.ext4', '-q', '-F', '-E', 'root_owner=0:0', '-d', str(tree), str(self.root), '8M'],
                       check=True, capture_output=True)
        self.root_sha = digest(self.root.read_bytes())
        self.zip = self.kit / 'os' / MODULE.OLD_PACKAGE
        self.zip.parent.mkdir(parents=True)
        with zipfile.ZipFile(self.zip, 'x', zipfile.ZIP_DEFLATED) as archive:
            archive.write(self.root, 'root.ext4')
            archive.writestr(MODULE.OLD_DTB[0], OLD_DTB)
            archive.writestr('boot/initramfs.cpio.gz', BOOT['boot/initramfs.cpio.gz'])
            archive.writestr(MODULE.OLD_KERNEL[0], OLD_KERNEL)
            archive.writestr('boot/loader.bin', BOOT['boot/loader.bin'])
        boot = {'bootargs': 'console=tty0', 'dtb': {'file': MODULE.OLD_DTB[0], 'sha256': digest(OLD_DTB)},
                'kernel': {'file': MODULE.OLD_KERNEL[0], 'sha256': digest(OLD_KERNEL)},
                'initramfs': {'file': 'boot/initramfs.cpio.gz', 'sha256': digest(BOOT['boot/initramfs.cpio.gz'])},
                'loader': {'file': 'boot/loader.bin', 'sha256': digest(BOOT['boot/loader.bin'])}}
        self.definition = {'os_list': [{'name': 'Omarchy (experimental J713)', 'package': MODULE.OLD_PACKAGE,
                                        'expert': True, 'supported_fw': ['26.5.1'], 'm4_boot': boot,
                                        'partitions': [{'name': 'Root', 'image': 'root.ext4'}]}]}
        put(self.kit / 'installer_data.json', json.dumps(self.definition).encode())
        put(self.kit / 'IMAGE.json', json.dumps({
            'private_only': False, 'release_version': '0.1.0-alpha.1', 'kernel_native_startup_qualified': True,
            'boot': {name: boot[name] for name in ('dtb', 'kernel')}, 'root': {'sha256': self.root_sha},
            'gpu_module': {'file': 'opt/m4-gpu/asahi_m4.ko', 'issues': [57, 118], 'sha256': digest(GPU_OLD)},
        }).encode())
        put(self.kit / 'Install.command', b'#!/bin/bash\necho J713\n', 0o755)
        put(self.kit / 'START-HERE.md', b'# J713\n')
        put(self.kit / 'KNOWN-LIMITATIONS.md', b'# J713 limits\n')
        put(self.kit / 'UPDATES.md', b'# J713 updates\n')
        put(self.kit / 'notices/LICENSE.md', b'license\n')
        put(self.kit / 'source/build.sh', b'#!/bin/sh\n', 0o755)
        installer_tar(self.kit / 'installer-macos.tar.gz', {**INSTALLER_KEPT, **INSTALLER_OLD})
        self.patch = base / 'installer-j715.patch'
        self.patch.write_bytes(installer_patch())
        self.seal()

    def seal(self):
        files = {}
        for path in sorted(self.kit.rglob('*')):
            name = path.relative_to(self.kit).as_posix()
            if path.is_file() and name not in ('KIT.json', 'SHA256SUMS'):
                files[name] = {'bytes': path.stat().st_size, 'sha256': digest(path.read_bytes())}
        meta = {'private_only': False, 'release_version': '0.1.0-alpha.1'}
        (self.kit / 'KIT.json').unlink(missing_ok=True)
        put(self.kit / 'KIT.json', json.dumps(dict(meta, files=files)).encode())
        listed = dict(files, **{'KIT.json': {'sha256': digest((self.kit / 'KIT.json').read_bytes())}})
        (self.kit / 'SHA256SUMS').unlink(missing_ok=True)
        put(self.kit / 'SHA256SUMS', ''.join(f"{listed[n]['sha256']}  {n}\n" for n in sorted(listed)).encode())

    def pins(self):
        """Patch the module's real update 4 pins to this miniature kit's values."""
        return [patch.object(MODULE, 'KIT_SHA256', digest((self.kit / 'KIT.json').read_bytes())),
                patch.object(MODULE, 'SUMS_SHA256', digest((self.kit / 'SHA256SUMS').read_bytes())),
                patch.object(MODULE, 'OLD_ROOT_SHA256', self.root_sha),
                patch.object(MODULE, 'OLD_DTB', (MODULE.OLD_DTB[0], digest(OLD_DTB))),
                patch.object(MODULE, 'OLD_KERNEL', (MODULE.OLD_KERNEL[0], digest(OLD_KERNEL))),
                patch.object(MODULE, 'KEPT_BOOT', {name: digest(data) for name, data in BOOT.items()}),
                patch.object(MODULE, 'DTB', (MODULE.DTB[0], digest(NEW_DTB))),
                patch.object(MODULE, 'KERNEL', (MODULE.KERNEL[0], digest(NEW_KERNEL))),
                patch.object(MODULE, 'INSTALLER_FILES', {name: (digest(INSTALLER_OLD[name]),
                                                                digest(INSTALLER_NEW[name]))
                                                         for name in MODULE.INSTALLER_FILES}),
                patch.object(MODULE, 'INSTALLER_PATCH', self.patch),
                patch.object(MODULE, 'GPU_OLD', digest(GPU_OLD)),
                patch.object(MODULE, 'GPU_NEW', digest(GPU_NEW)),
                patch.object(MODULE, 'GPU_SUMS_OLD', digest(self.root_files[MODULE.GPU_SUMS])),
                patch.object(MODULE, 'GPU_START_OLD', digest(self.root_files[MODULE.GPU_START])),
                patch.object(MODULE, 'PLACEMENT_OLD', digest(self.root_files[MODULE.PLACEMENT]))]


def read(image, path):
    return subprocess.run(['debugfs', '-R', 'cat ' + path, str(image)], capture_output=True, check=True).stdout


def stat(image, path):
    return subprocess.run(['debugfs', '-R', 'stat ' + path, str(image)], capture_output=True, text=True).stdout


@unittest.skipUnless(TOOLS, 'mkfs.ext4, debugfs, e2fsck and patch are required')
class Derive(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.fixture = Kit(self.base)
        self.dtb = self.base / 'j715.dtb'
        self.dtb.write_bytes(NEW_DTB)
        self.kernel = self.base / 'Image.gz'
        self.kernel.write_bytes(NEW_KERNEL)
        self.gpu = self.base / 'asahi_m4.ko'
        self.gpu.write_bytes(GPU_NEW)
        self.output = self.base / 'j715'

    def derive(self, output=None):
        pins = self.fixture.pins()
        for pin in pins:
            pin.start()
        try:
            return MODULE.derive(self.fixture.kit, self.dtb, self.kernel, self.gpu, output or self.output, REVISION)
        finally:
            for pin in reversed(pins):
                pin.stop()

    def refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            self.derive()
        self.assertFalse(self.output.exists())
        self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith('.j715-kit-')], [])

    def test_the_tester_kit_is_complete_and_packageable(self):
        receipt = self.derive()
        files, archive = PACKAGER.inventory(self.output, receipt['kit_sha256'], receipt['checksums_sha256'],
                                            'v0.1.0-alpha.1-j715-test.1')
        self.assertEqual(archive, 'os/' + MODULE.PACKAGE)
        definition = json.loads((self.output / 'installer_data.json').read_bytes())['os_list'][0]
        old = self.fixture.definition['os_list'][0]
        self.assertEqual(definition['package'], MODULE.PACKAGE)
        self.assertEqual(definition['name'], 'Omarchy M4 Native 0.1.0-alpha.1 (J715 tester)')
        self.assertEqual(definition['supported_fw'], ['26.5.1'])
        self.assertEqual(definition['m4_boot']['dtb'], {'file': 'boot/j715.dtb', 'sha256': digest(NEW_DTB)})
        self.assertEqual(definition['m4_boot']['kernel'], {'file': 'boot/Image.gz', 'sha256': digest(NEW_KERNEL)})
        for part in ('initramfs', 'loader', 'bootargs'):
            self.assertEqual(definition['m4_boot'][part], old['m4_boot'][part])
        self.assertEqual(definition['partitions'], old['partitions'])
        self.assertEqual(definition['expert'], True)
        for name, mode in (('Install.command', 0o755), ('START-HERE.md', 0o644),
                           ('KNOWN-LIMITATIONS.md', 0o644), ('UPDATES.md', 0o644)):
            self.assertEqual((self.output / name).read_bytes(), (ROOT / 'packaging/j715-test' / name).read_bytes())
            self.assertEqual(os.stat(self.output / name).st_mode & 0o777, mode, name)
        self.assertEqual((self.output / 'notices/LICENSE.md').read_bytes(), b'license\n')
        for name, mode in (('notices/LICENSE.md', 0o644), ('source/build.sh', 0o755)):
            self.assertEqual(os.stat(self.output / name).st_mode & 0o7777, mode, name)
        for name, source in MODULE.SOURCES.items():
            self.assertEqual((self.output / name).read_bytes(), (ROOT / source).read_bytes(), name)
            self.assertEqual(os.stat(self.output / name).st_mode & 0o7777, 0o644, name)
        image = json.loads((self.output / 'IMAGE.json').read_bytes())
        self.assertEqual(image['root']['sha256'], receipt['root_sha256'])
        self.assertEqual(image['boot']['dtb'], definition['m4_boot']['dtb'])
        self.assertEqual(image['boot']['kernel'], definition['m4_boot']['kernel'])
        self.assertIs(image['kernel_native_startup_qualified'], False)
        self.assertEqual(image['gpu_module'], {'file': 'opt/m4-gpu/asahi_m4.ko', 'issues': [57, 118, 142],
                                               'sha256': digest(GPU_NEW), 'replaces_sha256': digest(GPU_OLD)})
        self.assertEqual(image['j715_test']['root_from_sha256'], self.fixture.root_sha)
        kit_meta = json.loads((self.output / 'KIT.json').read_bytes())
        self.assertEqual(kit_meta['j715_test']['derivation_revision'], REVISION)
        self.assertEqual(kit_meta['j715_test']['model'], 'Mac16,13')
        with zipfile.ZipFile(self.output / 'os' / MODULE.PACKAGE) as archive:
            self.assertEqual(archive.namelist(), ['root.ext4', 'boot/j715.dtb', 'boot/initramfs.cpio.gz',
                                                  'boot/Image.gz', 'boot/loader.bin'])
            self.assertEqual(archive.read('boot/j715.dtb'), NEW_DTB)
            self.assertEqual(archive.read('boot/Image.gz'), NEW_KERNEL)
            for name, data in BOOT.items():
                self.assertEqual(archive.read(name), data)
            root = self.base / 'derived.ext4'
            root.write_bytes(archive.read('root.ext4'))
        self.assertEqual(digest(root.read_bytes()), receipt['root_sha256'])
        self.assertEqual(read(root, MODULE.GPU_MODULE), GPU_NEW)
        self.assertEqual(read(root, MODULE.GPU_SUMS), GPU_SUMS.replace(digest(GPU_OLD).encode(),
                                                                      digest(GPU_NEW).encode()))
        self.assertEqual(read(root, MODULE.GPU_START), GPU_START.replace(*MODULE.GPU_START_LINE))
        self.assertEqual(digest(read(root, MODULE.PLACEMENT)), MODULE.PLACEMENT_SOURCE[3])
        self.assertEqual(digest((self.output / 'source/j715/g16.rs').read_bytes()), MODULE.G16_SOURCE[3])
        for path, mode in ((MODULE.GPU_MODULE, '0644'), (MODULE.GPU_SUMS, '0664'), (MODULE.GPU_START, '0755'),
                           (MODULE.PLACEMENT, '0644')):
            self.assertRegex(stat(root, path), r'Mode:\s+' + mode + r'\b', path)
        for path in (*MODULE.ROOT_LINKS, *MODULE.ROOT_DROPINS):
            self.assertNotIn('Inode:', stat(root, path))
        self.assertEqual(read(root, AUDIO_GATE), AUDIO_SOURCE.read_bytes())
        self.assertEqual(read(root, '/etc/hostname'), b'omarchy\n')
        self.assertEqual(subprocess.run(['e2fsck', '-fn', str(root)], capture_output=True).returncode, 0)
        old_tar, new_tar = tar_files(self.fixture.kit / 'installer-macos.tar.gz'), \
            tar_files(self.output / 'installer-macos.tar.gz')
        self.assertEqual(list(new_tar), list(old_tar))
        for name, (data, mode, mtime) in new_tar.items():
            short = name.removeprefix('./')
            expected = INSTALLER_NEW[short] if short in INSTALLER_NEW else old_tar[name][0]
            self.assertEqual((data, mode, mtime), (expected, old_tar[name][1], old_tar[name][2]), name)
        old_headers = tar_headers(self.fixture.kit / 'installer-macos.tar.gz')
        new_headers = tar_headers(self.output / 'installer-macos.tar.gz')
        self.assertEqual([name for name in old_headers if old_headers[name] != new_headers[name]],
                         ['./' + name for name in INSTALLER_OLD])

    def test_the_installer_archive_is_reproducible(self):
        # The root is not: debugfs stamps the files it writes with the current time.
        first = self.derive()
        second = self.derive(self.base / 'again')
        self.assertEqual(first['installer_sha256'], second['installer_sha256'])

    def test_the_root_changes_are_exactly_the_reviewed_ones(self):
        self.assertEqual(list(MODULE.ROOT_LINKS), [
            '/etc/systemd/system/multi-user.target.wants/m4-j713-audio-register.service',
            '/etc/systemd/system/multi-user.target.wants/speakersafetyd-j713.service',
            '/etc/systemd/system/multi-user.target.wants/m4-video.service',
            '/etc/systemd/system/multi-user.target.wants/j713-owner-assets.service',
            '/etc/systemd/system/timers.target.wants/m4-channel-connect.timer'])
        self.assertEqual(sorted(MODULE.ROOT_DROPINS), sorted(OWNER_DROPINS))
        for path, source in OWNER_DROPINS.items():
            self.assertEqual(MODULE.ROOT_DROPINS[path], digest(source.read_bytes()), path)
        self.assertEqual(MODULE.GPU_START_LINE[1],
                         b'grep -zFxq -e apple,j713 -e apple,j715 /sys/firmware/devicetree/base/compatible\n')
        self.assertEqual(MODULE.FIRMWARE, '26.5.1')
        self.assertEqual(sorted(MODULE.REPLACED_DOCS),
                         ['Install.command', 'KNOWN-LIMITATIONS.md', 'START-HERE.md', 'UPDATES.md'])

    def test_a_changed_kit_file_is_refused(self):
        (self.fixture.kit / 'START-HERE.md').unlink()
        put(self.fixture.kit / 'START-HERE.md', b'# changed\n')
        self.refused('Kit file changed: START-HERE.md')

    def test_an_unlisted_kit_file_is_refused(self):
        put(self.fixture.kit / 'extra.txt', b'x')
        self.refused('unlisted or missing files')

    def test_a_different_devicetree_is_refused(self):
        self.dtb.write_bytes(b'other')
        self.refused('Unexpected J715 devicetree')

    def test_a_different_kernel_is_refused(self):
        self.kernel.write_bytes(b'other')
        self.refused('Unexpected J715 kernel')

    def test_a_different_gpu_module_is_refused(self):
        self.gpu.write_bytes(b'other')
        self.refused('Unexpected J715 GPU module')

    def test_a_root_whose_gpu_start_lacks_the_board_check_is_refused(self):
        start = b'#!/bin/sh\nexec /usr/bin/true\n'
        (self.base / 'other').mkdir()
        # The derivation reads the kit in self.fixture; refused() still checks self.base for leftovers.
        self.fixture = Kit(self.base / 'other', {MODULE.GPU_MODULE: GPU_OLD, MODULE.GPU_SUMS: GPU_SUMS,
                                                 MODULE.GPU_START: start, MODULE.PLACEMENT: PLACEMENT})
        self.refused('Unexpected m4-gpu-start')

    def test_a_changed_live_gate_is_refused(self):
        for name in ('PLACEMENT_SOURCE', 'G16_SOURCE'):
            path, _, kit_patch, result = getattr(MODULE, name)
            with self.subTest(name=name), patch.object(MODULE, name, (path, digest(b'other'), kit_patch, result)):
                self.refused('Unexpected repository file: ' + path)

    def test_a_kit_patch_that_does_not_give_the_pinned_result_is_refused(self):
        for name in ('PLACEMENT_SOURCE', 'G16_SOURCE'):
            path, base, kit_patch, _ = getattr(MODULE, name)
            with self.subTest(name=name), patch.object(MODULE, name, (path, base, kit_patch, digest(b'other'))):
                self.refused('Unexpected patched file: ' + path)

    def test_a_missing_start_link_is_refused(self):
        with patch.object(MODULE, 'ROOT_LINKS', MODULE.ROOT_LINKS + ('/etc/systemd/system/x.wants/y.service',)):
            self.refused('Missing root start link')

    def test_a_missing_owner_options_drop_in_is_refused(self):
        with patch.dict(MODULE.ROOT_DROPINS, {'/usr/lib/systemd/system/x.d/y.conf': digest(b'[Unit]\n')}):
            self.refused('Unexpected root drop-in')

    def test_a_changed_owner_options_drop_in_is_refused(self):
        for path in OWNER_DROPINS:
            with self.subTest(path=path), patch.dict(MODULE.ROOT_DROPINS, {path: digest(b'other')}):
                self.refused('Unexpected root drop-in: ' + path)

    def test_an_installer_patch_that_needs_fuzz_is_refused(self):
        self.fixture.patch.write_bytes(installer_patch().replace(b'\n update 4 main.py\n', b'\n update 4 other\n'))
        self.refused('Installer patch did not apply exactly')

    def test_an_unexpected_installer_file_is_refused(self):
        with patch.dict(INSTALLER_OLD, {'main.py': b'other\nj713ap\n'}):
            self.refused('Unexpected installer file: main.py')

    def test_an_unexpected_installer_definition_is_refused(self):
        definition = json.loads((self.fixture.kit / 'installer_data.json').read_bytes())
        definition['os_list'][0]['supported_fw'] = ['26.6.2']
        (self.fixture.kit / 'installer_data.json').unlink()
        put(self.fixture.kit / 'installer_data.json', json.dumps(definition).encode())
        self.fixture.seal()
        self.refused('Unexpected installer definition')

    def test_existing_output_is_kept(self):
        self.output.mkdir()
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.derive()


class Sources(unittest.TestCase):
    def test_the_real_pins_are_complete(self):
        for value in (MODULE.KIT_SHA256, MODULE.SUMS_SHA256, MODULE.OLD_ROOT_SHA256, MODULE.OLD_DTB[1],
                      MODULE.OLD_KERNEL[1], MODULE.DTB[1], MODULE.KERNEL[1], MODULE.GPU_OLD, MODULE.GPU_NEW,
                      MODULE.GPU_SUMS_OLD, MODULE.GPU_START_OLD, MODULE.PLACEMENT_OLD,
                      *MODULE.PLACEMENT_SOURCE[1::2], *MODULE.G16_SOURCE[1::2],
                      *MODULE.KEPT_BOOT.values(), *(pin for pins in MODULE.INSTALLER_FILES.values() for pin in pins)):
            self.assertRegex(value, r'^[0-9a-f]{64}$')

    def test_the_kit_sources_exist_and_the_live_gates_still_refuse_the_j715(self):
        for source in MODULE.SOURCES.values():
            self.assertTrue((ROOT / source).is_file(), source)
        for path, base, kit_patch, _ in (MODULE.PLACEMENT_SOURCE, MODULE.G16_SOURCE):
            with self.subTest(path=path):
                self.assertEqual(digest((ROOT / path).read_bytes()), base)
                self.assertNotIn(b'apple,j715', (ROOT / path).read_bytes())
                text = kit_patch.read_text()
                self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), [path])
                self.assertIn('+', ''.join(line[0] for line in text.splitlines() if 'apple,j715' in line))

    def test_the_installer_patch_changes_only_the_pinned_files(self):
        text = (ROOT / 'packaging/j715-test/installer-j715.patch').read_text()
        targets = re.findall(r'^\+\+\+ b/(\S+)', text, re.M)
        self.assertEqual(sorted(targets),
                         sorted(MODULE.PATCH_PATHS.get(name, name) for name in MODULE.INSTALLER_FILES))
        self.assertIn('j715ap', text)

    def test_the_kernel_patch_admits_the_j715(self):
        text = (ROOT / 'patches-kernel/j715-input/dockchannel-hid-accept-j715.patch').read_text()
        self.assertEqual(re.findall(r'^\+\+\+ b/(\S+)', text, re.M), ['drivers/hid/dockchannel-hid/dockchannel-hid.c'])
        self.assertIn('+', ''.join(line for line in text.splitlines() if 'apple,j715' in line))


class InstallCommand(unittest.TestCase):
    SCRIPT = ROOT / 'packaging/j715-test/Install.command'

    def test_the_script_parses(self):
        subprocess.run(['bash', '-n', str(self.SCRIPT)], check=True)

    def test_it_accepts_only_the_15_inch_air(self):
        gates = [line for line in self.SCRIPT.read_text().splitlines() if 'sysctl -n hw.model' in line]
        self.assertEqual(len(gates), 1)
        gate = 'sysctl() { printf "%s\\n" "$MODEL"; }\n' + gates[0].replace('/usr/sbin/sysctl', 'sysctl')
        for model, accepted in (('Mac16,13', True), ('Mac16,12', False), ('Mac16,10', False), ('', False)):
            with self.subTest(model=model):
                result = subprocess.run(['bash', '-c', gate], capture_output=True, text=True,
                                        env={'MODEL': model, 'PATH': os.environ.get('PATH', '/usr/bin:/bin')})
                self.assertEqual(result.returncode == 0, accepted, result.stderr)

    def test_install_needs_the_tester_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            kit = Path(temp)
            shutil.copy(self.SCRIPT, kit / 'Install.command')
            result = subprocess.run(['bash', str(kit / 'Install.command'), '--install'],
                                    capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('add --tester', result.stderr)

    def test_only_typing_test_with_the_tester_flag_continues(self):
        lines = self.SCRIPT.read_text().splitlines()
        start = next(i for i, line in enumerate(lines) if "read -r -p 'Type TEST" in line)
        guard = '\n'.join(lines[start:start + 2]) + '\necho REACHED-INSTALLER\n'
        # The confirmation must come before the installer is unpacked or started.
        later = [i for i, line in enumerate(lines) if line.startswith(('tar -xzf', 'exec ', 'work='))]
        self.assertEqual(len(later), 3)
        self.assertTrue(all(start + 1 < i for i in later))
        self.assertEqual(lines[start + 1], '[[ "$answer" == TEST && "$tester" == yes ]] || exit 0')
        for tester, answer, reached in (('yes', 'TEST', True), ('yes', 'test', False), ('yes', 'y', False),
                                        ('yes', '', False), ('no', 'TEST', False)):
            with self.subTest(tester=tester, answer=answer):
                result = subprocess.run(['bash', '-c', f'tester={tester}\n' + guard], input=answer + '\n',
                                        capture_output=True, text=True)
                self.assertEqual('REACHED-INSTALLER' in result.stdout, reached)

    def test_it_names_the_air_firmware_ipsw(self):
        text = self.SCRIPT.read_text()
        self.assertIn('UniversalMac_26.5.1_25F80_Restore.ipsw', text)
        self.assertNotIn('26.6.2', text)
        self.assertIn('M4_OWNER_WIDEVINE=no', text)


if __name__ == '__main__':
    unittest.main()
