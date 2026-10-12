# SPDX-License-Identifier: MIT
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('scan', ROOT / 'tools/ci/scan-source.py')
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


class AntivirusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)

    def add(self, name, data):
        (self.root / name).write_bytes(data)
        subprocess.run(['git', 'add', '--', name], cwd=self.root, check=True)

    def test_export_ignore_cannot_hide_file(self):
        self.add('.gitattributes', b'hidden export-ignore\n')
        self.add('hidden', b'payload')
        self.assertIn(str(self.root / 'hidden'), scan.inventory(self.root))

    def test_scan_fails_on_detection_and_scanner_error(self):
        self.add('file', b'data')
        for code in (1, 2):
            with self.subTest(code=code), patch.object(scan.subprocess, 'run',
                    side_effect=subprocess.CalledProcessError(code, ['clamscan'])):
                with self.assertRaises(subprocess.CalledProcessError):
                    scan.scan(self.root)

    def test_success_scans_explicit_inventory_with_strict_limits(self):
        self.add('file', b'data')
        def scanner(command, check):
            self.assertTrue(check)
            self.assertIn('--alert-exceeds-max=yes', command)
            self.assertIn('--alert-encrypted=yes', command)
            listing = next(x.split('=', 1)[1] for x in command if x.startswith('--file-list='))
            self.assertEqual(Path(listing).read_text(), str(self.root / 'file') + '\n')
        with patch.object(scan.subprocess, 'run', side_effect=scanner):
            scan.scan(self.root)

    def test_symlink_fails(self):
        (self.root / 'link').symlink_to('/etc/passwd')
        subprocess.run(['git', 'add', 'link'], cwd=self.root, check=True)
        with self.assertRaises(ValueError):
            scan.inventory(self.root)

    def test_newline_path_fails(self):
        self.add('bad\npath', b'x')
        with self.assertRaises(ValueError):
            scan.inventory(self.root)

    def test_unmaterialized_lfs_fails(self):
        self.add('pointer', b'version https://git-lfs.github.com/spec/v1\noid sha256:abc\n')
        with self.assertRaises(ValueError):
            scan.inventory(self.root)

    def test_empty_inventory_fails(self):
        with self.assertRaises(ValueError):
            scan.inventory(self.root)
