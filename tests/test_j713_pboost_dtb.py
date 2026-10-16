# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Offline build guards for the J713 boost-state devicetree and its boost gate (#66)."""
import importlib.util
from pathlib import Path
import re
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


MODULE = load('pboost_dtb', 'tools/build-j713-pboost-dtb.py')
ECLUSTER_TESTS = load('ecluster_fixtures', 'tests/test_j713_ecluster_dtb.py')
dtb, U32 = ECLUSTER_TESTS.dtb, ECLUSTER_TESTS.U32
ZONE = ('thermal-zones', 'cpu-die-thermal')
GATE = ZONE + ('trips', 'boost-gate')
GATE_MAP = ZONE + ('cooling-maps', 'map-p-boost-gate')


def base_nodes():
    """The E-cluster devicetree as fixture nodes: the E-cluster builder's output, full P table."""
    nodes = ECLUSTER_TESTS.expected_dtb_nodes()
    for state, mhz in MODULE.P_BASE.items():
        nodes[('opp-table-1', f'opp{state:02d}')] = {'opp-level': U32(state),
                                                      'opp-hz': struct.pack('>Q', mhz * 1000000)}
    return nodes


def expected_dtb_nodes(changes=None):
    """The candidate as fixture nodes: the builder's expectation, optionally changed."""
    old, _, _ = MODULE.tree(dtb(base_nodes()))
    nodes = {tuple(p for p in name.split('/') if p): dict(props)
             for name, props in MODULE.expected_nodes(old).items()}
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
        self.overlay = self.root / 'pboost.dtso'
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

    def refused(self, candidate, message='beyond the boost states and the boost gate'):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(candidate))
        self.assertFalse(self.output.exists())

    def base_refused(self, base, message):
        with self.assertRaisesRegex(ValueError, message):
            self.build(dtb(expected_dtb_nodes()), base)
        self.assertFalse(self.output.exists())

    def test_states_and_gate_added(self):
        receipt = self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), dtb(expected_dtb_nodes()))
        self.assertEqual(receipt['p_states_mhz'][-1], 4464)
        self.assertEqual(len(receipt['p_states_mhz']), 20)
        self.assertEqual(receipt['e_states_mhz'], [900, 1080, 1476, 1860, 2172, 2496, 2808])
        self.assertEqual(receipt['boost_gate']['p_mhz_while_closed'], 1800)

    def test_closed_gate_holds_the_p_cluster_at_1800_mhz(self):
        nodes = expected_dtb_nodes()
        gate = struct.unpack('>I', nodes[GATE]['phandle'])[0]
        self.assertEqual(nodes[GATE]['type'], b'passive\0')
        self.assertEqual(nodes[GATE]['temperature'], U32(20000), 'below any CPU die reading')
        self.assertEqual(nodes[GATE]['hysteresis'], U32(0))
        self.assertEqual(nodes[GATE_MAP]['trip'], U32(gate))
        self.assertEqual(nodes[GATE_MAP]['cooling-device'], U32(7, 16, 16), 'the P policy CPU, pinned')
        # cpufreq cooling state N is N states below the top of the policy's table.
        table = sorted(struct.unpack('>Q', props['opp-hz'])[0]
                       for name, props in nodes.items() if name[:1] == ('opp-table-1',) and len(name) == 2)
        self.assertEqual(len(table), 20)
        self.assertEqual(table[-1 - 16], 1800000000)
        self.assertEqual(len({gate, *range(1, 15)}), 15, 'the gate phandle is new')

    def test_the_95_c_trip_stays_for_both_clusters(self):
        nodes = expected_dtb_nodes()
        self.assertEqual(nodes[ZONE + ('trips', 'cpu-die-passive')]['temperature'], U32(95000))
        for name in ('map-p-cluster', 'map-e-cluster'):
            self.assertIn(ZONE + ('cooling-maps', name), nodes)

    def test_open_gate_refused(self):
        self.refused(expected_dtb_nodes({GATE: {'temperature': U32(150000)}}))

    def test_gate_pinning_another_state_refused(self):
        self.refused(expected_dtb_nodes({GATE_MAP: {'cooling-device': U32(7, 15, 15)}}))
        self.refused(expected_dtb_nodes({GATE_MAP: {'cooling-device': U32(7, 0, 16)}}))

    def test_missing_gate_refused(self):
        self.refused(expected_dtb_nodes({GATE_MAP: None}))

    def test_e8_refused(self):
        self.refused(expected_dtb_nodes({('opp-table-0', 'opp08'): {
            'opp-level': U32(8), 'opp-hz': struct.pack('>Q', 2892000000)}}))

    def test_turbo_states_refused(self):
        self.refused(expected_dtb_nodes({('opp-table-1', 'opp20'): {'turbo-mode': b''}}))

    def test_changed_95_c_trip_refused(self):
        self.refused(expected_dtb_nodes({ZONE + ('trips', 'cpu-die-passive'): {'temperature': U32(105000)}}))

    def test_capacity_change_refused(self):
        self.refused(expected_dtb_nodes({('cpus', 'cpu@10100'): {'capacity-dmips-mhz': U32(2048)}}))

    def test_base_with_boost_states_already_refused(self):
        base = base_nodes()
        base[('opp-table-1', 'opp05')] = {'opp-level': U32(5), 'opp-hz': struct.pack('>Q', 2088000000)}
        self.base_refused(base, 'Unexpected P OPP table')

    def test_base_without_the_e_cluster_states_refused(self):
        base = base_nodes()
        del base[('opp-table-0', 'opp05')]
        self.base_refused(base, 'Unexpected E OPP table')

    def test_base_without_the_thermal_zone_refused(self):
        base = {name: props for name, props in base_nodes().items() if name[:1] != ('thermal-zones',)}
        self.base_refused(base, 'No CPU die thermal zone')

    def test_base_with_another_trip_refused(self):
        base = base_nodes()
        base[GATE] = {'temperature': U32(20000), 'hysteresis': U32(0), 'type': b'passive\0'}
        self.base_refused(base, 'Unexpected CPU die trips')

    def test_base_whose_policy_cpu_phandle_moved_refused(self):
        base = base_nodes()
        base[('cpus', 'cpu@10100')]['phandle'] = U32(30)
        self.base_refused(base, 'Policy CPU phandle moved')

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'Unqualified E-cluster base DTB'):
                MODULE.build(self.base, self.output, self.overlay)
        run.assert_not_called()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'keep')
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.build(dtb(expected_dtb_nodes()))
        self.assertEqual(self.output.read_bytes(), b'keep')


class Lineage(unittest.TestCase):
    def test_base_is_the_ecluster_devicetree_and_the_lineage_knows_the_result(self):
        updater = load('updater', 'tools/m4-j713-update-boot-dtb.py')
        self.assertEqual(MODULE.BASE_SHA256, updater.ECLUSTER_DTB)
        self.assertEqual(updater.UPDATES[updater.PBOOST_DTB], ((updater.ECLUSTER_DTB,), '.pre-pboost'))
        compose = load('composer', 'tools/m4-boot-compose.py')
        lineage = list(compose.KNOWN_DTBS)
        self.assertEqual(lineage.index(updater.PBOOST_DTB), lineage.index(updater.ECLUSTER_DTB) + 1)
        self.assertEqual(compose.KNOWN_DTBS[updater.PBOOST_DTB], 'j713-pboost')

    def test_states_are_the_adt_tables(self):
        self.assertEqual(MODULE.P_BASE, {1: 912, 2: 1260, 3: 1512, 4: 1800})
        self.assertEqual(list(MODULE.P_STATES), list(range(5, 21)))
        self.assertEqual(MODULE.E_STATES, {6: 2496, 7: 2808})
        self.assertEqual(MODULE.E_BASE, ECLUSTER_TESTS.MODULE.E_STATES | {1: 900})
        overlay = (ROOT / 'patches/j713-pboost.dtso').read_text()
        for state, mhz in (MODULE.P_STATES | MODULE.E_STATES).items():
            self.assertIn(f'opp{state:02d} {{ opp-hz = /bits/ 64 <{mhz}000000>; opp-level = <{state}>; }};', overlay)

    def test_guard_module_agrees_with_the_devicetree(self):
        source = (ROOT / 'cpu/j713/j713_pguard.c').read_text()
        policy = (ROOT / 'cpu/j713/j713_pguard_policy.h').read_text()
        self.assertIn(f'#define GATE_CLOSED_MC {MODULE.GATE_MILLI_C}\n', source)
        self.assertIn('#define GATE_ZONE "cpu-die-thermal"', source)
        self.assertIn(f'#define SUSTAIN_MC {ECLUSTER_TESTS.MODULE.TRIP_MILLI_C}\t', source,
                      'the guard raises the 95 C trip the E-cluster devicetree sets')
        self.assertIn(f'level_khz[PG_BASE_LEVEL] != {MODULE.P_BASE[4] * 1000}', source)
        self.assertIn(f'level_khz[PG_TOP_LEVEL] != {MODULE.P_STATES[20] * 1000}', source)
        self.assertIn('#define PG_BASE_LEVEL 4\t', policy)
        self.assertIn('#define PG_TOP_LEVEL 20\t', policy)
        self.assertIn(f'#define PG_E_HIGH_KHZ {MODULE.E_STATES[6] * 1000}\t', policy)
        self.assertEqual(int(re.search(r'GATE_OPEN_MC (\d+)', source).group(1)), 150000)


@unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtoverlay'), 'dtc and fdtoverlay are required')
class CommittedOverlay(unittest.TestCase):
    def test_committed_overlay_builds_exactly_the_expected_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp) / 'base.dtb'
            base.write_bytes(dtb(base_nodes()))
            output = Path(temp) / 'out.dtb'
            with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(base.read_bytes())):
                receipt = MODULE.build(base, output, ROOT / 'patches/j713-pboost.dtso')
            nodes, _, _ = MODULE.tree(output.read_bytes())
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertNotIn('/__symbols__', nodes)
        self.assertEqual(sorted(p for p in nodes if p.startswith('/opp-table-1/')),
                         [f'/opp-table-1/opp{n:02d}' for n in range(1, 21)])


if __name__ == '__main__':
    unittest.main()
