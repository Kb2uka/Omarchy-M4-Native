"""Offline build guards for the J713 always-on sleep power domain devicetree."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/build-j713-sleep-domains-dt.py'
SPEC = importlib.util.spec_from_file_location('sleep_domains_dtb', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

U32 = lambda *values: struct.pack('>' + 'I' * len(values), *values)
PMGR = ('soc', 'power-management@380700000')
GFX = PMGR + ('power-controller@250',)
DISP = PMGR + ('power-controller@10000',)
DPTX = PMGR + ('power-controller@3c8',)
OTHER = PMGR + ('power-controller@1c8',)


def base_nodes():
    domain = lambda label, handle: {
        'compatible': b'apple,t8132-pmgr-pwrstate\0apple,pmgr-pwrstate\0',
        '#power-domain-cells': U32(0), 'label': label + b'\0', 'phandle': U32(handle)}
    return {
        (): {'compatible': b'apple,j713\0apple,t8132\0'},
        ('soc',): {},
        PMGR: {},
        GFX: domain(b'gfx', 0x40),
        DISP: dict(domain(b'disp_cpu', 0x6c), **{'apple,min-state': U32(4)}),
        DPTX: domain(b'dptx_phy', 0x70),
        OTHER: domain(b'ans', 0x20),
    }


def candidate_nodes(changes=None):
    nodes = base_nodes()
    for path in (GFX, DISP, DPTX):
        nodes[path]['apple,always-on'] = b''
    for path, props in (changes or {}).items():
        if props is None:
            nodes.pop(path)
        else:
            nodes[path] = dict(nodes.get(path, {}), **props)
    return nodes


def dtb(nodes):
    """Serialize {path tuple: {name: bytes}} as a version 17 FDT."""
    strings, offsets = bytearray(), {}
    words = bytearray()
    def string(name):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode() + b'\0')
        return offsets[name]
    def pad():
        words.extend(bytes((-len(words)) % 4))
    def emit(path):
        words.extend(U32(1) + (path[-1].encode() if path else b'') + b'\0'); pad()
        for name, value in nodes[path].items():
            words.extend(U32(3, len(value), string(name)) + value); pad()
        for child in sorted(p for p in nodes if len(p) == len(path) + 1 and p[:len(path)] == path):
            emit(child)
        words.extend(U32(2))
    emit(())
    words.extend(U32(9))
    start = 56
    header = U32(0xd00dfeed, start + len(words) + len(strings), start, start + len(words),
                 40, 17, 16, 0, len(strings), len(words))
    return header + bytes(16) + bytes(words) + bytes(strings)


class BuildGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base.dtb'
        self.base.write_bytes(dtb(base_nodes()))
        self.output = self.root / 'out.dtb'

    def build(self, candidate):
        def compile_only(command, check):
            destination = Path(command[command.index('-o') + 1])
            destination.write_bytes(candidate if command[0] == 'fdtoverlay' else b'overlay')
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run', side_effect=compile_only):
            return MODULE.build(self.base, self.output)

    def refused(self, candidate):
        with self.assertRaisesRegex(ValueError, 'beyond the always-on domains'):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def test_three_domains_marked_always_on(self):
        receipt = self.build(dtb(candidate_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(candidate_nodes()))
        self.assertEqual(sorted(receipt['always_on'].values()), ['disp_cpu', 'dptx_phy', 'gfx'])
        self.assertTrue(receipt['reservations_and_boot_cpu_unchanged'])

    def test_unchanged_candidate_refused(self):
        self.refused(base_nodes())

    def test_missing_domain_property_refused(self):
        nodes = candidate_nodes()
        del nodes[DPTX]['apple,always-on']
        self.refused(nodes)

    def test_extra_domain_refused(self):
        self.refused(candidate_nodes({OTHER: {'apple,always-on': b''}}))

    def test_property_value_refused(self):
        self.refused(candidate_nodes({GFX: {'apple,always-on': U32(1)}}))

    def test_other_property_change_refused(self):
        self.refused(candidate_nodes({DISP: {'apple,min-state': U32(0)}}))

    def test_added_node_refused(self):
        self.refused(candidate_nodes({PMGR + ('power-controller@999',): {}}))

    def test_wrong_label_refused_before_compiler(self):
        nodes = base_nodes()
        nodes[GFX]['label'] = b'ans\0'
        self.base.write_bytes(dtb(nodes))
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'Unexpected power domain'):
                MODULE.build(self.base, self.output)
        command.assert_not_called()

    def test_already_always_on_refused(self):
        self.base.write_bytes(dtb(candidate_nodes()))
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'already always-on'):
                MODULE.build(self.base, self.output)
        command.assert_not_called()

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'Unqualified backlight base'):
                MODULE.build(self.base, self.output)
        command.assert_not_called()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'custom')
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.build(dtb(candidate_nodes()))
        self.assertEqual(self.output.read_bytes(), b'custom')

    def test_base_symlink_refused(self):
        real = self.root / 'real.dtb'
        self.base.rename(real)
        self.base.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.build(dtb(candidate_nodes()))


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'),
                     'dtc and fdtoverlay are required')
class GeneratedOverlay(unittest.TestCase):
    def test_overlay_applies_only_the_property(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'o.dtso').write_text(MODULE.overlay_source())
            (root / 'base.dtb').write_bytes(dtb(base_nodes()))
            subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(root / 'o.dtbo'),
                            str(root / 'o.dtso')], check=True)
            subprocess.run(['fdtoverlay', '-i', str(root / 'base.dtb'), '-o',
                            str(root / 'out.dtb'), str(root / 'o.dtbo')], check=True)
            new, _, _ = MODULE.tree((root / 'out.dtb').read_bytes())
            old, _, _ = MODULE.tree(dtb(base_nodes()))
        self.assertEqual(new, MODULE.expected_nodes(old))


if __name__ == '__main__':
    unittest.main()
