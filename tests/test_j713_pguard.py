# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""J713 P-cluster guard (#66): the policy header compiled on the host, and the module's fail-closed shape."""
import ctypes
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'cpu/j713/j713_pguard.c'
POLICY = ROOT / 'cpu/j713/j713_pguard_policy.h'
HARNESS = ROOT / 'tests/j713_pguard_policy_harness.c'
# ADT voltage-states5-sram, one-based (docs/j713-cpu-boost.md); 0 for no state.
P_KHZ = [0, 912000, 1260000, 1512000, 1800000, 2088000, 2352000, 2616000, 2868000, 3096000, 3300000,
         3468000, 3624000, 3756000, 3876000, 3936000, 3984000, 4044000, 4104000, 4416000, 4464000]
# TCMz rise from P4 about 1.5-2 s after the change, by busy P cores and P state, with the idle
# driver loaded (docs/j713-cpu-boost.md). Runs stopped by the 95 C abort are lower bounds.
MEASURED = {
    1: {12: 23.7, 15: 31.2, 20: 32.1},
    2: {10: 21.6, 12: 32.3, 14: 44.9},
    3: {8: 15.3, 10: 26.9, 12: 35.3},
    4: {6: 8.7, 8: 17.7, 10: 30.3},
}
U32S = ctypes.c_uint32 * 21
U64S = ctypes.c_uint64 * 6


@unittest.skipUnless(shutil.which('cc'), 'no host C compiler')
class Policy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        library = Path(cls.temp.name) / 'pguard_policy.so'
        subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-shared', '-fPIC',
                        '-o', str(library), str(HARNESS)], check=True)
        lib = ctypes.CDLL(str(library))
        lib.step.restype = ctypes.c_uint32
        lib.base.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_uint, ctypes.c_uint32]
        lib.window_busy.restype = ctypes.c_bool
        lib.e_heat.argtypes = [ctypes.c_uint, ctypes.c_uint32]
        lib.float_to_mc.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_int)]
        lib.allowed.restype = ctypes.c_uint32
        lib.allowed.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_int, ctypes.c_int, ctypes.c_bool, U32S]
        cls.lib = lib
        cls.levels = U32S(*P_KHZ)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def allowed(self, base_c, busy, e_heat_mc=0, limit_c=100, hot=False, levels=None):
        return self.lib.allowed(int(base_c * 1000), busy, e_heat_mc, limit_c * 1000, hot,
                                levels if levels is not None else self.levels)

    def test_steps_never_below_a_measurement(self):
        for busy, points in MEASURED.items():
            for level, rise in points.items():
                with self.subTest(busy=busy, level=level):
                    self.assertGreaterEqual(self.lib.step(busy, level), rise * 1000)

    def test_steps_grow_with_state_and_busy_cores(self):
        for busy in range(1, 5):
            row = [self.lib.step(busy, level) for level in range(21)]
            self.assertEqual(row[:5], [0] * 5, 'no step at or below P4')
            self.assertTrue(all(row[level] > 0 for level in range(5, 21)))
            self.assertEqual(row, sorted(row), f'{busy} busy cores: a higher state never heats less')
        for level in range(5, 21):
            column = [self.lib.step(busy, level) for busy in range(1, 5)]
            self.assertEqual(column, sorted(column), f'P{level}: more busy cores never heat less')

    def test_float_reading(self):
        mc = ctypes.c_int()
        for celsius in (1.0, 52.046, 99.9, 114.8, 255.9):
            bits = struct.unpack('>I', struct.pack('>f', celsius))[0]
            self.assertEqual(self.lib.float_to_mc(bits, ctypes.byref(mc)), 0)
            self.assertAlmostEqual(mc.value, celsius * 1000, delta=1)
        for celsius in (-5.0, 0.5, 256.0, float('inf'), float('nan')):
            bits = struct.unpack('>I', struct.pack('>f', celsius))[0]
            self.assertEqual(self.lib.float_to_mc(bits, ctypes.byref(mc)), -1, celsius)

    def test_hot_or_hopeless_pins_p4(self):
        self.assertEqual(self.allowed(50, 1, hot=True), 4)
        self.assertEqual(self.allowed(97, 1), 5, 'P5 adds 3 C')
        self.assertEqual(self.allowed(97.5, 1), 4, 'no state above P4 fits under the limit')
        self.assertEqual(self.allowed(120, 4), 4)

    def test_cool_die_allows_more_with_fewer_busy_cores(self):
        self.assertEqual(self.allowed(52, 1), 20)
        caps = [self.allowed(60, busy) for busy in range(1, 5)]
        self.assertEqual(caps, sorted(caps, reverse=True))
        self.assertLess(caps[3], caps[0])

    def test_idle_cluster_is_planned_as_one_busy_core(self):
        self.assertEqual(self.allowed(70, 0), self.allowed(70, 1))
        self.assertEqual(self.allowed(70, 9), self.allowed(70, 4))

    def test_predicted_peak_never_over_the_limit(self):
        for limit in (90, 100):
            for busy in range(0, 5):
                for e_busy in range(0, 7):
                    for e_khz in (900000, 2172000, 2496000, 2808000):
                        heat = self.lib.e_heat(e_busy, e_khz)
                        for base_c in range(30, 111):
                            level = self.allowed(base_c, busy, heat, limit)
                            self.assertTrue(4 <= level <= 20)
                            if level > 4:
                                peak = base_c * 1000 + heat + self.lib.step(min(max(busy, 1), 4), level)
                                self.assertLessEqual(peak, limit * 1000)
                                above = [n for n in range(level + 1, 21)
                                         if base_c * 1000 + heat + self.lib.step(min(max(busy, 1), 4), n)
                                         <= limit * 1000]
                                self.assertEqual(above, [], 'the highest fitting state is chosen')

    def test_missing_states_are_skipped(self):
        levels = list(P_KHZ)
        levels[20] = levels[19] = 0
        self.assertEqual(self.allowed(52, 1, levels=U32S(*levels)), 18)

    def test_e_cluster_heat(self):
        self.assertEqual(self.lib.e_heat(0, 2808000), 0)
        self.assertEqual(self.lib.e_heat(6, 2808000), 12000)
        self.assertEqual(self.lib.e_heat(6, 2496000), 12000)
        self.assertEqual(self.lib.e_heat(6, 2172000), 6000)
        self.assertEqual(self.lib.e_heat(9, 2808000), 12000, 'at most six E cores')
        self.assertLess(self.allowed(70, 4, self.lib.e_heat(6, 2808000)), self.allowed(70, 4))

    def test_base_subtracts_only_a_share_of_a_busy_window(self):
        share = self.lib.share()
        self.assertTrue(0 < share < 100)
        for busy in range(1, 5):
            for level in range(5, 21):
                expected = 85000 - self.lib.step(busy, level) * share // 100
                self.assertEqual(self.lib.base(85000, True, busy, level), expected)
                self.assertGreater(self.lib.base(85000, True, busy, level), 85000 - self.lib.step(busy, level))

    def test_base_subtracts_nothing_unless_a_core_ran_above_p4_throughout(self):
        self.assertEqual(self.lib.base(85000, False, 4, 20), 85000)
        for level in (0, 1, 4, 21, 99):
            self.assertEqual(self.lib.base(85000, True, 1, level), 85000)
        self.assertEqual(self.lib.base(85000, True, 0, 12), self.lib.base(85000, True, 1, 12))
        self.assertEqual(self.lib.base(85000, True, 7, 12), self.lib.base(85000, True, 4, 12))

    def test_window_busy_needs_one_core_at_least_90_percent_busy(self):
        wall = U64S(*[1000] * 6)
        self.assertTrue(self.lib.window_busy(U64S(1000, 1000, 100, 1000), wall, 4))
        self.assertFalse(self.lib.window_busy(U64S(1000, 1000, 101, 1000), wall, 4))
        self.assertFalse(self.lib.window_busy(U64S(0, 0, 0, 0), U64S(0, 0, 0, 0), 4), 'no time, no evidence')

    def test_busy_count_is_more_than_half_busy(self):
        wall = U64S(*[1000] * 6)
        self.assertEqual(self.lib.busy_count(U64S(0, 499, 500, 1000, 0, 0), wall, 4), 2)
        self.assertEqual(self.lib.busy_count(U64S(0, 0, 0, 0, 0, 0), U64S(0, 0, 0, 0, 0, 0), 4), 0)
        self.assertEqual(self.lib.busy_count(U64S(*[0] * 6), wall, 6), 6)


class ModuleSource(unittest.TestCase):
    """The guard fails closed, opens the boost gate only behind its own cap, and never touches hardware."""

    def setUp(self):
        self.source = SOURCE.read_text()
        start = self.source.index('static int __init guard_init(void)')
        self.init = self.source[start:self.source.index('\n}\n', start)]

    def test_built_with_the_idle_module(self):
        self.assertEqual((ROOT / 'cpu/j713/Kbuild').read_text().split(),
                         ['obj-m', '+=', 'j713_cpuidle.o', 'obj-m', '+=', 'j713_pguard.o'])
        self.assertIn('#include "j713_pguard_policy.h"', self.source)

    def test_no_hardware_access(self):
        for word in ('asm', 'msr', 'writel', 'readl', 'ioremap', 'apple_smc_write', 'regmap'):
            self.assertIsNone(re.search(r'\b' + word + r'\b', self.source), word)

    def test_cannot_be_unloaded(self):
        self.assertIsNone(re.search(r'\bmodule_exit\s*\(', self.source))
        self.assertIn('module_init(guard_init);', self.source)

    def test_refuses_without_t8132_the_idle_driver_or_the_closed_gate(self):
        self.assertIn('of_machine_is_compatible("apple,t8132")', self.init)
        self.assertIn('strcmp(idle->name, "j713_idle")', self.init)
        self.assertIn('thermal_zone_get_zone_by_name(GATE_ZONE)', self.init)
        self.assertIn('if (n != 1)\n\t\treturn -ENODEV;', self.init)
        self.assertIn('#define GATE_ZONE "cpu-die-thermal"', self.source)
        self.assertIn('#define GATE_CLOSED_MC 20000', self.source)

    def test_refuses_any_other_cluster_layout_or_table(self):
        for check in ('n != PG_P_CPUS', 'i != PG_E_CPUS', 'level_khz[PG_BASE_LEVEL] != 1800000',
                      'level_khz[PG_TOP_LEVEL] != 4464000', 'policy->cpuinfo.max_freq != 4464000',
                      'cpumask_intersects(e_policy->related_cpus, policy->related_cpus)'):
            self.assertIn(check, self.init)

    def test_cap_starts_at_p4_and_the_gate_opens_only_after_it(self):
        order = [self.init.index(call) for call in (
            'freq_qos_add_request(&policy->constraints, &cap_req, FREQ_QOS_MAX,\n\t\t\t\t   level_khz[PG_BASE_LEVEL])',
            'add_timer_on(t, p_cpus[i]);',
            'queue_delayed_work(system_unbound_wq, &slow, 0);',
            'thermal_zone_for_each_trip(zone, open_gate, NULL);',
            'thermal_zone_device_update(zone, THERMAL_TRIP_CHANGED);')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(self.init.count('open_gate'), 1)
        # Every refusal returns before the gate opens.
        self.assertLess(self.init.rindex('goto put_e_policy'), self.init.index('open_gate'))
        self.assertLess(self.init.rindex('goto remove_req'), self.init.index('open_gate'))

    def test_sensor_failure_or_heat_pins_p4(self):
        wanted = self.source[self.source.index('static u32 wanted_level(void)\n{'):]
        wanted = wanted[:wanted.index('\n}\n')]
        self.assertIn('if (!smc_ok)\n\t\treturn PG_BASE_LEVEL;', wanted)
        self.assertIn('hot, level_khz', wanted)
        slow = self.source[self.source.index('static void slow_work(struct work_struct *w)\n{'):]
        slow = slow[:slow.index('\n}\n')]
        self.assertIn('smc_ok = false;', slow)
        self.assertIn('if (mc >= (int)hot_c * 1000)\n\t\t\thot = true;', slow)
        self.assertIn('static int base_mc = 1000 * 100;', self.source, 'no state above P4 before a reading')

    def test_only_the_slow_path_raises(self):
        self.assertIn('static void apply_work(struct work_struct *w)\n{\n\tupdate_cap(false);\n}', self.source)
        self.assertIn('update_cap(true);\n\tqueue_delayed_work', self.source)
        self.assertIn('if (want > applied_level && !may_raise)\n\t\twant = applied_level;', self.source)

    def test_parameters_are_bounded(self):
        self.assertIn('limit_c < 60 || limit_c > 105', self.init)
        self.assertIn('release_c >= hot_c || hot_c > 110', self.init)
        for name in ('limit_c', 'hot_c', 'release_c', 'period_ms'):
            self.assertIn(f'module_param({name}, uint, 0444);', self.source)


if __name__ == '__main__':
    unittest.main()
