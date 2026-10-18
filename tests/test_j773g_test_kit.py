# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The M4 Mac mini tester kit: derived from a miniature J713 kit with a real ext4 root."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
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


MODULE = load('j773g_test_kit', 'tools/derive-j773g-test-kit.py')
PACKAGER = load('j713_download', 'tools/package-j713-download.py')
TOOLS = all(shutil.which(tool) for tool in ('mkfs.ext4', 'debugfs', 'e2fsck'))
REVISION = 'a' * 40
SDDM_FILES = {path: f'J713 GPU file {index}\n'.encode() for index, path in enumerate(MODULE.ROOT_FILES)}
BOOT = {name: f'{name} bytes'.encode() for name in MODULE.KEPT_BOOT}
OLD_DTB, NEW_DTB = b'J713 devicetree', b'J773g devicetree'
digest = lambda data: hashlib.sha256(data).hexdigest()


def put(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, mode)


class Kit:
    """A J713-shaped kit: real ext4 root, OS ZIP, installer definition and closed inventory."""

    def __init__(self, base):
        self.base = base
        self.kit = base / 'j713'
        tree = base / 'tree'
        for path, data in SDDM_FILES.items():
            put(tree / path.lstrip('/'), data)
        for link in MODULE.ROOT_LINKS:
            (tree / link.lstrip('/')).parent.mkdir(parents=True, exist_ok=True)
            os.symlink('/usr/lib/systemd/system/' + link.rsplit('/', 1)[1], tree / link.lstrip('/'))
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
            for name, data in BOOT.items():
                archive.writestr(name, data)
        boot = {'bootargs': 'console=tty0', 'dtb': {'file': MODULE.OLD_DTB[0], 'sha256': digest(OLD_DTB)}}
        for part, name in (('initramfs', 'boot/initramfs.cpio.gz'), ('kernel', 'boot/Image.gz'),
                           ('loader', 'boot/loader.bin')):
            boot[part] = {'file': name, 'sha256': digest(BOOT[name])}
        self.definition = {'os_list': [{'name': 'Omarchy (experimental J713)', 'package': MODULE.OLD_PACKAGE,
                                        'expert': True, 'supported_fw': ['26.5.1'], 'm4_boot': boot,
                                        'partitions': [{'name': 'Root', 'image': 'root.ext4'}]}]}
        put(self.kit / 'installer_data.json', json.dumps(self.definition).encode())
        put(self.kit / 'IMAGE.json', json.dumps({'private_only': False, 'release_version': '0.1.0-alpha.1',
                                                 'boot': {'dtb': boot['dtb']},
                                                 'root': {'sha256': self.root_sha}}).encode())
        put(self.kit / 'Install.command', b'#!/bin/bash\necho J713\n', 0o755)
        put(self.kit / 'START-HERE.md', b'# J713\n')
        put(self.kit / 'KNOWN-LIMITATIONS.md', b'# J713 limits\n')
        put(self.kit / 'UPDATES.md', b'# J713 updates\n')
        put(self.kit / 'notices/LICENSE.md', b'license\n')
        put(self.kit / 'installer-macos.tar.gz', b'installer')
        self.seal()

    def seal(self, kit_meta=None):
        files = {}
        for path in sorted(self.kit.rglob('*')):
            name = path.relative_to(self.kit).as_posix()
            if path.is_file() and name not in ('KIT.json', 'SHA256SUMS'):
                files[name] = {'bytes': path.stat().st_size, 'sha256': digest(path.read_bytes())}
        meta = kit_meta or {'private_only': False, 'release_version': '0.1.0-alpha.1'}
        (self.kit / 'KIT.json').unlink(missing_ok=True)
        put(self.kit / 'KIT.json', json.dumps(dict(meta, files=files)).encode())
        listed = dict(files, **{'KIT.json': {'sha256': digest((self.kit / 'KIT.json').read_bytes())}})
        (self.kit / 'SHA256SUMS').unlink(missing_ok=True)
        put(self.kit / 'SHA256SUMS', ''.join(f"{listed[n]['sha256']}  {n}\n" for n in sorted(listed)).encode())

    def pins(self):
        """Patch the module's real update 4 pins to this miniature kit's values."""
        root_files = {path: (digest(SDDM_FILES[path]), name) for path, (_, name) in MODULE.ROOT_FILES.items()}
        return [patch.object(MODULE, 'KIT_SHA256', digest((self.kit / 'KIT.json').read_bytes())),
                patch.object(MODULE, 'SUMS_SHA256', digest((self.kit / 'SHA256SUMS').read_bytes())),
                patch.object(MODULE, 'OLD_ROOT_SHA256', self.root_sha),
                patch.object(MODULE, 'OLD_DTB', (MODULE.OLD_DTB[0], digest(OLD_DTB))),
                patch.object(MODULE, 'KEPT_BOOT', {name: digest(data) for name, data in BOOT.items()}),
                patch.object(MODULE, 'DTB', (MODULE.DTB[0], digest(NEW_DTB))),
                patch.object(MODULE, 'ROOT_FILES', root_files)]


def read(image, path):
    return subprocess.run(['debugfs', '-R', 'cat ' + path, str(image)], capture_output=True, check=True).stdout


@unittest.skipUnless(TOOLS, 'mkfs.ext4, debugfs and e2fsck are required')
class Derive(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.fixture = Kit(self.base)
        self.dtb = self.base / 'j773g.dtb'
        self.dtb.write_bytes(NEW_DTB)
        self.output = self.base / 'mini'

    def derive(self, kit=None):
        pins = self.fixture.pins()
        for pin in pins:
            pin.start()
        try:
            return MODULE.derive(kit or self.fixture.kit, self.dtb, self.output, REVISION)
        finally:
            for pin in reversed(pins):
                pin.stop()

    def refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            self.derive()
        self.assertFalse(self.output.exists())
        self.assertEqual([p.name for p in self.base.iterdir() if p.name.startswith('.j773g-kit-')], [])

    def test_the_tester_kit_is_complete_and_packageable(self):
        receipt = self.derive()
        files, archive = PACKAGER.inventory(self.output, receipt['kit_sha256'], receipt['checksums_sha256'],
                                            'v0.1.0-alpha.1-j773g-test.1')
        self.assertEqual(archive, 'os/' + MODULE.PACKAGE)
        definition = json.loads((self.output / 'installer_data.json').read_bytes())['os_list'][0]
        old = self.fixture.definition['os_list'][0]
        self.assertEqual(definition['package'], MODULE.PACKAGE)
        self.assertEqual(definition['supported_fw'], ['26.6.2'])
        self.assertEqual(definition['m4_boot']['dtb'], {'file': 'boot/j773g.dtb', 'sha256': digest(NEW_DTB)})
        for part in ('initramfs', 'kernel', 'loader', 'bootargs'):
            self.assertEqual(definition['m4_boot'][part], old['m4_boot'][part])
        self.assertEqual(definition['partitions'], old['partitions'])
        self.assertEqual(definition['expert'], True)
        self.assertEqual(definition['name'], 'Omarchy M4 Native 0.1.0-alpha.1 (J773g tester)')
        for name in ('Install.command', 'START-HERE.md', 'KNOWN-LIMITATIONS.md', 'UPDATES.md'):
            self.assertEqual((self.output / name).read_bytes(), (ROOT / 'packaging/j773g-test' / name).read_bytes())
        self.assertEqual(os.stat(self.output / 'Install.command').st_mode & 0o777, 0o755)
        self.assertEqual((self.output / 'notices/LICENSE.md').read_bytes(), b'license\n')
        image = json.loads((self.output / 'IMAGE.json').read_bytes())
        self.assertEqual(image['root']['sha256'], receipt['root_sha256'])
        self.assertEqual(image['j773g_test']['root_from_sha256'], self.fixture.root_sha)
        self.assertEqual(json.loads((self.output / 'KIT.json').read_bytes())['j773g_test']['derivation_revision'],
                         REVISION)
        with zipfile.ZipFile(self.output / 'os' / MODULE.PACKAGE) as archive:
            self.assertEqual(archive.namelist(), ['root.ext4', 'boot/j773g.dtb', *BOOT])
            self.assertEqual(archive.read('boot/j773g.dtb'), NEW_DTB)
            for name, data in BOOT.items():
                self.assertEqual(archive.read(name), data)
            root = self.base / 'derived.ext4'
            root.write_bytes(archive.read('root.ext4'))
        self.assertEqual(digest(root.read_bytes()), receipt['root_sha256'])
        for path, (_, name) in MODULE.ROOT_FILES.items():
            self.assertEqual(read(root, path), (ROOT / 'packaging/j773g-test' / name).read_bytes())
        for link in MODULE.ROOT_LINKS:
            stat = subprocess.run(['debugfs', '-R', 'stat ' + link, str(root)], capture_output=True, text=True)
            self.assertNotIn('Inode:', stat.stdout)
        self.assertEqual(read(root, '/etc/hostname'), b'omarchy\n')
        self.assertEqual(subprocess.run(['e2fsck', '-fn', str(root)], capture_output=True).returncode, 0)

    def test_the_root_changes_are_exactly_the_reviewed_ones(self):
        self.assertEqual(sorted(MODULE.ROOT_FILES), ['/etc/sddm.conf.d/99-m4.conf',
                                                     '/etc/systemd/system/sddm.service.d/90-m4-staging.conf'])
        self.assertEqual(list(MODULE.ROOT_LINKS), [
            '/etc/systemd/system/multi-user.target.wants/m4-j713-audio-register.service',
            '/etc/systemd/system/multi-user.target.wants/speakersafetyd-j713.service',
            '/etc/systemd/system/multi-user.target.wants/m4-video.service',
            '/etc/systemd/system/multi-user.target.wants/j713-owner-assets.service',
            '/etc/systemd/system/timers.target.wants/m4-channel-connect.timer'])
        self.assertEqual(MODULE.FIRMWARE, '26.6.2')
        self.assertEqual(sorted(MODULE.REPLACED_DOCS),
                         ['Install.command', 'KNOWN-LIMITATIONS.md', 'START-HERE.md', 'UPDATES.md'])

    def test_the_mini_root_files_drop_the_gpu_requirement_and_the_custom_mesa(self):
        for _, name in MODULE.ROOT_FILES.values():
            text = (ROOT / 'packaging/j773g-test' / name).read_text()
            self.assertNotIn('m4-gpu', text)
            self.assertNotIn('/opt/m4-mesa', text)
            self.assertIn('LIBSEAT_BACKEND=seatd', text)
            self.assertIn('AQ_M4_SIMPLEDRM_GBM=1', text)

    def test_a_changed_kit_file_is_refused(self):
        put(self.fixture.kit / 'START-HERE.md', b'# changed\n')
        self.refused('Kit file changed: START-HERE.md')

    def test_an_unlisted_kit_file_is_refused(self):
        put(self.fixture.kit / 'extra.txt', b'x')
        self.refused('unlisted or missing files')

    def test_a_different_root_file_is_refused(self):
        with patch.dict(SDDM_FILES, {next(iter(MODULE.ROOT_FILES)): b'other\n'}):
            self.refused('Unexpected root file')

    def test_a_missing_start_link_is_refused(self):
        with patch.object(MODULE, 'ROOT_LINKS', MODULE.ROOT_LINKS + ('/etc/systemd/system/x.wants/y.service',)):
            self.refused('Missing root start link')

    def test_a_different_devicetree_is_refused(self):
        self.dtb.write_bytes(b'other')
        self.refused('Unexpected J773g devicetree')

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


class InstallCommand(unittest.TestCase):
    SCRIPT = ROOT / 'packaging/j773g-test/Install.command'

    def test_the_script_parses(self):
        subprocess.run(['bash', '-n', str(self.SCRIPT)], check=True)

    def test_it_accepts_only_the_mini(self):
        gates = [line for line in self.SCRIPT.read_text().splitlines() if 'sysctl -n hw.model' in line]
        self.assertEqual(len(gates), 1)
        gate = 'sysctl() { printf "%s\\n" "$MODEL"; }\n' + gates[0].replace('/usr/sbin/sysctl', 'sysctl')
        for model, accepted in (('Mac16,10', True), ('Mac16,12', False), ('Mac16,11', False), ('', False)):
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
        for tester, answer, reached in (('yes', 'TEST', True), ('yes', 'test', False), ('yes', 'y', False),
                                        ('yes', '', False), ('no', 'TEST', False)):
            with self.subTest(tester=tester, answer=answer):
                result = subprocess.run(['bash', '-c', f'tester={tester}\n' + guard], input=answer + '\n',
                                        capture_output=True, text=True)
                self.assertEqual('REACHED-INSTALLER' in result.stdout, reached)

    def test_it_names_the_mini_firmware_ipsw(self):
        text = self.SCRIPT.read_text()
        self.assertIn('UniversalMac_26.6.2_25G83_Restore.ipsw', text)
        self.assertNotIn('26.5.1', text)
        self.assertIn('M4_OWNER_WIDEVINE=no', text)


if __name__ == '__main__':
    unittest.main()
