# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'patches-m1n1/m4-m1n1-test-stage2.py'
STAGE2 = 0x110000
STOCK = b'S' * STAGE2
REPORTER = b'R' * STAGE2
TEST = b'T' * STAGE2
PAYLOAD = b'chosen.bootargs=... kernel and devicetree'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class M1n1TestStage2Tests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.dir = Path(temporary.name)
        self.boot = self.dir / 'boot.bin'
        self.backup = self.dir / 'stage2.pre-issue82.bin'
        self.download = self.dir / 'm1n1-stage2-issue82-test1.bin'
        self.download.write_bytes(TEST)

    def run_helper(self, *args, known=(STOCK, REPORTER)):
        env = dict(os.environ, M4_M1N1_TEST_BOOT=str(self.boot), M4_M1N1_TEST_KNOWN=','.join(sha(k) for k in known))
        result = subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True, env=env)
        return result.returncode, result.stdout, result.stderr

    def install(self):
        return self.run_helper('install', str(self.download), sha(TEST))

    def test_install_replaces_only_stage2_and_keeps_it(self):
        for start in (STOCK, REPORTER):
            with self.subTest(start=start[:1]):
                self.boot.write_bytes(start + PAYLOAD)
                self.backup.unlink(missing_ok=True)
                code, out, err = self.install()
                self.assertEqual(code, 0, err)
                self.assertEqual(self.boot.read_bytes(), TEST + PAYLOAD)
                self.assertEqual(self.backup.read_bytes(), start)
                self.assertEqual(json.loads(out)['status'], 'installed; takes effect at next boot')

    def test_rollback_restores_stage2_and_keeps_a_later_payload_change(self):
        self.boot.write_bytes(STOCK + PAYLOAD)
        self.assertEqual(self.install()[0], 0)
        self.boot.write_bytes(TEST + PAYLOAD + b' updated devicetree')
        code, out, err = self.run_helper('rollback')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.boot.read_bytes(), STOCK + PAYLOAD + b' updated devicetree')
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.run_helper('rollback')[0], 1)

    def test_refusals_change_nothing(self):
        cases = {
            'wrong hash': (STOCK, ('install', str(self.download), '0' * 64)),
            'unknown stage2': (b'U' * STAGE2, ('install', str(self.download), sha(TEST))),
            'known loader as test': (STOCK, ('install', str(self.download), sha(STOCK))),
        }
        for name, (start, args) in cases.items():
            with self.subTest(name):
                self.boot.write_bytes(start + PAYLOAD)
                if name == 'known loader as test':
                    self.download.write_bytes(STOCK)
                code, _, err = self.run_helper(*args)
                self.download.write_bytes(TEST)
                self.assertEqual(code, 1, err)
                self.assertEqual(self.boot.read_bytes(), start + PAYLOAD)
                self.assertFalse(self.backup.exists())

    def test_refuses_short_loader_and_second_install(self):
        self.boot.write_bytes(STOCK + PAYLOAD)
        self.download.write_bytes(TEST[:-1])
        self.assertEqual(self.run_helper('install', str(self.download), sha(TEST[:-1]))[0], 1)
        self.download.write_bytes(TEST)
        self.assertEqual(self.install()[0], 0)
        code, _, err = self.install()
        self.assertEqual(code, 1)
        self.assertIn('roll back first', err)
        self.assertEqual(self.backup.read_bytes(), STOCK)

    def test_rollback_refusals_change_nothing(self):
        self.boot.write_bytes(STOCK + PAYLOAD)
        self.assertEqual(self.install()[0], 0)
        self.backup.write_bytes(b'X' * STAGE2)
        code, _, err = self.run_helper('rollback')
        self.assertEqual(code, 1)
        self.assertIn('backup is not a known stage 2', err)
        self.assertEqual(self.boot.read_bytes(), TEST + PAYLOAD)
        # A reinstall put a different known loader in place: keep it.
        self.backup.write_bytes(STOCK)
        self.boot.write_bytes(REPORTER + PAYLOAD)
        code, _, err = self.run_helper('rollback')
        self.assertEqual(code, 1)
        self.assertIn('not a test build', err)
        self.assertEqual(self.boot.read_bytes(), REPORTER + PAYLOAD)
        self.assertTrue(self.backup.exists())

    def test_missing_boot_bin_is_a_clean_refusal(self):
        for args in (('status',), ('install', str(self.download), sha(TEST))):
            with self.subTest(args[0]):
                code, out, err = self.run_helper(*args)
                self.assertEqual(code, 1)
                self.assertIn('boot.bin must be a regular file', err)
                self.assertEqual(out, '')

    def test_status_names_the_loader(self):
        self.boot.write_bytes(STOCK + PAYLOAD)
        code, out, _ = self.run_helper('status')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['stage2_sha256'], sha(STOCK))
        self.install()
        self.assertEqual(json.loads(self.run_helper('status')[1])['stage2'], 'test build or unknown')


if __name__ == '__main__':
    unittest.main()
