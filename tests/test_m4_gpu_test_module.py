# SPDX-License-Identifier: MIT
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HELPER = Path(os.environ.get('M4_GPU_TEST_MODULE', ROOT / 'gpu/j713/m4-gpu-test-module'))
ABI = '7.1.9-m4-gpu-backport-20261008 SMP preempt mod_unload aarch64'
STOCK = b'alpha.1 module'
TEST = b'test module'
OTHER = '/opt/m4-mesa/lib/libEGL.so.1.0.0'


def sha(data):
    return hashlib.sha256(data).hexdigest()


class GpuTestModuleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.gpu = self.root / 'opt/m4-gpu'
        self.gpu.mkdir(parents=True)
        self.module = self.gpu / 'asahi_m4.ko'
        self.module.write_bytes(STOCK)
        self.sums = self.gpu / 'runtime.sha256'
        self.original = f'{sha(STOCK)}  /opt/m4-gpu/asahi_m4.ko\n{"1" * 64}  {OTHER}\n'
        self.sums.write_text(self.original)
        self.sums.chmod(0o664)
        self.download = self.root / 'download.ko'
        self.download.write_bytes(TEST)
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        modinfo = bin_dir / 'modinfo'
        modinfo.write_text('#!/bin/sh\n[ "$1" = -F ] && [ "$2" = vermagic ] || exit 64\n'
                           'cat "$3.vermagic" 2>/dev/null || echo "$ABI"\n')
        modinfo.chmod(0o755)
        self.env = {**os.environ, 'M4_GPU_TEST_ROOT': str(self.root), 'M4_GPU_TEST_PATH': str(bin_dir),
                    'M4_GPU_TEST_STOCK': sha(STOCK), 'ABI': ABI}

    def run_helper(self, *args):
        return subprocess.run(['bash', str(HELPER), *args], capture_output=True, text=True, env=self.env)

    def install(self, sum_=None):
        return self.run_helper('install', str(self.download), sha(TEST) if sum_ is None else sum_)

    def assert_untouched(self):
        self.assertEqual(self.module.read_bytes(), STOCK)
        self.assertEqual(self.sums.read_text(), self.original)
        self.assertFalse((self.gpu / 'alpha1-backup').exists())

    def test_install_swaps_module_and_its_pin_only(self):
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.module.read_bytes(), TEST)
        self.assertEqual(self.sums.read_text(),
                         f'{sha(TEST)}  /opt/m4-gpu/asahi_m4.ko\n{"1" * 64}  {OTHER}\n')
        self.assertEqual(self.sums.stat().st_mode & 0o777, 0o664)
        self.assertIn('test module', self.run_helper('status').stdout)

    def test_rollback_restores_exact_files(self):
        self.assertEqual(self.install().returncode, 0)
        result = self.run_helper('rollback')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_untouched()
        self.assertIn('alpha.1 module', self.run_helper('status').stdout)

    def test_download_mismatch_refused(self):
        self.assertNotEqual(self.install('0' * 64).returncode, 0)
        self.assert_untouched()

    def test_wrong_kernel_refused(self):
        Path(str(self.download) + '.vermagic').write_text('7.1.9-other SMP preempt mod_unload aarch64\n')
        self.assertNotEqual(self.install().returncode, 0)
        self.assert_untouched()

    def test_malformed_or_stock_sum_refused(self):
        for value in ['', 'abc', sha(TEST).upper(), sha(STOCK)]:
            self.assertNotEqual(self.install(value).returncode, 0, value)
            self.assert_untouched()

    def test_non_stock_install_refused(self):
        self.module.write_bytes(b'something else')
        self.sums.write_text(self.original.replace(sha(STOCK), sha(b'something else')))
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.module.read_bytes(), b'something else')
        self.assertFalse((self.gpu / 'alpha1-backup').exists())

    def test_hand_replaced_module_refused(self):
        self.module.write_bytes(b'something else')
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(self.module.read_bytes(), b'something else')
        self.assertEqual(self.sums.read_text(), self.original)
        self.assertFalse((self.gpu / 'alpha1-backup').exists())

    def test_unpinned_or_twice_pinned_module_refused(self):
        for text in [f'{"1" * 64}  {OTHER}\n', self.original + self.original.splitlines(True)[0]]:
            self.sums.write_text(text)
            self.assertNotEqual(self.install().returncode, 0)
            self.assertEqual(self.module.read_bytes(), STOCK)
            self.assertEqual(self.sums.read_text(), text)

    def test_second_install_refused(self):
        self.assertEqual(self.install().returncode, 0)
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual((self.gpu / 'alpha1-backup/asahi_m4.ko').read_bytes(), STOCK)

    def test_rollback_without_backup_refused(self):
        self.assertNotEqual(self.run_helper('rollback').returncode, 0)
        self.assert_untouched()

    def test_interrupted_install_can_roll_back(self):
        self.assertEqual(self.install().returncode, 0)
        self.sums.write_text(self.original)
        self.assertEqual(self.run_helper('rollback').returncode, 0)
        self.assert_untouched()

    def test_real_paths_without_test_root(self):
        env = {k: v for k, v in self.env.items() if k != 'M4_GPU_TEST_ROOT'}
        text = HELPER.read_text()
        self.assertIn('stock=80cf3669575edd09aed226a93e47aaaf132e70f1070154629c413c0b40d34578', text)
        self.assertIn("[ -z \"$root\" ] || stock=", text)
        result = subprocess.run(['bash', str(HELPER), 'bogus'], capture_output=True, text=True, env=env)
        self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
