"""Offline build guards for the J713 SMC poweroff devicetree."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/build-j713-poweroff-dtb.py'
SPEC = importlib.util.spec_from_file_location('poweroff_dtb', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

LAYOUT = ('soc', 'spmi@388714000', 'pmic@e', 'nvmem-layout')
SMC = ('soc', 'smc@38c600000')
U32 = lambda *values: struct.pack('>' + 'I' * len(values), *values)


def base_nodes():
    return {
        (): {'compatible': b'apple,j713\0apple,t8132\0'},
        ('soc',): {},
        SMC: {'compatible': b'apple,t8132-smc\0apple,t8103-smc\0', 'status': b'okay\0'},
        ('soc', 'spmi@388714000'): {},
        ('soc', 'spmi@388714000', 'pmic@e'): {'phandle': U32(0x6e)},
        LAYOUT: {'#address-cells': U32(1), '#size-cells': U32(1)},
        LAYOUT + ('pm-setting@2001',): {'reg': U32(0x2001, 1), 'phandle': U32(0x6d)},
    }


def candidate_nodes(changes=None):
    nodes = base_nodes()
    handles = {}
    for index, (name, (node, reg, bits)) in enumerate(MODULE.CELLS.items()):
        handles[name] = U32(0x109 + index)
        props = {'reg': U32(*reg), 'phandle': handles[name]}
        if bits:
            props['bits'] = U32(*bits)
        nodes[LAYOUT + (node,)] = props
    names = list(MODULE.CELLS)
    nodes[SMC + ('reboot',)] = {
        'compatible': b'apple,smc-reboot\0',
        'nvmem-cells': b''.join(handles[n] for n in names),
        'nvmem-cell-names': b''.join(n.encode() + b'\0' for n in names)}
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
        self.overlay = self.root / 'poweroff.dtso'
        self.overlay.write_bytes(b'source fixture')
        self.output = self.root / 'out.dtb'

    def build(self, candidate):
        def compile_only(command, check):
            destination = Path(command[command.index('-o') + 1])
            destination.write_bytes(candidate if command[0] == 'fdtoverlay' else b'overlay')
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run', side_effect=compile_only):
            return MODULE.build(self.base, self.output, self.overlay)

    def refused(self, candidate):
        with self.assertRaisesRegex(ValueError, 'beyond the SMC reboot child'):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def test_child_and_cells_added(self):
        receipt = self.build(dtb(candidate_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(candidate_nodes()))
        self.assertEqual(set(receipt['added_nvmem_cells']), set(MODULE.CELLS))
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertFalse(receipt['hardware_poweroff_verified'])

    def test_bare_reboot_child_refused(self):
        nodes = candidate_nodes({LAYOUT + (c[0],): None for c in MODULE.CELLS.values()})
        nodes[SMC + ('reboot',)] = {'compatible': b'apple,smc-reboot\0'}
        self.refused(nodes)

    def test_missing_child_refused(self):
        self.refused(base_nodes())

    def test_missing_shutdown_flag_refused(self):
        self.refused(candidate_nodes({LAYOUT + ('shutdown-flag@f80f,3',): None}))

    def test_wrong_shutdown_bit_refused(self):
        self.refused(candidate_nodes({LAYOUT + ('shutdown-flag@f80f,3',): {'bits': U32(2, 1)}}))

    def test_wrong_scratchpad_offset_refused(self):
        self.refused(candidate_nodes({LAYOUT + ('boot-stage@f801',): {'reg': U32(0xf701, 1)}}))

    def test_reused_phandle_refused(self):
        self.refused(candidate_nodes({LAYOUT + ('boot-stage@f801',): {'phandle': U32(0x6d)}}))

    def test_cells_in_wrong_order_refused(self):
        cells = candidate_nodes()[SMC + ('reboot',)]['nvmem-cells']
        self.refused(candidate_nodes({SMC + ('reboot',): {'nvmem-cells': cells[4:] + cells[:4]}}))

    def test_other_property_change_refused(self):
        self.refused(candidate_nodes({SMC: {'status': b'disabled\0'}}))

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'Unqualified'):
                MODULE.build(self.base, self.output, self.overlay)
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


@unittest.skipUnless(shutil.which('dtc'), 'dtc is required')
class CommittedOverlay(unittest.TestCase):
    def test_overlay_declares_the_builder_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / 'poweroff.dtbo'
            subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(out),
                            str(ROOT / 'patches/j713-smc-poweroff.dtso')], check=True)
            nodes, _, _ = MODULE.tree(out.read_bytes())
        self.assertEqual(nodes['/fragment@0']['target-path'], (MODULE.LAYOUT + '\0').encode())
        self.assertEqual(nodes['/fragment@1']['target-path'], (MODULE.SMC + '\0').encode())
        layout = '/fragment@0/__overlay__/'
        for node, reg, bits in MODULE.CELLS.values():
            props = nodes[layout + node]
            self.assertEqual(props['reg'], U32(*reg))
            self.assertEqual(props.get('bits'), U32(*bits) if bits else None)
        reboot = nodes['/fragment@1/__overlay__/reboot']
        self.assertEqual(reboot['nvmem-cell-names'],
                         b''.join(n.encode() + b'\0' for n in MODULE.CELLS))
        handles = [nodes[layout + c[0]]['phandle'] for c in MODULE.CELLS.values()]
        self.assertEqual(reboot['nvmem-cells'], b''.join(handles))


if __name__ == '__main__':
    unittest.main()
