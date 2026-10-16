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
# The settle runs (cpu/j713/bench/settle.py, 15 s holds, twice): rise 1.5 s into the hold, which
# the step table must cover, and the fast heat gone 2 s after the drop back to P4, which pg_fast
# must not exceed. One busy core asking for P20 runs P16/P17.
SETTLED_RISE = {
    1: {5: 1.9, 6: 3.8, 7: 5.7, 8: 8.4, 10: 14.0, 12: 20.5, 14: 28.5, 15: 27.1, 17: 28.9, 20: 28.5},
    2: {5: 3.2, 6: 5.8, 7: 8.3, 8: 11.7, 9: 14.2, 10: 19.2, 11: 25.0, 12: 26.8},
    3: {5: 3.8, 6: 6.2, 7: 8.4, 8: 13.2, 9: 16.4, 10: 23.1},
    4: {5: 4.1, 6: 7.3, 7: 9.7, 8: 15.4, 9: 19.2},
}
SETTLED_FAST = {
    1: {5: [1.9, 2.2], 6: [3.6, 3.6], 7: [5.5, 5.5], 8: [7.1], 10: [13.6, 14.0], 12: [20.9, 20.1], 14: [26.9, 28.8], 15: [27.9, 27.4], 17: [28.3, 28.8],
        20: [26.2, 28.8]},
    2: {5: [3.1, 3.2], 6: [5.2, 5.0], 7: [6.6, 7.8], 8: [10.0, 11.1], 9: [14.0], 10: [18.9, 16.5], 11: [25.0], 12: [27.6, 27.4]},
    3: {5: [2.6, 3.1], 6: [5.9, 5.5], 7: [8.0], 8: [12.3, 10.8], 9: [16.7], 10: [21.4, 21.2]},
    4: {5: [3.1, 3.3], 6: [6.6, 6.1], 7: [8.7, 8.7], 8: [14.6, 11.3], 9: [18.8, 18.6]},
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
        lib.fast.restype = ctypes.c_uint32
        lib.lowest.restype = ctypes.c_uint32
        lib.keep.argtypes = [U32S, U32S, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint32, ctypes.c_uint32]
        lib.lowest.argtypes = [U32S, ctypes.c_uint, ctypes.c_uint32]
        lib.base.argtypes = [ctypes.c_int, ctypes.c_bool, ctypes.c_uint, ctypes.c_uint32]
        lib.window_busy.restype = ctypes.c_bool
        lib.hold.restype = ctypes.c_bool
        lib.hold.argtypes = [ctypes.POINTER(ctypes.c_uint), ctypes.POINTER(ctypes.c_uint), ctypes.c_uint,
                             ctypes.c_uint, ctypes.c_bool]
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
        for busy, points in list(MEASURED.items()) + list(SETTLED_RISE.items()):
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
        for limit in (90, 100, 105):
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

    def test_base_subtracts_the_fast_heat_of_a_busy_window(self):
        for busy in range(1, 5):
            for level in range(5, 21):
                self.assertEqual(self.lib.base(85000, True, busy, level), 85000 - self.lib.fast(busy, level))

    def test_every_state_up_to_the_hot_ones_was_measured(self):
        # P5 up to the first state that reached the bench's 95 C abort (two P cores P11, three P10, four P9).
        for busy, top in ((1, 17), (2, 11), (3, 10), (4, 9)):
            with self.subTest(busy=busy):
                missing = [level for level in range(5, top + 1) if level not in SETTLED_FAST[busy]
                           and not (busy == 1 and level in (9, 11, 13, 16))]
                self.assertEqual(missing, [], 'one P core at P9/P11/P13/P16 takes the state below')

    def test_fast_heat_never_above_a_settle_measurement(self):
        # Every state subtracts no more than the lowest run of any measured state at or above it,
        # so the base can only err high.
        for busy, points in SETTLED_FAST.items():
            for level in range(5, 21):
                above = [min(runs) for measured, runs in points.items() if measured >= level]
                if above:
                    with self.subTest(busy=busy, level=level):
                        self.assertLessEqual(self.lib.fast(busy, level), min(above) * 1000)

    def test_base_looks_back_750_ms_for_the_lowest_state(self):
        ring = U32S(*([17, 9, 14] + [20] * 18))
        self.assertEqual(self.lib.lowest(ring, 3, 20), 9)
        self.assertEqual(self.lib.lowest(ring, 3, 6), 6, 'the current tick counts too')
        self.assertEqual(self.lib.lowest(ring, 0, 12), 12)
        self.assertEqual(self.lib.recent_ms(), 750)
        self.assertGreaterEqual(self.lib.recent_max(), -(-self.lib.recent_ms() // 50), 'room at the shortest period')

    def test_fewer_busy_cores_never_subtract_more_than_a_measured_higher_count(self):
        # The base uses the fewest busy cores of the look-back while more may have joined.
        for level in range(5, 21):
            for fewer in range(1, 4):
                for more in range(fewer + 1, 5):
                    if level in SETTLED_FAST[more]:
                        with self.subTest(level=level, fewer=fewer, more=more):
                            self.assertLessEqual(self.lib.fast(fewer, level), min(SETTLED_FAST[more][level]) * 1000)

    def test_the_ring_keeps_three_ticks_and_forgets_older_ones(self):
        # As the guard runs it: kept ticks start at P4 / all busy, each slow tick is kept, and the
        # base takes the lowest of the ring and the current tick.
        ring, busy = U32S(*([4] * 21)), U32S(*([4] * 21))
        pos = 0
        for tick, tick_busy in ((12, 1), (15, 2), (17, 1)):
            pos = self.lib.keep(ring, busy, pos, 3, tick, tick_busy)
        self.assertEqual(pos, 0)
        self.assertEqual((self.lib.lowest(ring, 3, 20), self.lib.lowest(busy, 3, 4)), (12, 1))
        pos = self.lib.keep(ring, busy, pos, 3, 18, 2)
        self.assertEqual(list(ring)[:3], [18, 15, 17])
        self.assertEqual(self.lib.lowest(ring, 3, 20), 15, 'the oldest tick has left the look-back')
        self.assertEqual(self.lib.lowest(busy, 3, 4), 1)

    def test_fast_heat_is_whole_degrees_rounded_down(self):
        for busy in range(1, 5):
            for level in range(21):
                self.assertEqual(self.lib.fast(busy, level) % 1000, 0, f'{busy} cores P{level}')

    def test_fast_heat_rows_are_bounded_and_ordered(self):
        for busy in range(1, 5):
            row = [self.lib.fast(busy, level) for level in range(21)]
            self.assertEqual(row[:5], [0] * 5, 'no fast heat at or below P4')
            self.assertEqual(row, sorted(row), f'{busy} busy cores: never more heat subtracted at a lower state')
            for level in range(21):
                self.assertLess(row[level], self.lib.step(busy, level) or 1,
                                'the subtracted heat stays under the predicted step')
        for level in range(21):
            self.assertEqual(self.lib.fast(0, level), 0, 'row 0 is unused: no busy core is planned as one')

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

    def hold(self, busy, e_busy, held, e_held, expired):
        b, e = ctypes.c_uint(busy), ctypes.c_uint(e_busy)
        restart = self.lib.hold(ctypes.byref(b), ctypes.byref(e), held, e_held, expired)
        return b.value, e.value, restart

    def test_more_busy_cores_count_at_once_and_fewer_wait_for_the_hold(self):
        self.assertEqual(self.hold(3, 1, 1, 1, False), (3, 1, True), 'a rise counts at once and restarts the hold')
        self.assertEqual(self.hold(3, 1, 1, 1, True), (3, 1, True))
        self.assertEqual(self.hold(1, 0, 3, 2, False), (3, 2, False), 'a fall waits while the hold runs')
        self.assertEqual(self.hold(1, 0, 3, 2, True), (1, 0, False), 'and counts once it has expired')
        self.assertEqual(self.hold(4, 0, 2, 3, False), (4, 3, True), 'P rose, E fell: P counts, E is held')
        self.assertEqual(self.hold(2, 3, 2, 3, False), (2, 3, True), 'unchanged keeps the hold running')

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
        self.assertIn('if (found[0] != 1 || found[1] != 1)\n\t\treturn -ENODEV;', self.init)
        self.assertIn('#define GATE_ZONE "cpu-die-thermal"', self.source)
        self.assertIn('#define GATE_CLOSED_MC 20000', self.source)
        self.assertIn('#define SUSTAIN_MC 95000', self.source)
        found = self.source[self.source.index('static int find_trips(struct thermal_trip *trip, void *data)\n{'):]
        found = found[:found.index('\n}\n')]
        self.assertIn('if (trip->temperature == GATE_CLOSED_MC) {\n\t\tgate = trip;\n\t\tfound[0]++;', found)
        self.assertIn('} else if (trip->temperature == SUSTAIN_MC) {\n\t\tsustain = trip;\n\t\tfound[1]++;', found)
        self.assertIn('#define SUSTAIN_BOOST_MC 100000', self.source)

    def test_limits_follow_macos_on_the_fanless_air(self):
        for name, value in (('limit_c', 105), ('hot_c', 108), ('release_c', 100)):
            self.assertIn(f'static uint {name} = {value};', self.source)
        self.assertIn('limit_c >= hot_c || release_c >= hot_c || hot_c > 110', self.init)
        opened = self.source[self.source.index('static int open_gate(struct thermal_trip *trip, void *data)\n{'):]
        opened = opened[:opened.index('\n}\n')]
        self.assertIn('thermal_zone_set_trip_temp(zone, trip, GATE_OPEN_MC);', opened)
        self.assertIn('else if (trip == sustain)\n\t\tthermal_zone_set_trip_temp(zone, trip, SUSTAIN_BOOST_MC);', opened)

    def test_refuses_any_other_cluster_layout_or_table(self):
        for check in ('n != PG_P_CPUS', 'i != PG_E_CPUS', 'level_khz[PG_BASE_LEVEL] != 1800000',
                      'level_khz[PG_TOP_LEVEL] != 4464000', 'policy->cpuinfo.max_freq != 4464000',
                      'cpumask_intersects(e_policy->related_cpus, policy->related_cpus)'):
            self.assertIn(check, self.init)

    def test_cap_starts_at_p4_and_the_gate_opens_only_after_it(self):
        order = [self.init.index(call) for call in (
            'freq_qos_add_request(&policy->constraints, &cap_req, FREQ_QOS_MAX,\n\t\t\t\t   level_khz[PG_BASE_LEVEL])',
            'cpuhp_setup_state(CPUHP_AP_ONLINE_DYN',
            'queue_delayed_work(system_unbound_wq, &slow, 0);',
            'thermal_zone_for_each_trip(zone, open_gate, NULL);',
            'thermal_zone_device_update(zone, THERMAL_TRIP_CHANGED);')]
        self.assertEqual(order, sorted(order))
        self.assertEqual(self.init.count('open_gate'), 1)
        # Every refusal returns before the gate opens.
        self.assertLess(self.init.rindex('goto put_e_policy'), self.init.index('open_gate'))
        self.assertLess(self.init.rindex('goto remove_req'), self.init.index('open_gate'))
        self.assertLess(self.init.rindex('goto remove_proc'), self.init.index('open_gate'))

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

    def test_the_cap_is_applied_to_the_p_policy(self):
        update = self.source[self.source.index('static void update_cap(bool may_raise)\n{'):]
        update = update[:update.index('\n}\n')]
        change = update[update.index('if (want != applied_level) {'):]
        self.assertIn('freq_qos_update_request(&cap_req, level_khz[want]);', change)
        self.assertLess(change.index('freq_qos_update_request'), change.index('applied_level = want;'))
        self.assertIn('freq_qos_add_request(&policy->constraints, &cap_req, FREQ_QOS_MAX,', self.init)

    def test_fast_timers_stay_on_their_cores_across_hotplug(self):
        self.assertIn('cpuhp_setup_state(CPUHP_AP_ONLINE_DYN, "j713_pguard:online", fast_timer_online, '
                      'fast_timer_offline);', self.init)
        self.assertLess(self.init.index('cpuhp_setup_state('), self.init.index('open_gate'))
        online = self.source[self.source.index('static int fast_timer_online(unsigned int cpu)\n{'):]
        online = online[:online.index('\n}\n')]
        self.assertIn('TIMER_DEFERRABLE | TIMER_PINNED', online)
        self.assertIn('add_timer_on(t, cpu);', online)
        offline = self.source[self.source.index('static int fast_timer_offline(unsigned int cpu)\n{'):]
        offline = offline[:offline.index('\n}\n')]
        order = [offline.index(s) for s in ('WRITE_ONCE(per_cpu(fast_timer_on, cpu), false);',
                                            'timer_delete_sync(per_cpu_ptr(&fast_timer, cpu));')]
        self.assertEqual(order, sorted(order), 'stop the re-arm before deleting the timer')
        fast = self.source[self.source.index('static void fast_timer_fn(struct timer_list *t)\n{'):]
        fast = fast[:fast.index('\n}\n')]
        self.assertIn('if (READ_ONCE(*this_cpu_ptr(&fast_timer_on)))\n\t\tmod_timer(', fast)
        self.assertNotIn('add_timer_on', self.init, 'only the hotplug callback arms timers')

    def test_the_slow_path_also_counts_busy_cores(self):
        slow = self.source[self.source.index('static void slow_work(struct work_struct *w)\n{'):]
        slow = slow[:slow.index('\n}\n')]
        self.assertIn('sample_busy();', slow)
        self.assertLess(slow.index('sample_busy();'), slow.index('update_cap(true);'))
        self.assertIn('pg_hold(&busy, &e_busy, busy_held, e_busy_held, time_after(jiffies, held_until))',
                      self.source)

    def test_the_base_uses_the_recent_ticks_and_starts_subtracting_nothing(self):
        slow = self.source[self.source.index('static void slow_work(struct work_struct *w)\n{'):]
        slow = slow[:slow.index('\n}\n')]
        self.assertIn('base_mc = pg_base(mc, busy, pg_lowest(recent_min_busy, recent_ticks, tick_min_busy),\n'
                      '\t\t\t\t\t  pg_lowest(recent_min, recent_ticks, tick_min));', slow)
        # The whole sequence, between the end of the reading's if/else and the unlock, so no condition can
        # wrap it: every slow tick is kept, then the next starts from the current state.
        self.assertIn('\t\tsmc_ok = true;\n\t}\n'
                      '\t/* This tick joins the recent ones; the next starts from the current state. */\n'
                      '\trecent_pos = pg_keep(recent_min, recent_min_busy, recent_pos, recent_ticks, tick_min, '
                      'tick_min_busy);\n\ttick_min = policy_level();\n\ttick_min_busy = PG_P_CPUS;\n'
                      '\tspin_unlock_irqrestore(&fast_lock, flags);\n\tmutex_unlock(&lock);\n', slow)
        self.assertEqual(slow.count('pg_keep('), 1)
        self.assertIn('recent_ticks = min_t(unsigned int, DIV_ROUND_UP(PG_RECENT_MS, period_ms), PG_RECENT_MAX);',
                      self.init)
        self.assertIn('recent_min[i] = PG_BASE_LEVEL;', self.init, 'until measured, the kept ticks subtract nothing')
        self.assertLess(self.init.index('recent_ticks = min_t('), self.init.index('queue_delayed_work'))
        self.assertIn('period_ms < 50', self.init)

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
