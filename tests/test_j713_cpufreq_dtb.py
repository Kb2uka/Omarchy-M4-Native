"""Offline build guards for the J713 bounded cpufreq devicetree."""
import importlib.util
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/build-j713-cpufreq-dtb.py'
SPEC = importlib.util.spec_from_file_location('cpufreq_dtb', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
FIXTURES = importlib.util.spec_from_file_location('poweroff_fixtures', ROOT / 'tests/test_j713_poweroff_dtb.py')
POWEROFF_TESTS = importlib.util.module_from_spec(FIXTURES)
FIXTURES.loader.exec_module(POWEROFF_TESTS)
U32 = MODULE.U32
# The states iBoot leaves the clusters in, read from DVFS_CMD on the owner's Air.
HANDOFF_HZ = {'e': 900000000, 'p': 1800000000}


def path(text):
    return tuple(part for part in text.split('/') if part)


def base_nodes():
    nodes = {(): {'compatible': b'apple,j713\0apple,t8132\0apple,arm-platform\0'},
             ('cpus',): {'#address-cells': U32(2), '#size-cells': U32(0)},
             ('soc',): {'#address-cells': U32(2), '#size-cells': U32(2)}}
    handle = 1
    for _, _, _, _, _, core, cpus in MODULE.CLUSTERS.values():
        for cpu in cpus:
            nodes[path(cpu)] = {'compatible': core, 'device_type': b'cpu\0', 'phandle': U32(handle)}
            handle += 1
    return nodes


def candidate_nodes(changes=None, handles=(0x10d, 0x10e, 0x10f, 0x110)):
    nodes = base_nodes()
    for index, (controller, base, table, opps, capacity, _, cpus) in enumerate(MODULE.CLUSTERS.values()):
        domain, opp = U32(handles[2 * index]), U32(handles[2 * index + 1])
        nodes[path(controller)] = {'compatible': MODULE.COMPATIBLE, 'reg': U32(base >> 32, base & 0xffffffff, 0, 0x1000),
                                   '#performance-domain-cells': U32(0), 'phandle': domain}
        nodes[path(table)] = {'compatible': b'operating-points-v2\0', 'opp-shared': b'', 'phandle': opp}
        for hz, level in opps:
            nodes[path(f'{table}/opp{level:02d}')] = {'opp-hz': struct.pack('>Q', hz), 'opp-level': U32(level)}
        for cpu in cpus:
            nodes[path(cpu)].update({'operating-points-v2': opp, 'performance-domains': domain,
                                     'capacity-dmips-mhz': U32(capacity)})
    for name, props in (changes or {}).items():
        if props is None:
            nodes.pop(name)
        else:
            nodes[name] = dict(nodes.get(name, {}), **props)
    return nodes


dtb = POWEROFF_TESTS.dtb


class BuildGuards(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = self.root / 'base.dtb'
        self.base.write_bytes(dtb(base_nodes()))
        self.overlay = self.root / 'cpufreq.dtso'
        self.overlay.write_bytes(b'source fixture')
        self.output = self.root / 'out.dtb'

    def build(self, candidate, base=None):
        def compile_only(command, check):
            Path(command[command.index('-o') + 1]).write_bytes(
                candidate if command[0] == 'fdtoverlay' else b'overlay')
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run', side_effect=compile_only):
            return MODULE.build(self.base, self.output, self.overlay)

    def refused(self, candidate, message='beyond the cpufreq clusters'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def test_clusters_tables_and_capacities_added(self):
        receipt = self.build(dtb(candidate_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(candidate_nodes()))
        self.assertEqual(receipt['clusters']['e']['max_mhz'], 900)
        self.assertEqual(receipt['clusters']['p']['max_mhz'], 1800)
        self.assertEqual(receipt['clusters']['e']['capacity_dmips_mhz'], 663)
        self.assertFalse(receipt['hardware_scaling_verified'])

    def test_state_above_handoff_refused(self):
        self.refused(candidate_nodes({path('/opp-table-1/opp05'): {
            'opp-hz': struct.pack('>Q', 2088000000), 'opp-level': U32(5)}}))

    def test_faster_label_on_handoff_state_refused(self):
        self.refused(candidate_nodes({path('/opp-table-0/opp01'): {'opp-hz': struct.pack('>Q', 1080000000)}}))

    def test_copied_capacity_refused(self):
        self.refused(candidate_nodes({path('/cpus/cpu@3'): {'capacity-dmips-mhz': U32(804)}}))

    def test_wrong_performance_domain_refused(self):
        self.refused(candidate_nodes({path('/cpus/cpu@10100'): {'performance-domains': U32(0x10d)}}))

    def test_cpu_left_without_scaling_refused(self):
        nodes = candidate_nodes()
        for name in ('operating-points-v2', 'performance-domains', 'capacity-dmips-mhz'):
            nodes[path('/cpus/cpu@5')].pop(name)
        self.refused(nodes)

    def test_reused_phandle_refused(self):
        self.refused(candidate_nodes(handles=(1, 0x10e, 0x10f, 0x110)))

    def test_other_property_change_refused(self):
        self.refused(candidate_nodes({path('/soc'): {'status': b'disabled\0'}}))

    def test_generic_compatible_refused(self):
        self.refused(candidate_nodes({path('/soc/cpufreq@210e20000'): {'compatible': b'apple,cluster-cpufreq\0'}}))

    def test_existing_cpufreq_base_refused(self):
        self.base.write_bytes(dtb(candidate_nodes()))
        with self.assertRaisesRegex(ValueError, 'Unexpected CPU node|already exist'):
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


class Tables(unittest.TestCase):
    def test_tables_stop_at_the_handoff_states(self):
        for name, cluster in MODULE.CLUSTERS.items():
            self.assertEqual(max(hz for hz, _ in cluster[3]), HANDOFF_HZ[name])
            self.assertEqual([level for _, level in cluster[3]], list(range(1, len(cluster[3]) + 1)))

    def test_capacity_matches_the_measured_ratio(self):
        # E did 0.324x a P core's work at 900 vs 1800 MHz.
        e, p = MODULE.CLUSTERS['e'], MODULE.CLUSTERS['p']
        ratio = e[4] * e[3][-1][0] / (p[4] * p[3][-1][0])
        self.assertAlmostEqual(ratio, 0.324, places=3)


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j713-cpufreq.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        for name, cluster in MODULE.CLUSTERS.items():
            rates = [struct.unpack('>Q', props['opp-hz'])[0] for node, props in nodes.items()
                     if node.startswith(cluster[2] + '/')]
            self.assertEqual(max(rates), HANDOFF_HZ[name])


if __name__ == '__main__':
    unittest.main()
