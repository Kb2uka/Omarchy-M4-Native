# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The notch stage 2 hands Linux the whole J713 panel; stock m1n1 hides 64 rows (#89).

Compiles m1n1's real get_notchless_fb(), from alpha.1's stage-2 src/kboot.c and
with display/j713/m1n1-show-notch.patch applied, against a simulated boot.
"""
import hashlib
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
DISPLAY = ROOT / 'display/j713'
BASE = DISPLAY / 'm1n1-base'
PATCH = DISPLAY / 'm1n1-show-notch.patch'
FIXTURE = Path(__file__).with_name('m1n1_show_notch_fixture.c')

# Per-file hashes from alpha.1's published companion source/m1n1-stage2-files.json.
PUBLISHED = {
    'LICENSE': '11ca423a564b52d3b7f260398baa20d016c25ae180fbdd0067c27b7d1ed7b420',
    'README.md': '0b52b29a5d4e161adb31cf25f8aed28772e4dff0761a0db0f32c7966d4b1dc8c',
    '3rdparty_licenses/LICENSE.BSD-2.libfdt': 'b410d206e6a11b2eb4aa7766a01b6cdf705b2a3c8ea26a00530587859ad873cd',
    '3rdparty_licenses/LICENSE.BSD-3.arm': 'ba420ebd24cc61d4b97d7173acd216e742524ed304ee2b2df6cf8546fbfca48e',
    '3rdparty_licenses/LICENSE.BSD-3.dwc3': 'aaef31cc73e25d5b48fa903957fae7243db7628f270b0511bf4232be871c5434',
    '3rdparty_licenses/LICENSE.CC0': 'a2010f343487d3f7618affe54f789f5487602331c0a8d03f49e9a7c547cf0499',
    '3rdparty_licenses/LICENSE.GPL-2': '86dc744860e6dfacfeba2f33fea908db03fe67c7e37a878285b7aae8e4596735',
    '3rdparty_licenses/LICENSE.minlzma': 'c8d8f2121d2bd6ffbe66f9ee9ade1076dc2951f685f6d0c65992121c21fbf12e',
    '3rdparty_licenses/LICENSE.OFL-1.1': '2e14ee65e55dbcc7d66347cd7c76a10a1a18ac49dd7989a2b3838b479676f746',
    '3rdparty_licenses/LICENSE.tinf': 'f39b507b81b9ba1edce7fe742bbd68fda4b852063ce7299274ae97991d48814b',
    'src/kboot.c': '833080681358a9506888464f441e6e60287841487ec627707112fd0a1aea3b8f',
}
# (base offset in rows, height) Linux gets. Only the J713 case differs between builds.
STOCK = {'j713': (64, 1600), 'no_notch': (0, 1664), 'occluded_zero': (0, 1664), 'already_16_10': (0, 1600)}
NOTCH = {**STOCK, 'j713': (0, 1664)}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def notchless(source):
    """The get_notchless_fb() definition from a kboot.c."""
    match = re.search(r'^void get_notchless_fb\(u64 \*fb_base, u64 \*fb_height\)\n\{\n.*?^\}\n', source, re.M | re.S)
    if match is None:
        raise AssertionError('get_notchless_fb() not found')
    return match.group(0)


class M1n1ShowNotchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='m1n1-notch-')
        cls.work = Path(cls.temp.name)
        cls.after = cls.work / 'after'
        shutil.copytree(BASE, cls.after)
        subprocess.run(['patch', '-p1', '--no-backup-if-mismatch', '--forward', '-d', str(cls.after),
                        '-i', str(PATCH)], check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def build(self, name, kboot):
        directory = self.work / name
        directory.mkdir(exist_ok=True)
        (directory / 'notchless.inc').write_text(notchless(kboot))
        exe = directory / 'fixture'
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Wsign-compare', '-Wunused-parameter',
                        '-Werror', '-iquote', str(directory), str(FIXTURE), '-o', str(exe)],
                       check=True, capture_output=True, text=True)
        return exe

    def run_cases(self, exe):
        results = {}
        for case in STOCK:
            output = subprocess.run([str(exe), case], capture_output=True, text=True, timeout=30, check=True)
            row, height = re.search(r'RESULT (\d+) (\d+)', output.stderr).groups()
            results[case] = (int(row), int(height))
        return results

    def test_base_is_the_published_alpha1_source(self):
        self.assertEqual({str(p.relative_to(BASE)): sha256(p) for p in BASE.rglob('*')
                          if p.is_file() and p.name != 'SHA256SUMS'}, PUBLISHED)
        sums = dict(line.split()[::-1] for line in (BASE / 'SHA256SUMS').read_text().splitlines())
        self.assertEqual(sums, PUBLISHED)

    def test_stock_m1n1_hides_the_notch_rows(self):
        self.assertEqual(self.run_cases(self.build('stock', (BASE / 'src/kboot.c').read_text())), STOCK)

    def test_notch_build_hands_linux_the_whole_panel(self):
        self.assertEqual(self.run_cases(self.build('notch', (self.after / 'src/kboot.c').read_text())), NOTCH)

    def test_patch_changes_only_the_crop(self):
        before = (BASE / 'src/kboot.c').read_text()
        after = (self.after / 'src/kboot.c').read_text()
        self.assertEqual(before.replace(notchless(before), ''), after.replace(notchless(after), ''))
        self.assertNotIn('Hiding notch', after)
        self.assertIn('display: Showing notch', after)
        # The devicetree notch-height note follows the cropped height, so it is not written.
        self.assertIn('if (cur_boot_args.video.height - fb_height) {', after)

    def test_build_recipe_pins_alpha1_and_this_patch(self):
        script = (DISPLAY / 'build-notch-loader.sh').read_text()
        self.assertIn('stock_sha=3a3f7a64c65fc366d5ac9aece3e1bee64119be012aa1ec5b15d2743c6f91fce4', script)
        self.assertIn('source_sha=c394517941a5700b227718bb02e3df3dd43c9ad5a8e53c9447a55d3c36846c44', script)
        self.assertIn('patch=$here/m1n1-show-notch.patch', script)
        self.assertIn('build notch "$tag-notch1"', script)


if __name__ == '__main__':
    unittest.main()
