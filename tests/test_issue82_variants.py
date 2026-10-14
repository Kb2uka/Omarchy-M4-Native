# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import hashlib
import importlib.util
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'patches-m1n1/issue82-variants.py'
spec = importlib.util.spec_from_file_location('issue82_variants', SCRIPT)
variants = importlib.util.module_from_spec(spec)
spec.loader.exec_module(variants)

SIZE = 0x120000
# Written out here, not read from the script, so a dropped or moved site fails.
EXPECTED = {
    'v1': {0x23e90: 0x94000ace, 0x23ea0: 0x94000aca},
    'v2': {0x26964: 0xb9000288, 0x26974: 0xb9000288, 0x26988: 0xb9000288, 0x26998: 0xb9000288},
    'v3': {0x26954: 0x940028b5},
}


def stage1():
    """A stand-in for test 1: every site holds its expected instruction, plus two tags."""
    data = bytearray(b'\x5a' * SIZE)
    for sites in variants.SITES.values():
        for offset, (word, _, _) in sites.items():
            struct.pack_into('<I', data, offset, word)
    data[0x65000:0x65000 + len(variants.TAG)] = variants.TAG
    data[0x6e000:0x6e000 + len(variants.TAG)] = variants.TAG
    return bytes(data)


def changed(before, after):
    return {i for i in range(len(before)) if before[i] != after[i]}


class Issue82VariantTests(unittest.TestCase):
    def test_sites_match_the_disassembly_of_test1(self):
        self.assertEqual({name: {o: w for o, (w, _, _) in sites.items()} for name, sites in variants.SITES.items()},
                         EXPECTED)

    def test_each_variant_nops_only_its_sites_and_retags(self):
        data = stage1()
        tags = [data.find(variants.TAG), data.rfind(variants.TAG)]
        for name, sites in variants.SITES.items():
            with self.subTest(name=name):
                out = variants.make_variant(data, name, expected_sha=None)
                self.assertEqual(len(out), len(data))
                for offset in sites:
                    self.assertEqual(struct.unpack_from('<I', out, offset)[0], variants.NOP)
                site_bytes = {offset + i for offset in sites for i in range(4)}
                tag_bytes = {t + i for t in tags for i in range(len(variants.TAG))}
                self.assertTrue(changed(data, out) <= site_bytes | tag_bytes)
                self.assertEqual(out.count(b'20261008-i82' + name.encode()), 2)
                self.assertNotIn(variants.TAG, out)

    def test_variants_touch_disjoint_sites(self):
        seen = set()
        for sites in variants.SITES.values():
            self.assertFalse(seen & set(sites))
            seen |= set(sites)

    def test_removed_calls_reach_the_documented_functions(self):
        for sites in variants.SITES.values():
            for offset, (word, target, what) in sites.items():
                if target is not None:
                    self.assertEqual(variants.bl_target(offset, word), target, what)
        self.assertEqual(variants.bl_target(0x100, 0x97ffffff), 0xfc)

    def test_refuses_a_different_loader(self):
        with self.assertRaisesRegex(variants.Refused, 'not the test 1'):
            variants.make_variant(stage1(), 'v1')
        self.assertEqual(variants.make_variant(stage1(), 'v1', expected_sha=hashlib.sha256(stage1()).hexdigest())[:4],
                         stage1()[:4])

    def test_refuses_an_unexpected_instruction(self):
        for name, sites in variants.SITES.items():
            offset = next(iter(sites))
            data = bytearray(stage1())
            struct.pack_into('<I', data, offset, variants.NOP)
            with self.subTest(name=name), self.assertRaisesRegex(variants.Refused, 'found 0xd503201f'):
                variants.make_variant(bytes(data), name, expected_sha=None)

    def test_refuses_a_call_to_another_function(self):
        data = bytearray(stage1())
        offset = 0x23e90
        struct.pack_into('<I', data, offset, 0x94000acf)
        saved = variants.SITES['v1'][offset]
        variants.SITES['v1'][offset] = (0x94000acf, saved[1], saved[2])
        try:
            with self.assertRaisesRegex(variants.Refused, 'does not reach'):
                variants.make_variant(bytes(data), 'v1', expected_sha=None)
        finally:
            variants.SITES['v1'][offset] = saved

    def test_refuses_without_both_tags(self):
        data = stage1().replace(variants.TAG, b'x' * len(variants.TAG), 1)
        with self.assertRaisesRegex(variants.Refused, 'copies of the -dctx1 tag'):
            variants.make_variant(data, 'v2', expected_sha=None)

    def test_cli_writes_each_variant_and_its_sum(self):
        data = stage1()
        saved = variants.TEST1
        variants.TEST1 = hashlib.sha256(data).hexdigest()
        try:
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / 'stage1.bin'
                source.write_bytes(data)
                out = Path(temporary) / 'out'
                self.assertEqual(variants.main(['issue82-variants.py', str(source), str(out)]), 0)
                names = ['m1n1-stage1-issue82-%s.bin' % name for name in EXPECTED]
                self.assertEqual(sorted(p.name for p in out.iterdir()), sorted(names + ['SHA256SUMS']))
                sums = (out / 'SHA256SUMS').read_text().splitlines()
                self.assertEqual(len(sums), len(names))
                for name, line in zip(EXPECTED, sums):
                    written = (out / ('m1n1-stage1-issue82-%s.bin' % name)).read_bytes()
                    self.assertEqual(line, '%s  m1n1-stage1-issue82-%s.bin' % (hashlib.sha256(written).hexdigest(), name))
                    self.assertNotEqual(written, data)
                    for offset in EXPECTED[name]:
                        self.assertEqual(struct.unpack_from('<I', written, offset)[0], variants.NOP)
        finally:
            variants.TEST1 = saved

    def test_cli_refuses_and_writes_nothing_for_a_wrong_input(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'stage1.bin'
            source.write_bytes(stage1())
            out = Path(temporary) / 'out'
            result = subprocess.run([sys.executable, str(SCRIPT), str(source), str(out)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('refused', result.stderr)
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
