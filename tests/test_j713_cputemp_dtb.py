"""Offline build guards for the J713 CPU temperature devicetree."""
import importlib.util
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/build-j713-cputemp-dtb.py'
SPEC = importlib.util.spec_from_file_location('cputemp_dtb', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FIXTURES = importlib.util.spec_from_file_location('poweroff_fixtures', ROOT / 'tests/test_j713_poweroff_dtb.py')
POWEROFF_TESTS = importlib.util.module_from_spec(FIXTURES)
FIXTURES.loader.exec_module(POWEROFF_TESTS)
U32 = lambda *values: struct.pack('>' + 'I' * len(values), *values)
HWMON = ('soc', 'smc@38c600000', 'hwmon')
# Keys chosen from the 2026-10-13 survey of the owner's J713 SMC.
SURVEYED = {'TCMz', 'Tp01', 'Tp05', 'Tp09', 'Tp0D', 'Te05', 'Te0S'}


def string(text):
    return text.encode() + b'\0'


def base_nodes():
    return {(): {'compatible': b'apple,j713\0apple,t8132\0apple,arm-platform\0'},
            ('soc',): {'#address-cells': U32(2), '#size-cells': U32(2)},
            ('soc', 'smc@38c600000'): {'compatible': b'apple,t8132-smc\0apple,t8103-smc\0'},
            HWMON: {'compatible': b'apple,smc-hwmon\0'},
            HWMON + ('temperature-TB0T',): {'apple,key-id': string('TB0T'), 'label': string('Battery Hotspot')},
            ('opp-table-1',): {'compatible': b'operating-points-v2\0', 'opp-shared': b''},
            ('opp-table-1', 'opp04'): {'opp-hz': struct.pack('>Q', 1800000000), 'opp-level': U32(4)}}


def candidate_nodes(changes=None):
    nodes = base_nodes()
    for key, label in MODULE.CHANNELS.items():
        nodes[HWMON + (f'temperature-{key}',)] = {'apple,key-id': string(key), 'label': string(label)}
    for name, props in (changes or {}).items():
        if props is None:
            nodes.pop(name)
        else:
            nodes[name] = dict(nodes.get(name, {}), **props)
    return nodes


dtb = POWEROFF_TESTS.dtb


def with_boot_cpu(data, cpu):
    return data[:28] + U32(cpu) + data[32:]


def with_reservation(data, address, size):
    """Insert one memory reservation before the terminator; later sections move 16 bytes."""
    total, start, strings = struct.unpack_from('>3I', data, 4)
    header = data[:4] + U32(total + 16, start + 16, strings + 16) + data[16:40]
    return header + struct.pack('>QQ', address, size) + data[40:]


class BuildGuards(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'base.dtb'
        self.base.write_bytes(dtb(base_nodes()))
        self.overlay = self.root / 'cputemp.dtso'
        self.overlay.write_bytes(b'source fixture')
        self.output = self.root / 'out.dtb'

    def build(self, candidate):
        def compile_only(command, check):
            Path(command[command.index('-o') + 1]).write_bytes(
                candidate if command[0] == 'fdtoverlay' else b'overlay')
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run', side_effect=compile_only):
            return MODULE.build(self.base, self.output, self.overlay)

    def refused(self, candidate, message='beyond the CPU temperature channels'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def test_channels_added(self):
        receipt = self.build(dtb(candidate_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(candidate_nodes()))
        self.assertEqual([c['key'] for c in receipt['channels']], list(MODULE.CHANNELS))
        self.assertFalse(receipt['clocks_changed'])

    def test_extra_channel_refused(self):
        self.refused(candidate_nodes({HWMON + ('temperature-Tp02',): {
            'apple,key-id': string('Tp02'), 'label': string('P-cluster Temperature 5')}}))

    def test_missing_channel_refused(self):
        self.refused(candidate_nodes({HWMON + ('temperature-Te0S',): None}))

    def test_wrong_key_refused(self):
        self.refused(candidate_nodes({HWMON + ('temperature-Tp01',): {'apple,key-id': string('Tp02')}}))

    def test_wrong_label_refused(self):
        self.refused(candidate_nodes({HWMON + ('temperature-TCMz',): {'label': string('CPU')}}))

    def test_clock_change_refused(self):
        self.refused(candidate_nodes({('opp-table-1', 'opp04'): {'opp-hz': struct.pack('>Q', 2088000000)}}))

    def test_other_property_change_refused(self):
        self.refused(candidate_nodes({('soc',): {'status': b'disabled\0'}}))

    def test_boot_cpu_change_refused(self):
        with self.assertRaisesRegex(ValueError, 'beyond the CPU temperature channels'):
            self.build(with_boot_cpu(dtb(candidate_nodes()), 1))
        self.assertFalse(self.output.exists())

    def test_reservation_change_refused(self):
        with self.assertRaisesRegex(ValueError, 'beyond the CPU temperature channels'):
            self.build(with_reservation(dtb(candidate_nodes()), 0x10000000000, 0x4000))
        self.assertFalse(self.output.exists())

    def test_fixture_variants_parse(self):
        # Guards against the two tests above passing only because the fixture is malformed.
        nodes, reservations, cpu = MODULE.tree(with_reservation(with_boot_cpu(dtb(candidate_nodes()), 1),
                                                                0x10000000000, 0x4000))
        self.assertEqual(cpu, 1)
        self.assertEqual(reservations, [(0x10000000000, 0x4000), (0, 0)])
        self.assertIn('/soc/smc@38c600000/hwmon/temperature-TCMz', nodes)

    def test_existing_channel_base_refused(self):
        self.base.write_bytes(dtb(candidate_nodes()))
        with self.assertRaisesRegex(ValueError, 'already exist'):
            self.build(dtb(candidate_nodes()))

    def test_key_already_reported_under_another_name_refused(self):
        nodes = base_nodes()
        nodes[HWMON + ('temperature-cpu',)] = {'apple,key-id': string('TCMz'), 'label': string('CPU')}
        self.base.write_bytes(dtb(nodes))
        with self.assertRaisesRegex(ValueError, 'already exist'):
            self.build(dtb(candidate_nodes()))

    def test_missing_hwmon_refused(self):
        nodes = base_nodes()
        nodes[HWMON] = {'compatible': b'apple,smc-rtc\0'}
        self.base.write_bytes(dtb(nodes))
        with self.assertRaisesRegex(ValueError, 'Unexpected SMC hwmon node'):
            self.build(dtb(candidate_nodes()))

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


class Channels(unittest.TestCase):
    def test_channels_are_the_surveyed_keys(self):
        self.assertEqual(set(MODULE.CHANNELS), SURVEYED)
        self.assertEqual(next(iter(MODULE.CHANNELS)), 'TCMz')

    def test_labels_fit_the_driver(self):
        # macsmc-hwmon copies labels into a 32-byte buffer.
        for key, label in MODULE.CHANNELS.items():
            self.assertEqual(len(key), 4)
            self.assertLess(len(label), 32)

    def test_base_is_the_cpufreq_devicetree(self):
        updater = importlib.util.spec_from_file_location('updater', ROOT / 'tools/m4-j713-update-boot-dtb.py')
        module = importlib.util.module_from_spec(updater)
        updater.loader.exec_module(module)
        self.assertEqual(MODULE.BASE_SHA256, module.CPUFREQ_DTB)


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j713-cpu-temperature.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        keys = {props['apple,key-id'][:-1].decode() for node, props in nodes.items()
                if node.startswith('/soc/smc@38c600000/hwmon/temperature-')}
        self.assertEqual(keys, SURVEYED | {'TB0T'})


if __name__ == '__main__':
    unittest.main()
