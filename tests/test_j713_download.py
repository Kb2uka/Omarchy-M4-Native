# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('download_package', ROOT / 'tools/package-j713-download.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def put(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(0o644)


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.delivery = self.root / 'source kit'
        self.assets = self.root / 'assets'
        self.output = self.root / 'download space'
        self.archive = 'os/unchanged-installer.zip'
        self.files = {
            self.archive: b'0123456789' * 4,
            'Install.command': b'#!/bin/bash\necho MUST_NOT_RUN\nexit 99\n',
            'START-HERE.md': b'Fixture only.\n',
            'notices/LICENSE': b'Fixture notice.\n',
            'IMAGE.json': b'{"private_only":false,"release_version":"0.1.0-alpha.1"}\n',
            'installer_data.json': b'{"os_list":[{"package":"unchanged-installer.zip"}]}\n',
        }
        for name, data in self.files.items():
            put(self.delivery / name, data)
        self.seal()

    def seal(self, private=False):
        mapping = {name: {'sha256': sha(data), 'bytes': len(data)} for name, data in self.files.items()}
        kit = json.dumps({'private_only': private, 'release_version': '0.1.0-alpha.1', 'files': mapping}).encode()
        put(self.delivery / 'KIT.json', kit)
        sums = ''.join(sha(data) + '  ' + name + '\n'
                       for name, data in sorted({**self.files, 'KIT.json': kit}.items())).encode()
        put(self.delivery / 'SHA256SUMS', sums)
        self.kit_pin, self.sums_pin = sha(kit), sha(sums)

    def package(self, **changes):
        args = dict(delivery=self.delivery, kit_pin=self.kit_pin, sums_pin=self.sums_pin,
                    tag='v0.1.0-alpha.1', output=self.assets, part_bytes=16)
        args.update(changes)
        return packager.package(**args)

    def run_download(self):
        manifest = self.assets / packager.MANIFEST
        return subprocess.run(['bash', str(ROOT / 'tools/download-j713.command'),
            '--asset-directory', str(self.assets), '--manifest-sha256', sha(manifest.read_bytes()),
            '--output', str(self.output)], capture_output=True, text=True)

    def rows(self):
        return [line.split('\t') for line in (self.assets / packager.MANIFEST).read_text().splitlines()]

    def write_rows(self, rows):
        (self.assets / packager.MANIFEST).write_text(''.join('\t'.join(r) + '\n' for r in rows))

    def refused(self, message):
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(message, result.stderr)
        self.assertFalse(self.output.exists(), 'Owned partial output should be removed')

    def test_actual_shell_reconstructs_exact_files_without_running_installer(self):
        result = self.package()
        self.assertEqual(result['part_count'], 3)
        run = self.run_download()
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertNotIn('MUST_NOT_RUN', run.stdout)
        self.assertIn('Public HTTPS availability was not checked', run.stdout)
        actual = {p.relative_to(self.output / 'installer').as_posix(): p.read_bytes()
                  for p in (self.output / 'installer').rglob('*') if p.is_file()}
        expected = {p.relative_to(self.delivery).as_posix(): p.read_bytes()
                    for p in self.delivery.rglob('*') if p.is_file()}
        self.assertEqual(actual, expected)
        self.assertFalse((self.output / '.partial').exists())

    def test_private_delivery_cannot_be_publicized_by_transport(self):
        self.seal(private=True)
        with self.assertRaisesRegex(ValueError, 'Private or unclassified'):
            self.package()
        self.assertFalse(self.assets.exists())

    def test_private_image_refused_even_when_kit_says_public(self):
        self.files['IMAGE.json'] = b'{"private_only":true,"release_version":"0.1.0-alpha.1"}'
        put(self.delivery / 'IMAGE.json', self.files['IMAGE.json']); self.seal()
        with self.assertRaisesRegex(ValueError, 'Private or unclassified'):
            self.package()

    def test_exact_metadata_pin_required(self):
        with self.assertRaisesRegex(ValueError, 'pin mismatch'):
            self.package(kit_pin='0' * 64)

    def test_input_symlinks_and_extra_members_refused(self):
        (self.delivery / 'linked').symlink_to(self.delivery / 'KIT.json')
        with self.assertRaisesRegex(ValueError, 'Symlink'):
            self.package()
        (self.delivery / 'linked').unlink()
        put(self.delivery / 'extra', b'extra')
        with self.assertRaisesRegex(ValueError, 'inventory'):
            self.package()

    def test_missing_part_refused(self):
        self.package()
        part = next(self.assets.glob('*.part002')); part.unlink()
        self.refused('Missing regular asset')

    def test_corrupt_part_refused(self):
        self.package()
        part = next(self.assets.glob('*.part001')); part.write_bytes(b'x' * 16)
        self.refused('Asset hash/size mismatch')

    def test_oversized_part_refused(self):
        self.package()
        part = next(self.assets.glob('*.part001')); part.write_bytes(part.read_bytes() + b'x')
        self.refused('Oversized asset')

    def test_out_of_order_part_rows_refused(self):
        self.package(); rows = self.rows(); rows[4], rows[5] = rows[5], rows[4]
        self.write_rows(rows); self.refused('Malformed transport manifest')

    def test_final_archive_hash_is_independently_required(self):
        self.package(); rows = self.rows(); rows[2][3] = '0' * 64
        for row in rows:
            if row[0] == 'file' and row[1] == self.archive:
                row[4] = '0' * 64
        self.write_rows(rows); self.refused('Reassembled OS ZIP mismatch')

    def test_unsafe_manifest_path_refused(self):
        self.package(); rows = self.rows()
        for row in rows:
            if row[0] == 'file' and row[1] == 'notices/LICENSE':
                row[1] = '../escape'
        self.write_rows(rows); self.refused('Malformed transport manifest')
        self.assertFalse((self.root / 'escape').exists())

    def test_link_asset_refused(self):
        self.package(); part = next(self.assets.glob('*.part001'))
        data = part.read_bytes(); part.unlink(); put(self.root / 'outside', data)
        part.symlink_to(self.root / 'outside')
        self.refused('Missing regular asset')

    def test_output_conflict_and_symlink_ancestor_preserved(self):
        self.package(); self.output.mkdir(); put(self.output / 'foreign', b'keep')
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.output / 'foreign').read_bytes(), b'keep')
        (self.root / 'alias').symlink_to(self.output, target_is_directory=True)
        self.output = self.root / 'alias' / 'new'
        result = self.run_download()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Symlink path refused', result.stderr)
        self.assertFalse(self.output.exists())

    def test_regular_member_type_required_before_extract(self):
        self.package(); rows = self.rows()
        companion = self.assets / rows[3][1]
        with tarfile.open(companion, 'w', format=tarfile.USTAR_FORMAT) as tar:
            for row in rows:
                if row[0] != 'file' or row[1] == self.archive:
                    continue
                info = tarfile.TarInfo(row[1])
                if row[1] == 'notices/LICENSE':
                    info.type = tarfile.SYMTYPE; info.linkname = '/tmp/outside'
                    tar.addfile(info)
                else:
                    data = (self.delivery / row[1]).read_bytes()
                    info.size = len(data); tar.addfile(info, io.BytesIO(data))
        rows[3][2:4] = [str(companion.stat().st_size), sha(companion.read_bytes())]
        self.write_rows(rows); self.refused('Nonregular companion member')


if __name__ == '__main__':
    unittest.main()
