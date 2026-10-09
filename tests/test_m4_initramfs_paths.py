# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import importlib.util
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'tools/build-m4-initramfs.py'
spec = importlib.util.spec_from_file_location('m4_initramfs', path)
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class CandidatePathTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='m4 candidate ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'usr/lib').mkdir(parents=True)
        self.library = self.root / 'usr/lib/libfixture.so.1.0'
        self.library.write_bytes(b'candidate library')
        (self.root / 'lib').symlink_to('usr/lib')
        (self.root / 'usr/lib/libfixture.so.1').symlink_to('libfixture.so.1.0')

    def test_relative_and_absolute_links_stay_in_candidate(self):
        self.assertEqual(builder.root_file(self.root, '/lib/libfixture.so.1'), self.library)
        (self.root / 'absolute').symlink_to('/lib/libfixture.so.1')
        self.assertEqual(builder.root_file(self.root, '/absolute'), self.library)
        (self.root / 'usr/lib/absolute').symlink_to('/lib/libfixture.so.1')
        self.assertEqual(builder.root_file(self.root, '/usr/lib/absolute'), self.library)
        (self.root / 'usr/lib/relative').symlink_to('../../lib/libfixture.so.1')
        self.assertEqual(builder.root_file(self.root, '/usr/lib/relative'), self.library)

    def test_missing_library_never_uses_host_file(self):
        (self.root / 'host-looking').symlink_to('/etc/passwd')
        with self.assertRaises(FileNotFoundError):
            builder.root_file(self.root, '/host-looking')
        with self.assertRaises(FileNotFoundError):
            builder.root_file(self.root, '/usr/lib/missing.so')

    def test_escape_and_cycles_reject(self):
        (self.root / 'escape').symlink_to('../etc/passwd')
        (self.root / 'cycle').symlink_to('cycle')
        for name in ('/escape', '/cycle', '/../../etc/passwd'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                builder.root_file(self.root, name)


if __name__ == '__main__':
    unittest.main()
