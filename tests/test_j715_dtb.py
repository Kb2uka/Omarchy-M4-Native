# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
# Copyright (c) 2026 Kazuhide Takahashi
"""Offline build guards for the 15-inch M4 Air (J715) boot devicetree."""
import importlib.util
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('j715_dtb', 'tools/build-j715-dtb.py')
POWEROFF_TESTS = load('poweroff_fixtures', 'tests/test_j713_poweroff_dtb.py')
dtb, U32 = POWEROFF_TESTS.dtb, POWEROFF_TESTS.U32
OKAY = b'okay\0'
PORT0 = ('soc', 'pcie@1cb0000000', 'pci@0,0')
WIFI, BLUETOOTH = PORT0 + ('wifi@0,0',), PORT0 + ('bluetooth@0,1',)
TRACKPAD = ('soc', 'fifo@394b14000', 'input@8000', 'multi-touch')


def key(path):
    """'/soc/x' -> ('soc', 'x'), the fixture's node key."""
    return tuple(part for part in path.split('/') if part)


def base_nodes():
    """The J713 front-USB devicetree's nodes that the J715 overlay touches, and a neighbour."""
    return {
        (): {'compatible': MODULE.J713, 'model': MODULE.J713_MODEL},
        ('soc',): {},
        ('soc', 'pcie@1cb0000000'): {'status': OKAY},
        PORT0: {'reset-gpios': U32(0x3f, 163, 1), 'bus-range': U32(1, 1)},
        WIFI: {'brcm,board-type': b'apple,garden\0', 'compatible': b'pci14e4,4434\0'},
        BLUETOOTH: {'brcm,board-type': b'apple,garden\0', 'compatible': b'pci14e4,5f72\0'},
        ('soc', 'fifo@394b14000'): {'status': OKAY, 'phandle': U32(0x3b)},
        ('soc', 'fifo@394b14000', 'input@8000'): {},
        TRACKPAD: {'compatible': b'apple,j713-touchpad\0', 'firmware-name': b'apple/tpmtfw-j713.bin\0'},
        ('soc', 'usb@402280000'): {'status': OKAY, 'iommus': U32(0x53, 0, 0x54, 1)},
    }


def with_reservation(data, address, size):
    """The same FDT with one memory reservation added ahead of the terminating entry."""
    header = list(struct.unpack('>10I', data[:40]))
    header[1] += 16  # totalsize
    header[2] += 16  # off_dt_struct
    header[3] += 16  # off_dt_strings
    return struct.pack('>10I', *header) + struct.pack('>QQ', address, size) + data[40:]


def expected_dtb_nodes(changes=None):
    """The candidate as fixture nodes, from the builder's own expectation plus optional changes."""
    old = {'/' + '/'.join(path): props for path, props in base_nodes().items()}
    nodes = {key(path): props for path, props in MODULE.expected_nodes(old).items()}
    for name, props in (changes or {}).items():
        if props is None:
            nodes.pop(name)
        else:
            nodes[name] = dict(nodes.get(name, {}), **props)
    return nodes


class BuildGuards(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'base.dtb'
        self.base.write_bytes(dtb(base_nodes()))
        self.overlay = self.root / 'j715.dtso'
        self.overlay.write_bytes(b'source fixture')
        self.output = self.root / 'out.dtb'

    def build(self, candidate, base=None):
        if base is not None:
            self.base.write_bytes(dtb(base))

        def compile_only(command, check):
            Path(command[command.index('-o') + 1]).write_bytes(
                candidate if command[0] == 'fdtoverlay' else b'overlay')
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run', side_effect=compile_only):
            return MODULE.build(self.base, self.output, self.overlay)

    def refused(self, candidate, message='beyond the J715 board identity'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def base_refused(self, base, message):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(expected_dtb_nodes()), base)
        self.assertFalse(self.output.exists())

    def test_j715_identity_built(self):
        receipt = self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(expected_dtb_nodes()))
        nodes = expected_dtb_nodes()
        self.assertEqual(nodes[()]['compatible'], b'apple,j715\0apple,t8132\0apple,arm-platform\0')
        self.assertEqual(nodes[()]['model'], b'Apple MacBook Air (15-inch, M4, 2025)\0')
        for radio in (WIFI, BLUETOOTH):
            self.assertEqual(nodes[radio]['brcm,board-type'], b'apple,kos\0')
        self.assertEqual(nodes[TRACKPAD]['firmware-name'], b'apple/tpmtfw-j715.bin\0')
        self.assertEqual(nodes[('soc', 'usb@402280000')], base_nodes()[('soc', 'usb@402280000')])
        self.assertEqual(receipt['board'], 'apple,j715')
        self.assertEqual(receipt['radios'], 'apple,kos')

    def test_j713_identity_kept_refused(self):
        self.refused(expected_dtb_nodes({(): {'compatible': MODULE.J713}}))
        self.refused(expected_dtb_nodes({(): {'model': MODULE.J713_MODEL}}))

    def test_garden_radios_refused(self):
        for radio in (WIFI, BLUETOOTH):
            with self.subTest(node=radio):
                self.refused(expected_dtb_nodes({radio: {'brcm,board-type': b'apple,garden\0'}}))

    def test_j713_trackpad_firmware_refused(self):
        self.refused(expected_dtb_nodes({TRACKPAD: {'firmware-name': b'apple/tpmtfw-j713.bin\0'}}))

    def test_other_change_refused(self):
        self.refused(expected_dtb_nodes({('soc', 'usb@402280000'): {'status': b'disabled\0'}}))
        self.refused(expected_dtb_nodes({TRACKPAD: {'compatible': b'apple,j715-touchpad\0'}}))
        self.refused(expected_dtb_nodes({('soc', 'extra'): {}}))

    def test_changed_boot_cpu_refused(self):
        candidate = bytearray(dtb(expected_dtb_nodes()))
        candidate[28:32] = U32(1)  # boot_cpuid_phys
        with self.assertRaisesRegex(ValueError, 'beyond the J715 board identity'):
            self.build(bytes(candidate))
        self.assertFalse(self.output.exists())

    def test_added_memory_reservation_refused(self):
        with self.assertRaisesRegex(ValueError, 'beyond the J715 board identity'):
            self.build(with_reservation(dtb(expected_dtb_nodes()), 0x10000000000, 0x4000))
        self.assertFalse(self.output.exists())

    def test_wrong_board_refused(self):
        for prop, value in (('compatible', b'apple,j773g\0apple,t8132\0apple,arm-platform\0'),
                            ('model', b'Apple Mac mini (M4, 2024)\0')):
            with self.subTest(prop=prop):
                base = base_nodes()
                base[()][prop] = value
                self.base_refused(base, 'Wrong board')

    def test_other_radios_refused(self):
        for radio in (WIFI, BLUETOOTH):
            with self.subTest(node=radio):
                base = base_nodes()
                base[radio]['brcm,board-type'] = b'apple,sakhalin\0'
                self.base_refused(base, 'Unexpected radios')
        base = base_nodes()
        base.pop(BLUETOOTH)
        self.base_refused(base, 'Unexpected radios')

    def test_other_trackpad_firmware_refused(self):
        base = base_nodes()
        base[TRACKPAD]['firmware-name'] = b'apple/tpmtfw-j715.bin\0'
        self.base_refused(base, 'Unexpected trackpad firmware')
        base = base_nodes()
        base.pop(TRACKPAD)
        self.base_refused(base, 'Unexpected trackpad firmware')

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Unqualified J713 base DTB'):
                MODULE.build(self.base, self.output, self.overlay)
        run.assert_not_called()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), b'keep')


class Lineage(unittest.TestCase):
    def test_base_is_the_newest_j713_devicetree(self):
        updater = load('updater', 'tools/m4-j713-update-boot-dtb.py')
        self.assertEqual(MODULE.BASE_SHA256, updater.USB0_DTB)

    def test_overlay_names_nodes_by_path_only(self):
        overlay = (ROOT / 'patches/j715-board.dtso').read_text()
        self.assertNotIn('<0x', overlay)
        for target in ('"/"', '"/soc/pcie@1cb0000000/pci@0,0/wifi@0,0"',
                       '"/soc/pcie@1cb0000000/pci@0,0/bluetooth@0,1"',
                       '"/soc/fifo@394b14000/input@8000/multi-touch"'):
            self.assertIn('target-path = ' + target + ';', overlay)


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j715-board.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        self.assertEqual(nodes['/']['compatible'], MODULE.J715)
        self.assertEqual(nodes['/soc/fifo@394b14000/input@8000/multi-touch']['firmware-name'],
                         b'apple/tpmtfw-j715.bin\0')


if __name__ == '__main__':
    unittest.main()
