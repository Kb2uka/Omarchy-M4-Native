# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Offline build guards for the J713 front USB-C port devicetree."""
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


MODULE = load('usb0_dtb', 'tools/build-j713-usb0-dtb.py')
POWEROFF_TESTS = load('poweroff_fixtures', 'tests/test_j713_poweroff_dtb.py')
dtb, U32 = POWEROFF_TESTS.dtb, POWEROFF_TESTS.U32
OKAY, DISABLED = b'okay\0', b'disabled\0'
DWC3 = b'apple,t8132-dwc3\0apple,t8103-dwc3\0'
DART = b'apple,t8132-dart\0apple,t8110-dart\0'
USB0, USB0_DARTS = ('soc', 'usb@402280000'), (('soc', 'iommu@402f00000'), ('soc', 'iommu@402f80000'))
USB1, USB1_DARTS = ('soc', 'usb@40a280000'), (('soc', 'iommu@40af00000'), ('soc', 'iommu@40af80000'))


def controller(status, iommus, domain, phy):
    return {'compatible': DWC3, 'status': status, 'iommus': iommus, 'power-domains': U32(domain),
            'resets': U32(phy), 'phys': U32(phy, 3, phy, 4), 'usb-role-switch': b''}


def base_nodes():
    """The boost-state devicetree's two USB ports, as fixture nodes."""
    nodes = {(): {'compatible': b'apple,j713\0apple,t8132\0apple,arm-platform\0'}, ('soc',): {}}
    nodes[USB0] = controller(DISABLED, U32(0x53, 1, 0x54, 14), 0x55, 0x56)
    nodes[USB1] = controller(OKAY, U32(0x5b, 0, 0x5c, 1), 0x5d, 0x5e)
    for darts, status, handles, domain in ((USB0_DARTS, DISABLED, (0x53, 0x54), 0x55),
                                           (USB1_DARTS, OKAY, (0x5b, 0x5c), 0x5d)):
        for dart, handle in zip(darts, handles):
            nodes[dart] = {'compatible': DART, '#iommu-cells': U32(1), 'power-domains': U32(domain),
                           'status': status, 'phandle': U32(handle)}
    nodes[USB0 + ('ports',)] = {}
    nodes[USB1 + ('ports',)] = {}
    return nodes


def expected_dtb_nodes(changes=None):
    """The candidate as fixture nodes: USB0 and its DARTs on, with USB1's streams."""
    nodes = base_nodes()
    nodes[USB0] = dict(nodes[USB0], status=OKAY, iommus=U32(0x53, 0, 0x54, 1))
    for dart in USB0_DARTS:
        nodes[dart] = dict(nodes[dart], status=OKAY)
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
        self.overlay = self.root / 'usb0.dtso'
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

    def refused(self, candidate, message='beyond the front USB port'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def base_refused(self, base, message):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(expected_dtb_nodes()), base)
        self.assertFalse(self.output.exists())

    def test_front_port_enabled_like_the_hinge_port(self):
        receipt = self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(expected_dtb_nodes()))
        self.assertEqual(receipt['usb0_streams'], {'before': [1, 14], 'after': [0, 1]})
        self.assertEqual(receipt['enabled'], ['/soc/usb@402280000', '/soc/iommu@402f00000', '/soc/iommu@402f80000'])
        nodes = expected_dtb_nodes()
        for front, hinge in ((USB0, USB1), *zip(USB0_DARTS, USB1_DARTS)):
            self.assertEqual(nodes[front]['status'], nodes[hinge]['status'])
        streams = lambda node: struct.unpack('>4I', nodes[node]['iommus'])[1::2]
        self.assertEqual(streams(USB0), streams(USB1), 'the front port uses the hinge port streams')

    def test_controller_left_off_refused(self):
        self.refused(expected_dtb_nodes({USB0: {'status': DISABLED}}))

    def test_dart_left_off_refused(self):
        for dart in USB0_DARTS:
            with self.subTest(dart=dart):
                self.refused(expected_dtb_nodes({dart: {'status': DISABLED}}))

    def test_old_streams_refused(self):
        self.refused(expected_dtb_nodes({USB0: {'iommus': U32(0x53, 1, 0x54, 14)}}))

    def test_hinge_port_darts_refused(self):
        self.refused(expected_dtb_nodes({USB0: {'iommus': U32(0x5b, 0, 0x5c, 1)}}))

    def test_hinge_port_change_refused(self):
        self.refused(expected_dtb_nodes({USB1: {'iommus': U32(0x5b, 1, 0x5c, 14)}}))
        self.refused(expected_dtb_nodes({USB1: {'status': DISABLED}}))

    def test_forced_role_refused(self):
        self.refused(expected_dtb_nodes({USB0: {'dr_mode': b'host\0'}}))

    def test_moved_connector_graph_refused(self):
        self.refused(expected_dtb_nodes({USB0 + ('ports',): None}))

    def test_base_with_front_port_already_on_refused(self):
        self.base_refused(expected_dtb_nodes(), 'Unexpected usb0 status')

    def test_base_with_other_front_streams_refused(self):
        base = base_nodes()
        base[USB0]['iommus'] = U32(0x53, 0, 0x54, 1)
        self.base_refused(base, 'Unexpected usb0 streams')

    def test_base_with_hinge_port_off_refused(self):
        base = base_nodes()
        base[USB1_DARTS[1]]['status'] = DISABLED
        self.base_refused(base, 'Unexpected usb1 status')

    def test_base_whose_dart_phandle_moved_refused(self):
        base = base_nodes()
        base[USB0_DARTS[0]]['phandle'] = U32(0x99)
        self.base_refused(base, 'Unexpected usb0 streams')

    def test_base_with_different_controller_layout_refused(self):
        base = base_nodes()
        base[USB0]['dr_mode'] = b'otg\0'
        self.base_refused(base, 'same controller layout')

    def test_base_sharing_the_hinge_port_phy_refused(self):
        base = base_nodes()
        base[USB0]['phys'] = base[USB1]['phys']
        self.base_refused(base, 'same controller layout')

    def test_wrong_board_refused(self):
        base = base_nodes()
        base[()]['compatible'] = b'apple,j415\0apple,t8132\0'
        self.base_refused(base, 'Wrong board')

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Unqualified boost-state base DTB'):
                MODULE.build(self.base, self.output, self.overlay)
        run.assert_not_called()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), b'keep')


class Lineage(unittest.TestCase):
    def test_base_is_the_boost_devicetree_and_the_lineage_knows_the_result(self):
        updater = load('updater', 'tools/m4-j713-update-boot-dtb.py')
        self.assertEqual(MODULE.BASE_SHA256, updater.PBOOST_DTB)
        self.assertEqual(updater.UPDATES[updater.USB0_DTB], ((updater.PBOOST_DTB,), '.pre-usb0'))
        compose = load('composer', 'tools/m4-boot-compose.py')
        self.assertEqual(list(compose.KNOWN_DTBS)[-2:], [updater.PBOOST_DTB, updater.USB0_DTB])
        self.assertEqual(compose.KNOWN_DTBS[updater.USB0_DTB], 'j713-usb0')

    def test_overlay_names_the_front_port_darts_and_the_hinge_streams(self):
        overlay = (ROOT / 'patches/j713-usb0.dtso').read_text()
        self.assertIn('iommus = <0x53 0>, <0x54 1>;', overlay)
        self.assertEqual(overlay.count('status = "okay";'), 3)
        self.assertNotIn('dr_mode', overlay)
        self.assertEqual(MODULE.STREAMS, (0, 1))


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j713-usb0.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        self.assertEqual(nodes['/soc/usb@402280000']['status'], OKAY)


if __name__ == '__main__':
    unittest.main()
