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
        self.receipt = self.dir / 'stage2.issue82.json'
        self.download = self.dir / 'm1n1-stage2-issue82-test1.bin'
        self.download.write_bytes(TEST)

    def run_helper(self, *args, known=(STOCK, REPORTER), published=()):
        env = dict(os.environ, M4_M1N1_TEST_BOOT=str(self.boot), M4_M1N1_TEST_KNOWN=','.join(sha(k) for k in known),
                   M4_M1N1_TEST_PUBLISHED=','.join(sha(p) for p in published))
        result = subprocess.run([sys.executable, str(HELPER), *args], capture_output=True, text=True, env=env)
        return result.returncode, result.stdout, result.stderr

    def install(self):
        return self.run_helper('install', str(self.download), sha(TEST))

    def test_install_replaces_only_stage2_and_keeps_it(self):
        for start in (STOCK, REPORTER):
            with self.subTest(start=start[:1]):
                self.boot.write_bytes(start + PAYLOAD)
                self.backup.unlink(missing_ok=True)
                self.receipt.unlink(missing_ok=True)
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
        self.assertFalse(self.receipt.exists())
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
        self.assertIn('run rollback first', err)
        self.assertEqual(self.backup.read_bytes(), STOCK)

    def test_rollback_refusals_change_nothing(self):
        self.boot.write_bytes(STOCK + PAYLOAD)
        self.assertEqual(self.install()[0], 0)
        self.backup.write_bytes(b'X' * STAGE2)
        code, _, err = self.run_helper('rollback')
        self.assertEqual(code, 1)
        self.assertIn('backup is not the replaced stage 2', err)
        self.assertEqual(self.boot.read_bytes(), TEST + PAYLOAD)
        # Another loader, known or not, replaced the test build since: keep it.
        self.backup.write_bytes(STOCK)
        for other in (REPORTER, b'N' * STAGE2):
            with self.subTest(other=other[:1]):
                self.boot.write_bytes(other + PAYLOAD)
                code, _, err = self.run_helper('rollback')
                self.assertEqual(code, 1)
                self.assertIn('not the installed test build', err)
                self.assertEqual(self.boot.read_bytes(), other + PAYLOAD)
                self.assertTrue(self.backup.exists())
        self.boot.unlink()
        code, _, err = self.run_helper('rollback')
        self.assertEqual(code, 1)
        self.assertIn('boot.bin must be a regular file', err)
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
        status = json.loads(self.run_helper('status')[1])
        self.assertEqual(status['stage2'], 'test build or unknown')
        self.assertEqual(status['receipt'], {'installed': sha(TEST), 'replaced': sha(STOCK)})

    def test_rollback_finishes_interrupted_states(self):
        receipt = json.dumps({'installed': sha(TEST), 'replaced': sha(STOCK)})
        # Stopped after the receipt, after the backup, or after rollback removed the backup.
        for name, files in (('receipt only', {'receipt'}), ('receipt and backup', {'receipt', 'backup'})):
            with self.subTest(name):
                self.boot.write_bytes(STOCK + PAYLOAD)
                self.receipt.write_text(receipt)
                if 'backup' in files:
                    self.backup.write_bytes(STOCK)
                self.assertEqual(self.install()[0], 1)
                code, out, err = self.run_helper('rollback')
                self.assertEqual(code, 0, err)
                self.assertEqual(json.loads(out)['restored'], 'already in place')
                self.assertEqual(self.boot.read_bytes(), STOCK + PAYLOAD)
                self.assertFalse(self.receipt.exists() or self.backup.exists())

    def test_rollback_of_a_receiptless_install_needs_a_published_build(self):
        # The first published helper kept only the backup.
        for published, code, boot in (((), 1, TEST), ((TEST,), 0, STOCK)):
            with self.subTest(published=bool(published)):
                self.boot.write_bytes(TEST + PAYLOAD)
                self.backup.write_bytes(STOCK)
                self.assertEqual(self.run_helper('rollback', published=published)[0], code)
                self.assertEqual(self.boot.read_bytes(), boot + PAYLOAD)
                self.assertEqual(self.backup.exists(), code == 1)

    def test_rollback_refuses_a_backup_the_receipt_does_not_name(self):
        self.boot.write_bytes(TEST + PAYLOAD)
        self.backup.write_bytes(REPORTER)
        self.receipt.write_text(json.dumps({'installed': sha(TEST), 'replaced': sha(STOCK)}))
        code, _, err = self.run_helper('rollback')
        self.assertEqual(code, 1)
        self.assertIn('backup is not the replaced stage 2', err)
        self.assertEqual(self.boot.read_bytes(), TEST + PAYLOAD)

    def test_receiptless_backup_already_restored_is_cleaned_up(self):
        self.boot.write_bytes(REPORTER + PAYLOAD)
        self.backup.write_bytes(REPORTER)
        code, out, err = self.run_helper('rollback')
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)['restored'], 'already in place')
        self.assertFalse(self.backup.exists())

    def test_stale_temporary_files_do_not_block(self):
        self.boot.write_bytes(STOCK + PAYLOAD)
        for name in ('boot.bin.new', 'stage2.pre-issue82.bin.new', 'stage2.issue82.json.new'):
            (self.dir / name).write_bytes(b'partial')
        code, _, err = self.install()
        self.assertEqual(code, 0, err)
        self.assertEqual(self.boot.read_bytes(), TEST + PAYLOAD)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.name.endswith('.new')), [])

    def test_foreign_receipt_is_refused(self):
        self.boot.write_bytes(TEST + PAYLOAD)
        self.backup.write_bytes(STOCK)
        for text in ('not json', '[]', json.dumps({'installed': sha(TEST), 'replaced': '0' * 64}),
                     json.dumps({'installed': None, 'replaced': sha(STOCK)}),
                     json.dumps({'installed': sha(STOCK), 'replaced': sha(STOCK)})):
            with self.subTest(text=text[:8]):
                self.receipt.write_text(text)
                for command in ('status', 'rollback'):
                    code, _, err = self.run_helper(command)
                    self.assertEqual(code, 1)
                    self.assertIn('not a receipt this helper wrote', err)
                self.assertEqual(self.boot.read_bytes(), TEST + PAYLOAD)


if __name__ == '__main__':
    unittest.main()
