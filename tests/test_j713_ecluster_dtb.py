"""Offline build guards for the J713 E-cluster and CPU thermal zone devicetree (#66)."""
import importlib.util
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ecluster_dtb', ROOT / 'tools/build-j713-ecluster-dtb.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FIXTURES = importlib.util.spec_from_file_location('poweroff_fixtures', ROOT / 'tests/test_j713_poweroff_dtb.py')
POWEROFF_TESTS = importlib.util.module_from_spec(FIXTURES)
FIXTURES.loader.exec_module(POWEROFF_TESTS)
dtb = POWEROFF_TESTS.dtb
U32 = lambda *values: struct.pack('>' + 'I' * len(values), *values)
HWMON = ('soc', 'smc@38c600000', 'hwmon')
E_TABLE, P_TABLE = 11, 12
# The fixture writer emits children sorted by name; these names keep the base's channel
# order, in which TCMz is the seventh temperature channel (index 6).
CHANNEL_KEYS = ('Te0S', 'Te05', 'Tp0D', 'Tp09', 'Tp05', 'Tp01', 'TCMz')


def base_nodes():
    nodes = {(): {'compatible': b'apple,j713\0apple,t8132\0apple,arm-platform\0'},
             ('cpus',): {'#address-cells': U32(2), '#size-cells': U32(0)}}
    for n in range(6):
        nodes[('cpus', f'cpu@{n}')] = {'operating-points-v2': U32(E_TABLE), 'phandle': U32(1 + n)}
    for n in range(4):
        nodes[('cpus', f'cpu@1010{n}')] = {'operating-points-v2': U32(P_TABLE), 'phandle': U32(7 + n)}
    nodes[('opp-table-0',)] = {'phandle': U32(E_TABLE), 'compatible': b'operating-points-v2\0'}
    nodes[('opp-table-0', 'opp01')] = {'opp-level': U32(1), 'opp-hz': struct.pack('>Q', 900000000)}
    nodes[('opp-table-1',)] = {'phandle': U32(P_TABLE), 'compatible': b'operating-points-v2\0'}
    nodes[('opp-table-1', 'opp04')] = {'opp-level': U32(4), 'opp-hz': struct.pack('>Q', 1800000000)}
    nodes[('soc',)] = {'#address-cells': U32(2), '#size-cells': U32(2)}
    nodes[('soc', 'smc@38c600000')] = {'compatible': b'apple,t8132-smc\0apple,t8103-smc\0'}
    nodes[HWMON] = {'compatible': b'apple,smc-hwmon\0'}
    for index, key in enumerate(CHANNEL_KEYS):
        nodes[HWMON + (f'temperature-{index}',)] = {'apple,key-id': key.encode() + b'\0'}
    return nodes


def expected_dtb_nodes(changes=None):
    """The candidate as fixture nodes: the builder's expectation, optionally changed."""
    old, _, _ = MODULE.tree(dtb(base_nodes()))
    expected = MODULE.expected_nodes(old)
    nodes = {tuple(p for p in name.split('/') if p): dict(props) for name, props in expected.items()}
    for name, props in (changes or {}).items():
        if props is None:
            nodes.pop(name)
        else:
            nodes[name] = dict(nodes.get(name, {}), **props)
    return nodes


ZONE = ('thermal-zones', 'cpu-die-thermal')


class BuildGuards(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'base.dtb'
        self.base.write_bytes(dtb(base_nodes()))
        self.overlay = self.root / 'ecluster.dtso'
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

    def refused(self, candidate, message='beyond the E-cluster states and the CPU thermal zone'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def base_refused(self, base, message):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(expected_dtb_nodes()), base)
        self.assertFalse(self.output.exists())

    def test_states_and_zone_added(self):
        receipt = self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(expected_dtb_nodes()))
        self.assertEqual(receipt['e_states_mhz'], [900, 1080, 1476, 1860, 2172])
        self.assertIsNone(receipt['thermal_zone']['critical_trip'])
        self.assertFalse(receipt['p_states_changed'])

    def test_zone_reads_cpu_die_max_and_cools_both_policy_cpus(self):
        nodes = expected_dtb_nodes()
        sensor = struct.unpack('>I', nodes[HWMON]['phandle'])[0]
        self.assertEqual(nodes[ZONE]['thermal-sensors'], U32(sensor, 6))
        trip = struct.unpack('>I', nodes[ZONE + ('trips', 'cpu-die-passive')]['phandle'])[0]
        self.assertEqual(nodes[ZONE + ('trips', 'cpu-die-passive')]['type'], b'passive\0')
        cooled = {nodes[ZONE + ('cooling-maps', m)]['cooling-device'][:4]: nodes[ZONE + ('cooling-maps', m)]['trip']
                  for m in ('map-p-cluster', 'map-e-cluster')}
        self.assertEqual(cooled, {U32(7): U32(trip), U32(1): U32(trip)})
        self.assertEqual(len({sensor, trip, *range(1, 13)}), 14, 'new phandles must be unique')

    def test_no_critical_trip(self):
        self.refused(expected_dtb_nodes({ZONE + ('trips', 'cpu-die-critical'): {
            'temperature': U32(105000), 'hysteresis': U32(0), 'type': b'critical\0'}}))

    def test_changed_trip_temperature_refused(self):
        self.refused(expected_dtb_nodes({ZONE + ('trips', 'cpu-die-passive'): {'temperature': U32(105000)}}))

    def test_extra_e_state_refused(self):
        self.refused(expected_dtb_nodes({('opp-table-0', 'opp06'): {
            'opp-level': U32(6), 'opp-hz': struct.pack('>Q', 2496000000)}}))

    def test_p_state_change_refused(self):
        self.refused(expected_dtb_nodes({('opp-table-1', 'opp05'): {
            'opp-level': U32(5), 'opp-hz': struct.pack('>Q', 2088000000)}}))

    def test_capacity_change_refused(self):
        self.refused(expected_dtb_nodes({('cpus', 'cpu@0'): {'capacity-dmips-mhz': U32(800)}}))

    def test_wrong_sensor_channel_refused(self):
        nodes = expected_dtb_nodes()
        sensor = struct.unpack('>I', nodes[HWMON]['phandle'])[0]
        self.refused(expected_dtb_nodes({ZONE: {'thermal-sensors': U32(sensor, 0)}}))

    def test_missing_cooling_cells_refused(self):
        changed = expected_dtb_nodes()
        del changed[('cpus', 'cpu@10103')]['#cooling-cells']
        self.refused(changed)

    def test_base_whose_channel_six_is_not_tcmz_refused(self):
        base = base_nodes()
        base[HWMON + ('temperature-6',)] = {'apple,key-id': b'Tp02\0'}
        self.base_refused(base, 'CPU Die Max is not temperature channel 6')

    def test_base_whose_policy_cpu_phandle_moved_refused(self):
        base = base_nodes()
        base[('cpus', 'cpu@10100')]['phandle'] = U32(20)
        self.base_refused(base, 'Policy CPU phandle moved')

    def test_base_with_e_states_already_refused(self):
        base = base_nodes()
        base[('opp-table-0', 'opp02')] = {'opp-level': U32(2), 'opp-hz': struct.pack('>Q', 1080000000)}
        self.base_refused(base, 'Unexpected E OPP table')

    def test_base_with_thermal_zones_refused(self):
        base = base_nodes()
        base[('thermal-zones',)] = {}
        self.base_refused(base, 'Thermal zones already exist')

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Unqualified CPU temperature base DTB'):
                MODULE.build(self.base, self.output, self.overlay)
        run.assert_not_called()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), b'keep')

    def test_base_is_the_cpu_temperature_devicetree_and_the_lineage_knows_the_result(self):
        updater = importlib.util.spec_from_file_location('updater', ROOT / 'tools/m4-j713-update-boot-dtb.py')
        module = importlib.util.module_from_spec(updater)
        updater.loader.exec_module(module)
        self.assertEqual(MODULE.BASE_SHA256, module.CPUTEMP_DTB)
        self.assertEqual(module.UPDATES[module.ECLUSTER_DTB], ((module.CPUTEMP_DTB,), '.pre-ecluster'))
        composer = importlib.util.spec_from_file_location('composer', ROOT / 'tools/m4-boot-compose.py')
        compose = importlib.util.module_from_spec(composer)
        composer.loader.exec_module(compose)
        lineage = list(compose.KNOWN_DTBS)
        self.assertEqual(lineage.index(module.ECLUSTER_DTB), lineage.index(module.CPUTEMP_DTB) + 1)

    def test_e_states_are_the_adt_table(self):
        self.assertEqual(MODULE.E_STATES, {2: 1080, 3: 1476, 4: 1860, 5: 2172})
        overlay = (ROOT / 'patches/j713-ecluster-thermal.dtso').read_text()
        for state, mhz in MODULE.E_STATES.items():
            self.assertIn(f'opp-hz = /bits/ 64 <{mhz}000000>;\n\t\t\t\topp-level = <{state}>;', overlay)


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j713-ecluster-thermal.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        self.assertEqual(sorted(p for p in nodes if p.startswith('/opp-table-0/')),
                         [f'/opp-table-0/opp0{n}' for n in range(1, 6)])


if __name__ == '__main__':
    unittest.main()
