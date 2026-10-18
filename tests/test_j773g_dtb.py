# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Offline build guards for the plain M4 Mac mini (J773g) boot devicetree."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('j773g_dtb', 'tools/build-j773g-dtb.py')
POWEROFF_TESTS = load('poweroff_fixtures', 'tests/test_j713_poweroff_dtb.py')
dtb, U32 = POWEROFF_TESTS.dtb, POWEROFF_TESTS.U32
OKAY, DISABLED = b'okay\0', b'disabled\0'
DART = b'apple,t8132-dart\0apple,t8110-dart\0'
PMGR = ('soc', 'power-management@380700000')
PCIE = ('soc', 'pcie@1cb0000000')
PORT0, PORT2 = PCIE + ('pci@0,0',), PCIE + ('pci@2,0',)


def key(path):
    """'/soc/x' -> ('soc', 'x'), the fixture's node key."""
    return tuple(part for part in path.split('/') if part)


def base_nodes():
    """The J713 front-USB devicetree's nodes that the J773g overlay touches or names."""
    return {
        (): {'compatible': MODULE.J713, 'model': b'Apple MacBook Air (13-inch, M4, 2025)\0'},
        ('aliases',): {'serial0': b'/soc/serial@3ad200000\0'},
        ('chosen',): {},
        ('chosen', 'framebuffer@0'): {'compatible': b'apple,simple-framebuffer\0simple-framebuffer\0',
                                      'power-domains': U32(0x6c, 0x6d), 'status': DISABLED},
        ('led-controller',): {'compatible': b'pwm-leds\0'},
        ('soc',): {},
        PMGR: {},
        PMGR + ('power-controller@1e0',): {'label': b'dispext0_fe\0', 'phandle': U32(0x0f)},
        PMGR + ('power-controller@10000',): {'label': b'disp_cpu\0', 'phandle': U32(0x6c)},
        PMGR + ('power-controller@3c8',): {'label': b'dptx_phy\0', 'phandle': U32(0x6d)},
        ('soc', 'iommu@490000000'): {'compatible': DART, 'reg': U32(4, 0x90000000, 0, 0x4000),
                                     '#iommu-cells': U32(1), 'interrupt-parent': U32(0x0d),
                                     'interrupts': U32(0, 1232, 4), 'power-domains': U32(0x65),
                                     'status': OKAY, 'phandle': U32(0x67)},
        PCIE: {'iommu-map': U32(0x100, 0x67, 1, 1, 0x101, 0x67, 2, 1), 'status': OKAY},
        PORT0: {'reset-gpios': U32(0x3f, 163, 1), 'bus-range': U32(1, 1)},
        PORT0 + ('wifi@0,0',): {'brcm,board-type': b'apple,garden\0'},
        PORT0 + ('bluetooth@0,1',): {'brcm,board-type': b'apple,garden\0'},
        PORT2: {'reg': U32(0x1000, 0, 0, 0, 0), 'status': DISABLED},
        ('soc', 'mtp@394600000'): {'status': OKAY, 'phandle': U32(0x3c)},
        ('soc', 'mbox@394608000'): {'status': OKAY, 'phandle': U32(0x39)},
        ('soc', 'iommu@394800000'): {'status': OKAY, 'phandle': U32(0x3a)},
        ('soc', 'fifo@394b14000'): {'status': OKAY, 'phandle': U32(0x3b)},
        ('soc', 'pwm@3ad044000'): {'status': OKAY, 'phandle': U32(0x113)},
    }


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
        self.overlay = self.root / 'j773g.dtso'
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

    def refused(self, candidate, message='beyond the J773g board wiring'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def base_refused(self, base, message):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(expected_dtb_nodes()), base)
        self.assertFalse(self.output.exists())

    def test_mini_wiring_built(self):
        receipt = self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(expected_dtb_nodes()))
        nodes = expected_dtb_nodes()
        self.assertEqual(nodes[()]['compatible'], b'apple,j773g\0apple,t8132\0apple,arm-platform\0')
        self.assertEqual(nodes[('chosen', 'framebuffer@0')]['power-domains'], U32(0x0f, 0x6d))
        self.assertEqual(nodes[('soc', 'iommu@492000000')]['phandle'], U32(0x114))
        self.assertEqual(nodes[PCIE]['iommu-map'][-16:], U32(0x200, 0x114, 1, 1))
        self.assertEqual(nodes[PORT2]['reset-gpios'], U32(0x3f, 165, 1))
        self.assertEqual(receipt['board'], 'apple,j773g')
        self.assertEqual(receipt['added_dart'], '/soc/iommu@492000000')
        for path in MODULE.J713_ONLY:
            self.assertEqual(nodes[key(path)]['status'], DISABLED)

    def test_j713_identity_kept_refused(self):
        self.refused(expected_dtb_nodes({(): {'compatible': MODULE.J713}}))

    def test_internal_panel_framebuffer_refused(self):
        self.refused(expected_dtb_nodes({('chosen', 'framebuffer@0'): {'power-domains': U32(0x6c, 0x6d)}}))

    def test_air_radios_refused(self):
        self.refused(expected_dtb_nodes({PORT0 + ('wifi@0,0',): {'brcm,board-type': b'apple,garden\0'}}))

    def test_keyboard_left_on_refused(self):
        for path in MODULE.J713_ONLY:
            with self.subTest(node=path):
                self.refused(expected_dtb_nodes({key(path): {'status': OKAY}}))

    def test_missing_ethernet_refused(self):
        self.refused(expected_dtb_nodes({PORT2 + ('ethernet@0,0',): None}))
        self.refused(expected_dtb_nodes({PORT2: {'status': DISABLED}}))

    def test_wrong_dart_refused(self):
        dart = ('soc', 'iommu@492000000')
        self.refused(expected_dtb_nodes({dart: {'interrupts': U32(0, 1232, 4)}}))
        self.refused(expected_dtb_nodes({PCIE: {'iommu-map': U32(0x100, 0x67, 1, 1, 0x101, 0x67, 2, 1,
                                                                  0x200, 0x67, 1, 1)}}))

    def test_wrong_perst_pin_refused(self):
        self.refused(expected_dtb_nodes({PORT2: {'reset-gpios': U32(0x3f, 163, 1)}}))

    def test_other_change_refused(self):
        self.refused(expected_dtb_nodes({('soc', 'iommu@490000000'): {'status': DISABLED}}))

    def test_wrong_board_refused(self):
        base = base_nodes()
        base[()]['compatible'] = b'apple,j415\0apple,t8132\0apple,arm-platform\0'
        self.base_refused(base, 'Wrong board')

    def test_moved_phandle_refused(self):
        base = base_nodes()
        base[PMGR + ('power-controller@1e0',)]['phandle'] = U32(0x99)
        self.base_refused(base, 'Unexpected phandle 0xf')

    def test_ethernet_already_wired_refused(self):
        base = base_nodes()
        base[('aliases',)]['ethernet0'] = b'/soc/x\0'
        self.base_refused(base, 'already wired')

    def test_port2_already_on_refused(self):
        base = base_nodes()
        base[PORT2]['status'] = OKAY
        self.base_refused(base, 'Unexpected PCIe port 2')

    def test_other_pcie_map_refused(self):
        base = base_nodes()
        base[PCIE]['iommu-map'] = U32(0x100, 0x67, 1, 1)
        self.base_refused(base, 'Unexpected PCIe map')

    def test_missing_j713_only_node_refused(self):
        base = base_nodes()
        base.pop(('soc', 'mtp@394600000'))
        self.base_refused(base, 'Unexpected J713-only node')

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

    def test_overlay_names_the_checked_phandles(self):
        overlay = (ROOT / 'patches/j773g-board.dtso').read_text()
        self.assertIn('power-domains = <0x0f>, <0x6d>;', overlay)
        self.assertIn('reset-gpios = <0x3f 165 1>;', overlay)
        self.assertIn('iommu-map = <0x100 0x67 1 1>, <0x101 0x67 2 1>, <0x200 &pcie2_dart 1 1>;', overlay)
        self.assertIn('interrupts = <0 1250 4>;', overlay)
        self.assertEqual(MODULE.PERST_PIN, 165)
        self.assertEqual(MODULE.DART_IRQ, 1250)


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j773g-board.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        self.assertEqual(nodes['/']['compatible'], MODULE.J773G)
        self.assertEqual(nodes['/soc/pcie@1cb0000000/pci@2,0']['status'], OKAY)


if __name__ == '__main__':
    unittest.main()
