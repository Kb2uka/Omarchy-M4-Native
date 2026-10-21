# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The boot-time CPU idle driver and boost guard load: pinned inputs only, one strike after a boot dies."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_cpu_idle', ROOT / 'tools/m4-cpu-idle.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

DRIVER_BYTES = b'cpu idle module'
PIN = {'sha256': hashlib.sha256(DRIVER_BYTES).hexdigest(), 'size': len(DRIVER_BYTES)}
GUARD_BYTES = b'boost guard module'
GUARD_PIN = {'sha256': hashlib.sha256(GUARD_BYTES).hexdigest(), 'size': len(GUARD_BYTES)}
CPUS = 10
ZONE = 'sys/class/thermal/thermal_zone2'
GATE_TRIP = ZONE + '/trip_point_0_temp'
SUSTAIN_TRIP = ZONE + '/trip_point_1_temp'


class Machine:
    """insmod against a fake sysfs; sleep advances the power-down counters while the driver is loaded."""

    def __init__(self, root, fail=False, idles=True, guard_fails=False):
        self.root, self.fail, self.idles, self.guard_fails = root, fail, idles, guard_fails
        self.commands, self.marked, self.slept = [], [], []
        self.markers = []

    def __call__(self, command):
        self.commands.append(command)
        self.marked.append((self.root / MODULE.STATE / 'attempt').is_file())
        self.markers.append((self.root / MODULE.STATE / 'attempt').read_text())
        if command[-1].endswith(MODULE.GUARD_STAGED):
            # The real guard refuses without the idle driver, and opens the gate itself.
            if self.guard_fails or not (self.root / 'sys/module' / MODULE.NAME).exists():
                return 1
            if (self.root / GATE_TRIP).read_text() != '20000\n' or (self.root / SUSTAIN_TRIP).read_text() != '95000\n':
                return 1  # the real guard refuses without both trips as the devicetree sets them
            (self.root / 'sys/module' / MODULE.GUARD_NAME).mkdir(parents=True)
            (self.root / GATE_TRIP).write_text('150000\n')
            (self.root / SUSTAIN_TRIP).write_text('100000\n')
            return 0
        if self.fail:
            return 1
        (self.root / 'sys/module' / MODULE.NAME).mkdir(parents=True)
        (self.root / MODULE.CURRENT_DRIVER).write_text(MODULE.DRIVER + '\n')
        for cpu in range(CPUS):
            usage = self.root / MODULE.CPUS / f'cpu{cpu}' / MODULE.POWER_DOWN / 'usage'
            usage.parent.mkdir(parents=True)
            usage.write_text('0\n')
        return 0

    def sleep(self, seconds):
        self.slept.append((seconds, (self.root / MODULE.STATE / 'attempt').is_file()))
        # The loader must hold its lock exclusively for the whole settle period.
        import fcntl, os
        fd = os.open(self.root / MODULE.STATE / 'lock', os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            self.lock_free_while_settling = True
        except BlockingIOError:
            self.lock_free_while_settling = False
        finally:
            os.close(fd)
        if self.idles:
            for usage in (self.root / MODULE.CPUS).glob('cpu*/' + MODULE.POWER_DOWN + '/usage'):
                usage.write_text(str(int(usage.read_text()) + 500) + '\n')


class ChildMachine(Machine):
    """Machine for a child process: at the chosen window (the settle sleep or a pending module
    load) it records the state, announces it to the parent, then waits for release. The failsafe
    exits the child without completing, long after the parent's EXIT_TIMEOUT, so a child still
    running at the parent's deadline ignored the signal rather than finished on its own."""

    FAILSAFE_SECONDS = 30.0

    def __init__(self, root, sync, window):
        super().__init__(root)
        self.sync, self.window, self.announced = Path(sync), window, False

    def announce(self, phase, seconds=None):
        import fcntl
        state = self.root / MODULE.STATE
        fd = os.open(state / 'lock', os.O_RDONLY)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
                lock_free = True
            except BlockingIOError:
                lock_free = False
        finally:
            os.close(fd)
        (self.sync / 'blocked.json').write_text(json.dumps(
            {'phase': phase, 'seconds': seconds, 'marked': (state / 'attempt').is_file(),
             'lock_free': lock_free}))
        (self.sync / 'ready').write_text('ready\n')
        deadline = time.monotonic() + self.FAILSAFE_SECONDS
        while not (self.sync / 'release').exists():
            if time.monotonic() > deadline:
                raise SystemExit('child harness: the parent never released the ' + phase + ' window')
            time.sleep(0.02)

    def __call__(self, command):
        guard = command[-1].endswith(MODULE.GUARD_STAGED)
        wanted = (self.window == 'load' and not guard) or (self.window == 'guard-load' and guard)
        if wanted and not self.announced:
            self.announced = True
            # The marker is already durable; this module load is still pending.
            self.announce(self.window)
        return super().__call__(command)

    def sleep(self, seconds):
        if not self.announced:
            self.announced = True
            self.announce('settle', seconds)
        super().sleep(seconds)


def child_settle_main(argv):
    """Child harness: the real main() with a fake machine runner and a synchronizing settle
    sleep, so a test can signal the child while it is provably blocked at the requested window."""
    mode, root, sync, window = argv[0], Path(argv[1]), Path(argv[2]), argv[3]
    MODULE.PIN, MODULE.GUARD_PIN = PIN, GUARD_PIN
    machine = ChildMachine(root, sync, window)
    real_run = MODULE.run

    def run_with_fake_machine(root_arg, mode_arg, runner=None, settle_seconds=None, sleep=None):
        return real_run(root_arg, mode_arg, machine, MODULE.SETTLE_SECONDS, machine.sleep)

    MODULE.run = run_with_fake_machine
    return MODULE.main(['--' + mode, '--root', str(root)])


class CpuIdleLoad(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name, value in (('PIN', PIN), ('GUARD_PIN', GUARD_PIN)):
            patcher = mock.patch.object(MODULE, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.put(MODULE.COMPATIBLE, b'apple,j713\0apple,t8132\0apple,arm-platform\0')
        self.put(MODULE.OSRELEASE, (MODULE.RELEASE + '\n').encode())
        self.put(MODULE.STAGED, DRIVER_BYTES)
        self.put(MODULE.CURRENT_DRIVER, b'none\n')
        (self.root / 'sys/module').mkdir(parents=True)
        for cpu in range(CPUS):
            (self.root / MODULE.CPUS / f'cpu{cpu}').mkdir(parents=True, exist_ok=True)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def marker(self):
        return self.root / MODULE.STATE / 'attempt'

    def boost_ready(self, gate='20000'):
        """The boost devicetree's CPU die zone, its 95 C trip and the gate, plus the staged guard."""
        self.put(MODULE.GUARD_STAGED, GUARD_BYTES)
        self.put(ZONE + '/type', b'cpu-die-thermal\n')
        self.put(GATE_TRIP, (gate + '\n').encode())
        self.put(ZONE + '/trip_point_0_type', b'passive\n')
        self.put(ZONE + '/trip_point_1_temp', b'95000\n')
        self.put(ZONE + '/trip_point_1_type', b'passive\n')
        self.put('sys/class/thermal/thermal_zone0/type', b'other\n')
        self.put('sys/class/thermal/thermal_zone0/trip_point_0_temp', b'20000\n')
        self.put('sys/class/thermal/thermal_zone0/trip_point_0_type', b'passive\n')
        self.put(MODULE.RT_FLOOR_KNOB, b'1024\n')

    def gate(self):
        return (self.root / GATE_TRIP).read_text().strip()

    def sustain(self):
        return (self.root / SUSTAIN_TRIP).read_text().strip()

    def restart(self):
        """What a restart resets: loaded modules, and the gate back to the devicetree's 20 C."""
        for name in (MODULE.NAME, MODULE.GUARD_NAME):
            path = self.root / 'sys/module' / name
            if path.exists():
                path.rmdir()
        if (self.root / GATE_TRIP).exists():
            self.put(GATE_TRIP, b'20000\n')
            self.put(SUSTAIN_TRIP, b'95000\n')
        import shutil
        shutil.rmtree(self.root / MODULE.CPUS)
        for cpu in range(CPUS):
            (self.root / MODULE.CPUS / f'cpu{cpu}').mkdir(parents=True)
        self.put(MODULE.CURRENT_DRIVER, b'none\n')

    def guard_insmod(self):
        return ['insmod', str(self.root / MODULE.GUARD_STAGED)]

    def idle_insmod(self):
        return ['insmod', str(self.root / MODULE.STAGED)]

    def hold(self):
        return self.root / MODULE.STATE / 'hold'

    def run_mode(self, mode, machine):
        return MODULE.run(self.root, mode, machine, settle_seconds=120, sleep=machine.sleep)

    def test_inspection_changes_nothing(self):
        machine = Machine(self.root)
        report = self.run_mode('inspect', machine)
        self.assertEqual(report['status'], 'loads at the next boot')
        self.assertEqual((machine.commands, machine.slept), ([], []))
        self.assertFalse((self.root / MODULE.STATE).exists())

    def test_boot_loads_the_pinned_module_and_settles_before_clearing_the_marker(self):
        machine = Machine(self.root)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [['insmod', str(self.root / MODULE.STAGED)]])
        self.assertEqual(machine.marked, [True], 'marker must be durable before the load')
        self.assertEqual(machine.slept, [(120, True)], 'marker must cover the settle period')
        self.assertFalse(machine.lock_free_while_settling, 'the loader must hold its lock exclusively while settling')
        self.assertFalse(self.marker().exists())
        self.assertEqual((report['state'], report['driver'], report['power_downs']), ('loaded', 'j713_idle', 5000))
        self.assertTrue(report['status'].startswith('loaded'))

    def test_a_boot_that_died_while_loading_or_settling_stops_the_next_boot(self):
        self.put(MODULE.STATE + '/attempt', b'loading\n')
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('inspect', machine)['state'], 'struck')
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])
        self.assertTrue(self.marker().exists())

    def test_start_retries_in_the_session_and_clears_the_marker(self):
        self.put(MODULE.STATE + '/attempt', b'loading\n')
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('start', machine)['state'], 'loaded')
        self.assertEqual(len(machine.commands), 1)
        self.assertFalse(self.marker().exists())

    def test_a_failed_load_clears_the_marker_and_stops(self):
        machine = Machine(self.root, fail=True)
        with self.assertRaisesRegex(MODULE.Refused, 'Loading j713_cpuidle.ko failed'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.slept, [])
        self.assertFalse(self.marker().exists(), 'a load that returned is not a strike')

    def test_a_driver_no_core_idles_through_is_reported_and_not_a_strike(self):
        machine = Machine(self.root, idles=False)
        with self.assertRaisesRegex(MODULE.Refused, 'no core powered down'):
            self.run_mode('boot', machine)
        self.assertFalse(self.marker().exists(), 'the boot lived through the settle period')

    def test_only_the_pinned_module_on_the_pinned_kernel_and_board(self):
        cases = ((MODULE.OSRELEASE, b'7.1.9-other\n', 'running kernel'),
                 (MODULE.COMPATIBLE, b'apple,j613\0apple,t8132\0', 'not a J713'),
                 (MODULE.STAGED, b'other idle module', 'differs from the pin'))
        for name, data, message in cases:
            with self.subTest(message):
                original = (self.root / name).read_bytes()
                self.put(name, data)
                machine = Machine(self.root)
                with self.assertRaisesRegex(MODULE.Refused, message):
                    self.run_mode('boot', machine)
                self.assertEqual(machine.commands, [])
                self.assertFalse(self.marker().exists())
                self.put(name, original)

    def test_another_idle_driver_stays_in_charge(self):
        self.put(MODULE.CURRENT_DRIVER, b'apple_idle\n')
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, r'Another CPU idle driver \(apple_idle\)'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])
        self.assertFalse(self.marker().exists())

    def test_loaded_driver_is_current(self):
        machine = Machine(self.root)
        self.run_mode('boot', machine)
        again = Machine(self.root)
        self.assertEqual(self.run_mode('boot', again)['status'], 'current')
        self.assertEqual((again.commands, again.slept), ([], []))

    def test_disable_stops_boot_and_start_until_enable(self):
        report = self.run_mode('disable', Machine(self.root))
        self.assertTrue(self.hold().is_file())
        self.assertEqual((report['held'], report['status']), (True, 'off; nothing loads at boot'))
        self.assertTrue(self.run_mode('inspect', Machine(self.root))['status'].startswith('off;'))
        for mode in ('boot', 'start'):
            with self.subTest(mode):
                machine = Machine(self.root)
                with self.assertRaisesRegex(MODULE.Refused, 'switched off'):
                    self.run_mode(mode, machine)
                self.assertEqual(machine.commands, [])
        self.run_mode('enable', Machine(self.root))
        self.assertFalse(self.hold().exists())
        self.assertEqual(self.run_mode('boot', Machine(self.root))['state'], 'loaded')

    def test_start_waits_for_a_boot_that_is_still_settling(self):
        state = self.root / MODULE.STATE
        state.mkdir(parents=True)
        import fcntl, os
        fd = os.open(state / 'lock', os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.put(MODULE.STATE + '/attempt', b'loading\n')
        for mode in ('start', 'boot', 'disable', 'enable'):
            with self.subTest(mode):
                machine = Machine(self.root)
                with self.assertRaisesRegex(MODULE.Refused, 'loading or settling'):
                    self.run_mode(mode, machine)
                self.assertEqual(machine.commands, [])
                self.assertTrue(self.marker().exists(), 'the settling boot keeps its marker')
        self.assertEqual(self.run_mode('inspect', Machine(self.root))['state'], 'struck')

    def test_disable_while_loaded_says_it_stays_until_restart(self):
        self.run_mode('boot', Machine(self.root))
        report = self.run_mode('disable', Machine(self.root))
        self.assertEqual(report['status'], 'off; the loaded driver stays until the next restart')

    def test_non_root_is_refused_except_inspection(self):
        with mock.patch.object(MODULE.os, 'geteuid', return_value=1000), \
                mock.patch.object(MODULE, 'run') as run:
            self.assertEqual(MODULE.main(['--boot']), 1)
            run.assert_not_called()

    def test_boot_loads_the_guard_inside_the_attempt_window(self):
        self.boost_ready()
        machine = Machine(self.root)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod(), self.guard_insmod()], 'idle first: the guard needs it')
        self.assertEqual(machine.marked, [True, True], 'a guard that takes the boot down is a strike too')
        self.assertEqual(machine.slept, [(120, True)])
        self.assertFalse(self.marker().exists())
        self.assertEqual((report['boost'], report['gate'], self.gate()), ('on', 'open', '150000'))
        self.assertEqual(report['status'], 'loaded; idle cores power down; boost guard loaded')
        self.assertEqual(report['rt_floor'], str(MODULE.RT_FLOOR), 'real-time tasks keep 1800 MHz, not the boost')
        self.assertEqual((report['sustained_trip'], self.sustain()), ('boost', '100000'))

    def test_boost_off_and_on_move_the_sustained_trip_with_the_gate(self):
        self.boost_ready()
        self.run_mode('boot', Machine(self.root))
        report = self.run_mode('boost-off', Machine(self.root))
        self.assertEqual((self.gate(), self.sustain(), report['sustained_trip']), ('20000', '95000', 'normal'))
        report = self.run_mode('boost-on', Machine(self.root))
        self.assertEqual((self.gate(), self.sustain(), report['sustained_trip']), ('150000', '100000', 'boost'))

    def test_a_sustained_trip_raised_without_the_guard_goes_back_to_95_c(self):
        self.boost_ready()
        self.put(SUSTAIN_TRIP, b'100000\n')
        machine = Machine(self.root, guard_fails=True)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod(), self.guard_insmod()])
        self.assertEqual((self.sustain(), report['sustained_trip']), ('95000', 'normal'), 'only behind the guard')
        self.restart()
        self.put(SUSTAIN_TRIP, b'100000\n')
        self.assertEqual(self.run_mode('boot', Machine(self.root))['boost'], 'on', 'reset, then the guard loads')

    def test_rt_floor_resolves_to_the_pre_boost_clock(self):
        # schedutil: frequency = policy maximum * floor / CPU capacity, rounded up to the next table state.
        p_table = [912000, 1260000, 1512000, 1800000, 2088000]
        target = 4464000 * MODULE.RT_FLOOR // 1024
        self.assertEqual(min(f for f in p_table if f >= target), 1800000)
        self.assertGreater(4464000 * (MODULE.RT_FLOOR + 1) // 1024, 1800000, 'the highest floor that gives 1800 MHz')
        e_table = [900000, 1080000, 1476000, 1860000, 2172000, 2496000, 2808000]
        self.assertEqual(min(f for f in e_table if f >= 2808000 * MODULE.RT_FLOOR // 417), 2808000,
                         'E cores (capacity 417) still run real-time tasks at their maximum')

    def test_a_lower_rt_floor_is_kept_and_a_higher_one_restored(self):
        self.boost_ready()
        knob = self.root / MODULE.RT_FLOOR_KNOB
        self.put(MODULE.RT_FLOOR_KNOB, b'0\n')
        self.run_mode('boot', Machine(self.root))
        self.assertEqual(knob.read_text(), '0\n', 'an owner floor below 412 is left alone')
        self.run_mode('boost-off', Machine(self.root))
        self.assertEqual(knob.read_text(), '0\n')
        self.put(MODULE.RT_FLOOR_KNOB, b'700\n')
        self.run_mode('boost-on', Machine(self.root))
        self.assertEqual(knob.read_text(), '412\n')
        self.run_mode('boost-off', Machine(self.root))
        self.assertEqual(knob.read_text(), '700\n', 'the replaced floor comes back')
        self.assertFalse((self.root / MODULE.STATE / 'rt-floor').exists())

    def test_an_owner_floor_below_412_drops_a_stale_saved_floor(self):
        self.boost_ready()
        knob = self.root / MODULE.RT_FLOOR_KNOB
        self.run_mode('boot', Machine(self.root))
        self.assertEqual(self.put(MODULE.STATE + '/rt-floor', b'1024\n'), None)
        self.put(MODULE.RT_FLOOR_KNOB, b'0\n')
        self.run_mode('boost-on', Machine(self.root))
        self.assertFalse((self.root / MODULE.STATE / 'rt-floor').exists(), 'nothing of ours to restore')
        self.put(MODULE.RT_FLOOR_KNOB, b'412\n')
        self.run_mode('boost-off', Machine(self.root))
        self.assertEqual(knob.read_text(), '412\n', 'an owner value is never replaced by a stale save')

    def test_boost_on_twice_still_restores_the_replaced_floor(self):
        self.boost_ready()
        knob = self.root / MODULE.RT_FLOOR_KNOB
        self.run_mode('boot', Machine(self.root))
        self.assertEqual(knob.read_text(), '412\n')
        self.run_mode('boost-on', Machine(self.root))
        self.put(GATE_TRIP, b'20000\n')
        self.run_mode('boost-on', Machine(self.root))
        self.assertEqual(knob.read_text(), '412\n')
        self.run_mode('boost-off', Machine(self.root))
        self.assertEqual(knob.read_text(), '1024\n', 'repeated boost on keeps the saved floor')

    def test_no_rt_floor_without_the_guard(self):
        self.boost_ready()
        self.run_mode('boot', Machine(self.root, guard_fails=True))
        self.assertEqual((self.root / MODULE.RT_FLOOR_KNOB).read_text(), '1024\n')
        (self.root / MODULE.RT_FLOOR_KNOB).unlink()
        self.restart()
        self.assertEqual(self.run_mode('boot', Machine(self.root))['boost'], 'on', 'a kernel without uclamp still boosts')

    def test_without_the_boost_devicetree_only_idle_loads(self):
        self.put(MODULE.GUARD_STAGED, GUARD_BYTES)
        machine = Machine(self.root)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod()])
        self.assertEqual(report['gate'], 'absent')
        self.assertIn('boost waits for the boost devicetree', report['status'])
        self.assertTrue(report['boost'].startswith('waits for the boost devicetree'))

    def test_a_guard_that_differs_from_the_pin_leaves_the_gate_closed(self):
        self.boost_ready()
        self.put(MODULE.GUARD_STAGED, b'other guard module')
        machine = Machine(self.root)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod()])
        self.assertEqual((report['state'], self.gate()), ('loaded', '20000'))
        self.assertIn('differs from the pin', report['status'])

    def test_a_failed_guard_load_keeps_idle_and_the_gate_closed(self):
        self.boost_ready()
        machine = Machine(self.root, guard_fails=True)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod(), self.guard_insmod()])
        self.assertEqual((report['state'], report['guard_loaded'], self.gate()), ('loaded', False, '20000'))
        self.assertIn('loading j713_pguard.ko failed', report['status'])
        self.assertFalse(self.marker().exists())

    def test_a_gate_open_without_the_guard_is_closed_before_the_guard_loads(self):
        self.boost_ready(gate='150000')
        machine = Machine(self.root, guard_fails=True)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod(), self.guard_insmod()])
        self.assertEqual((self.gate(), report['boost']), ('20000', 'starts with the CPU idle driver at the next boot'),
                         'never open without the guard')
        self.restart()
        self.put(GATE_TRIP, b'150000\n')
        self.assertEqual(self.run_mode('boot', Machine(self.root))['boost'], 'on')

    def test_boost_off_closes_the_gate_now_and_at_every_boot_until_on(self):
        self.boost_ready()
        self.run_mode('boot', Machine(self.root))
        report = self.run_mode('boost-off', Machine(self.root))
        self.assertEqual(self.gate(), '20000', 'closed at once, holding P at 1800 MHz')
        self.assertEqual(report['rt_floor'], '1024', 'the floor it replaced, the kernel default here')
        self.assertTrue(report['boost'].startswith('off; P stays at 1800 MHz'))
        self.restart()
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('boot', machine)['boost'], report['boost'])
        self.assertEqual(machine.commands, [self.idle_insmod()], 'CPU idle still loads')
        report = self.run_mode('boost-on', Machine(self.root))
        self.assertEqual((self.gate(), report['boost']), ('20000', 'starts with the CPU idle driver at the next boot'))
        self.restart()
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('boot', machine)['boost'], 'on')
        self.assertEqual(machine.commands, [self.idle_insmod(), self.guard_insmod()])

    def test_trips_raised_by_hand_are_reset_even_with_boost_off(self):
        self.boost_ready()
        self.run_mode('boost-off', Machine(self.root))
        self.put(GATE_TRIP, b'150000\n')
        self.put(SUSTAIN_TRIP, b'100000\n')
        machine = Machine(self.root)
        self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod()], 'boost is off: no guard')
        self.assertEqual((self.gate(), self.sustain()), ('20000', '95000'), 'nothing raised without the guard')

    def test_boost_on_without_the_guard_leaves_both_trips(self):
        self.boost_ready()
        self.run_mode('boost-on', Machine(self.root))
        self.assertEqual((self.gate(), self.sustain()), ('20000', '95000'))

    def test_boost_on_reopens_the_gate_only_behind_a_loaded_guard(self):
        self.boost_ready()
        self.run_mode('boot', Machine(self.root))
        self.run_mode('boost-off', Machine(self.root))
        machine = Machine(self.root)
        report = self.run_mode('boost-on', machine)
        self.assertEqual((self.gate(), report['boost'], machine.commands), ('150000', 'on', []))
        self.assertEqual(report['rt_floor'], str(MODULE.RT_FLOOR))
        self.put(GATE_TRIP, b'20000\n')
        self.assertTrue(self.run_mode('inspect', Machine(self.root))['boost'].startswith('off; the gate was closed'))

    def test_start_adds_the_guard_to_a_loaded_idle_driver(self):
        self.run_mode('boot', Machine(self.root))
        self.boost_ready()
        machine = Machine(self.root)
        report = self.run_mode('start', machine)
        self.assertEqual(machine.commands, [self.guard_insmod()])
        self.assertEqual((machine.marked, machine.slept), ([True], [(120, True)]))
        self.assertFalse(self.marker().exists())
        self.assertEqual((report['status'], report['boost']), ('current; boost guard loaded', 'on'))
        again = Machine(self.root)
        self.assertEqual(self.run_mode('start', again)['status'], 'current')
        self.assertEqual(again.commands, [])

    def test_boost_switch_needs_root_and_one_action(self):
        with mock.patch.object(MODULE.os, 'geteuid', return_value=1000), \
                mock.patch.object(MODULE, 'run') as run:
            self.assertEqual(MODULE.main(['--boost', 'off']), 1)
            run.assert_not_called()
        with self.assertRaises(SystemExit):
            MODULE.main(['--boost', 'off', '--start'])
        with self.assertRaises(SystemExit):
            MODULE.main(['--boost', 'sometimes'])

    def test_pins_match_the_channel_and_the_package(self):
        import json
        channel = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
        real = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(real)
        self.assertEqual(channel['cpu_idle_module'], {real.STAGED: real.PIN['sha256']})
        self.assertEqual(channel['cpu_guard_module'], {real.GUARD_STAGED: real.GUARD_PIN['sha256']})
        self.assertEqual(real.RELEASE, channel['kernel']['release'])
        pkgbuild = (ROOT / 'packaging/omarchy-m4/omarchy-m4-cpu/PKGBUILD').read_text()
        for pin, name in ((real.PIN, 'j713_cpuidle'), (real.GUARD_PIN, 'j713_pguard')):
            self.assertIn(pin['sha256'], pkgbuild)
            self.assertIn(f"{name}-{pin['sha256'][:8]}.ko", pkgbuild)
            self.assertIn(f'"$pkgdir/usr/lib/omarchy-m4/cpu/{name}.ko"', pkgbuild)

    def test_unit_runs_the_boot_load_without_holding_up_boot(self):
        unit = (ROOT / 'packaging/omarchy-m4/omarchy-m4-cpu/m4-cpu-idle.service').read_text()
        self.assertIn('ExecStart=-/usr/bin/m4-cpu-idle --boot', unit)
        self.assertIn('Type=exec', unit)
        self.assertIn('WantedBy=multi-user.target', unit)
        install = (ROOT / 'packaging/omarchy-m4/omarchy-m4-cpu/omarchy-m4-cpu.install').read_text()
        self.assertIn('systemctl enable m4-cpu-idle.service', install)
        self.assertIn('systemctl disable m4-cpu-idle.service', install)


class CpuIdleOrderlyRetry(unittest.TestCase):
    """Only a proven orderly shutdown earns one automatic retry of a leftover marker."""

    put = CpuIdleLoad.put
    marker = CpuIdleLoad.marker
    boost_ready = CpuIdleLoad.boost_ready
    restart = CpuIdleLoad.restart
    idle_insmod = CpuIdleLoad.idle_insmod
    guard_insmod = CpuIdleLoad.guard_insmod

    BOOT_ID = 'proc/sys/kernel/random/boot_id'
    CURRENT = '0123456789abcdef0123456789abcdef'
    PREVIOUS = 'fedcba9876543210fedcba9876543210'
    IDLE = 'loading the CPU idle driver'
    GUARD = 'loading the P-cluster boost guard'
    REFUSAL = ('A previous boot stopped while loading the CPU idle driver, so this boot loads none. '
               'Run sudo m4-cpu-idle --start to try again in the running session')
    RETRIED = 'retried after an orderly restart left the attempt marker; '
    LIST = ['--list-boots', '-o', 'json']

    def setUp(self):
        CpuIdleLoad.setUp(self)
        self.put(self.BOOT_ID, b' 01234567-89AB-CDEF-0123-456789ABCDEF\n')
        self.journal_calls = []
        self.boots = [{'boot_id': self.PREVIOUS, 'first_entry': 1000000, 'last_entry': 3000000}]
        self.shutdown = 'Syncing filesystems and block devices.\nSending SIGTERM to remaining processes...\n'

    def journal(self, args):
        self.journal_calls.append(args)
        if args == self.LIST:
            return 0, json.dumps(self.boots)
        return 0, self.shutdown

    def attempt(self, text):
        self.put(MODULE.STATE + '/attempt', text.encode())
        os.utime(self.marker(), ns=(2000000000, 2000000000))

    def run_mode(self, mode, machine, journal=None):
        return MODULE.run(self.root, mode, machine, settle_seconds=120, sleep=machine.sleep,
                          journal=self.journal if journal is None else journal)

    def assert_struck(self, journal=None):
        machine = Machine(self.root)
        with self.assertRaises(MODULE.Refused) as caught:
            self.run_mode('boot', machine, journal)
        self.assertEqual(str(caught.exception), self.REFUSAL)
        self.assertEqual((machine.commands, machine.slept), ([], []))
        self.assertTrue(self.marker().exists() or self.marker().is_symlink())

    def test_legacy_marker_with_one_orderly_boot_retries_under_a_new_durable_marker(self):
        for first in (self.IDLE, self.GUARD):
            with self.subTest(first=first):
                self.attempt(first + '\n')
                self.journal_calls.clear()
                machine = Machine(self.root)
                remove = MODULE.remove_state
                sync = MODULE.sync_directory
                with mock.patch.object(MODULE, 'remove_state', wraps=remove) as removed, \
                        mock.patch.object(MODULE, 'sync_directory', wraps=sync) as synced:
                    def runner(command):
                        removed.assert_called_once_with(self.root, 'attempt')
                        self.assertEqual(synced.call_count, 2, 'removal and replacement must both be durable')
                        return machine(command)

                    report = MODULE.run(self.root, 'boot', runner, 120, machine.sleep, journal=self.journal)
                self.assertEqual(machine.commands, [self.idle_insmod()])
                self.assertEqual(machine.markers, [self.IDLE + '\nboot_id=' + self.CURRENT + '\nretry=1\n'])
                self.assertFalse(self.marker().exists())
                self.assertEqual(report['state'], 'loaded')
                self.assertTrue(report['status'].startswith(self.RETRIED))
                self.assertEqual(self.journal_calls[0], self.LIST)
                self.assertEqual(self.journal_calls[1], ['-b', self.PREVIOUS,
                                 'SYSLOG_IDENTIFIER=systemd-shutdown', '-o', 'cat', '-q', '--no-pager'])
                self.assertEqual(len(self.journal_calls), 2)
                self.restart()

    def test_boot_id_marker_uses_its_boot_without_mtime_lookup_and_loads_both_modules(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        os.utime(self.marker(), ns=(9000000000, 9000000000))
        self.boost_ready()
        machine = Machine(self.root)
        report = self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [self.idle_insmod(), self.guard_insmod()])
        self.assertEqual(machine.markers, [self.IDLE + '\nboot_id=' + self.CURRENT + '\nretry=1\n'] * 2)
        self.assertEqual((report['state'], report['boost']), ('loaded', 'on'))
        self.assertTrue(report['status'].startswith(self.RETRIED))
        self.assertFalse(self.marker().exists())
        self.assertEqual(self.journal_calls, [['-b', self.PREVIOUS, 'SYSLOG_IDENTIFIER=systemd-shutdown',
                                              '-o', 'cat', '-q', '--no-pager']])

    def test_abrupt_boot_keeps_the_exact_refusal_and_marker(self):
        for shutdown in ('', 'Reached target System Reboot.\n', 'Sending SIGKILL to remaining processes...\n'):
            with self.subTest(shutdown=shutdown):
                self.shutdown = shutdown
                text = self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n'
                self.attempt(text)
                self.assert_struck()
                self.assertEqual(self.marker().read_text(), text)

    def test_journal_failure_missing_command_timeout_and_garbage_keep_the_strike(self):
        for stage in ('list', 'shutdown'):
            for failure in ((1, self.shutdown), (1, json.dumps(self.boots)), (0, 'garbage'), FileNotFoundError('journalctl'),
                            subprocess.TimeoutExpired('journalctl', 30), OSError('journal unreadable')):
                with self.subTest(stage=stage, failure=failure):
                    self.attempt(self.IDLE + '\n')

                    def journal(args):
                        if stage == 'shutdown' and args == self.LIST:
                            return self.journal(args)
                        if isinstance(failure, Exception):
                            raise failure
                        return failure

                    self.assert_struck(journal)

    def test_failed_boot_list_with_valid_stdout_keeps_the_exact_refusal_and_marker(self):
        self.attempt(self.IDLE + '\n')
        original = self.marker().read_bytes()

        def journal(args):
            code, output = self.journal(args)
            return (1 if args == self.LIST else code), output

        self.assert_struck(journal)
        self.assertEqual(self.journal_calls, [self.LIST])
        self.assertEqual(self.marker().read_bytes(), original)
        self.assertEqual(self.run_mode('inspect', Machine(self.root))['state'], 'struck')

    def test_legacy_mtime_must_match_exactly_one_boot(self):
        match = self.boots[0]
        for boots in ([], [dict(match, first_entry=2000001)], [dict(match, last_entry=1999999)],
                      [match, dict(match, boot_id='a' * 32)], [match, match]):
            with self.subTest(boots=boots):
                self.attempt(self.IDLE + '\n')
                self.boots = boots
                self.journal_calls.clear()
                self.assert_struck()
                self.assertEqual(self.journal_calls, [self.LIST])

    def test_legacy_mtime_includes_both_interval_endpoints(self):
        for timestamp in (1000000000, 3000000000):
            with self.subTest(timestamp=timestamp):
                self.attempt(self.IDLE)
                os.utime(self.marker(), ns=(timestamp, timestamp))
                self.assertEqual(self.run_mode('boot', Machine(self.root))['state'], 'loaded')
                self.restart()

    def test_malformed_boot_lists_never_prove_an_orderly_boot(self):
        good = self.boots[0]
        for boots in ({}, None, [None], [{}], [dict(good, boot_id='bad')],
                      [dict(good, first_entry='1000000')], [dict(good, last_entry=True)],
                      [dict(good, first_entry=True)], [dict(good, first_entry=1000000.0)],
                      [dict(good, last_entry=3000000.0)],
                      [dict(good, first_entry=-1)], [dict(good, first_entry=4000000)],
                      [good, dict(good, boot_id='bad', first_entry=4000000, last_entry=5000000)],
                      [good, {}]):
            with self.subTest(boots=boots):
                self.attempt(self.IDLE + '\n')
                self.boots = boots
                self.assert_struck()

    def test_the_current_boot_never_earns_a_retry(self):
        for legacy in (False, True):
            with self.subTest(legacy=legacy):
                self.attempt(self.IDLE + ('\n' if legacy else '\nboot_id=' + self.CURRENT + '\n'))
                self.boots[0]['boot_id'] = self.CURRENT
                self.journal_calls.clear()
                self.assert_struck()
                self.assertEqual(self.journal_calls, [self.LIST] if legacy else [])

    def test_missing_or_invalid_current_boot_id_cannot_prove_a_different_boot(self):
        for content in (None, b'bad', b'\xff'):
            with self.subTest(content=content):
                if content is None:
                    (self.root / self.BOOT_ID).unlink()
                else:
                    self.put(self.BOOT_ID, content)
                self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
                self.assert_struck()

    def test_retry_markers_never_clear_automatically(self):
        for first in (self.IDLE, self.GUARD):
            for boot in ('', 'boot_id=' + self.PREVIOUS + '\n'):
                with self.subTest(first=first, boot=boot):
                    self.attempt(first + '\n' + boot + 'retry=1\n')
                    self.assert_struck()
                    self.assertEqual(self.journal_calls, [])

    def test_unknown_binary_oversized_and_malformed_markers_keep_the_strike(self):
        valid = self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n'
        for content in (b'', b'loading\n', valid.encode() + b'unknown\n', valid.encode() + b'\xff',
                        valid.encode() + b'\0', (valid + 'retry=0\n').encode(),
                        (valid + 'boot_id=' + self.PREVIOUS + '\n').encode(),
                        (valid + '\n').encode(), (self.IDLE + '\n' + 'x' * 4096).encode()):
            with self.subTest(content=content[:100]):
                self.put(MODULE.STATE + '/attempt', content)
                os.utime(self.marker(), ns=(2000000000, 2000000000))
                self.assert_struck()
                self.assertEqual(self.marker().read_bytes(), content)
        for boot in ('', 'A' * 32, 'g' * 32, 'a' * 31, 'a' * 33, '../' + 'a' * 29,
                     '01234567-89ab-cdef-0123-456789abcdef'):
            with self.subTest(boot=boot):
                self.attempt(self.IDLE + '\nboot_id=' + boot + '\n')
                self.assert_struck()

    def test_oversized_marker_is_rejected_even_if_the_prefix_looks_valid(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        with mock.patch.object(MODULE.os, 'fstat', return_value=types.SimpleNamespace(
                st_mode=0o100600, st_size=4097, st_mtime_ns=2000000000)):
            self.assert_struck()

    def test_marker_read_is_bounded_and_rejects_growth_after_stat(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        fdopen = MODULE.os.fdopen
        journal = mock.Mock(return_value=(0, self.shutdown))
        streams = []

        def opened(fd, mode):
            stream = fdopen(fd, mode)
            proxy = mock.MagicMock(wraps=stream)
            proxy.__enter__.return_value = proxy
            proxy.__exit__.side_effect = stream.__exit__
            data = mock.MagicMock()
            data.__len__.return_value = 4097
            data.decode.return_value = self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n'
            proxy.read.return_value = data
            streams.append(proxy)
            return proxy

        with mock.patch.object(MODULE.os, 'fdopen', side_effect=opened) as opened_file:
            self.assertFalse(MODULE.orderly_attempt(self.root, journal))
        opened_file.assert_called_once()
        streams[0].read.assert_called_once_with(4097)
        journal.assert_not_called()

    def test_nonregular_metadata_is_rejected_before_reading(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        with mock.patch.object(MODULE.os, 'fstat', return_value=types.SimpleNamespace(
                st_mode=0o010600, st_size=100, st_mtime_ns=2000000000)):
            self.assert_struck()

    def test_unreadable_marker_keeps_the_strike(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        real_open = MODULE.os.open

        def opened(path, flags, *args):
            if path == self.marker():
                raise PermissionError('unreadable marker')
            return real_open(path, flags, *args)

        with mock.patch.object(MODULE.os, 'open', side_effect=opened):
            self.assert_struck()

    def test_symlink_directory_and_fifo_markers_stay_struck(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        target = self.marker().with_name('target')
        self.marker().rename(target)
        self.marker().symlink_to(target)
        self.assert_struck()
        target.unlink()  # a dangling symlink is still a strike
        self.assert_struck()
        self.marker().unlink()
        self.marker().mkdir()
        self.assert_struck()
        self.marker().rmdir()
        os.mkfifo(self.marker())
        self.assert_struck()
        self.assertEqual(self.journal_calls, [])

    def test_marker_open_uses_nofollow_and_nonblocking(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        real_open = MODULE.os.open

        def opened(path, flags, *args):
            if path == self.marker() and not flags & os.O_WRONLY:
                self.assertTrue(flags & os.O_NOFOLLOW)
                self.assertTrue(flags & os.O_NONBLOCK, 'opening a FIFO must never wait')
            return real_open(path, flags, *args)

        with mock.patch.object(MODULE.os, 'open', side_effect=opened):
            self.assertEqual(self.run_mode('boot', Machine(self.root))['state'], 'loaded')

    def test_a_retry_that_dies_stays_struck_after_even_an_orderly_shutdown(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')

        def died(command):
            self.assertEqual(command, self.idle_insmod())
            raise SystemExit('the load never returned')

        with self.assertRaises(SystemExit):
            MODULE.run(self.root, 'boot', died, sleep=lambda seconds: None, journal=self.journal)
        self.assertEqual(self.marker().read_text(), self.IDLE + '\nboot_id=' + self.CURRENT + '\nretry=1\n')
        self.put(self.BOOT_ID, ('a' * 32).encode())
        journal = mock.Mock(return_value=(0, self.shutdown))
        self.assert_struck(journal)
        journal.assert_not_called()

    def test_start_clears_a_retry_marker_without_consulting_the_journal(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\nretry=1\n')
        journal = mock.Mock(side_effect=AssertionError('start must not read the journal'))
        machine = Machine(self.root)
        report = self.run_mode('start', machine, journal)
        self.assertEqual(report['state'], 'loaded')
        self.assertFalse(report['status'].startswith(self.RETRIED))
        self.assertEqual(machine.markers, [self.IDLE + '\nboot_id=' + self.CURRENT + '\n'])
        self.assertFalse(self.marker().exists())
        journal.assert_not_called()

    def test_start_with_an_orderly_legacy_marker_never_uses_automatic_retry(self):
        self.attempt(self.IDLE + '\n')
        journal = mock.Mock(wraps=self.journal)
        machine = Machine(self.root)
        report = self.run_mode('start', machine, journal)
        journal.assert_not_called()
        self.assertEqual(report['state'], 'loaded')
        self.assertEqual(machine.commands, [self.idle_insmod()])
        self.assertEqual(machine.markers, [self.IDLE + '\nboot_id=' + self.CURRENT + '\n'])
        self.assertFalse(report['status'].startswith(self.RETRIED))
        self.assertFalse(self.marker().exists())

    def test_new_idle_and_guard_markers_record_the_normalized_boot_id(self):
        machine = Machine(self.root)
        self.run_mode('boot', machine)
        self.assertEqual(machine.markers, [self.IDLE + '\nboot_id=' + self.CURRENT + '\n'])
        self.boost_ready()
        machine = Machine(self.root)
        self.run_mode('start', machine)
        self.assertEqual(machine.markers, [self.GUARD + '\nboot_id=' + self.CURRENT + '\n'])
        self.assertEqual(self.journal_calls, [])

    def test_new_markers_without_a_readable_boot_id_keep_the_existing_first_line(self):
        (self.root / self.BOOT_ID).unlink()
        machine = Machine(self.root)
        self.run_mode('boot', machine)
        self.assertEqual(machine.markers, [self.IDLE + '\n'])
        self.boost_ready()
        machine = Machine(self.root)
        self.run_mode('start', machine)
        self.assertEqual(machine.markers, [self.GUARD + '\n'])

    def test_inspection_enable_and_disable_never_consult_the_journal_or_clear_a_marker(self):
        text = self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n'
        self.attempt(text)
        journal = mock.Mock(side_effect=AssertionError('only boot may query the journal'))
        for mode in ('inspect', 'disable', 'enable'):
            self.run_mode(mode, Machine(self.root), journal)
            self.assertEqual(self.marker().read_text(), text)
        journal.assert_not_called()

    def test_a_hold_prevents_recovery_and_journal_access(self):
        self.attempt(self.IDLE + '\nboot_id=' + self.PREVIOUS + '\n')
        self.put(MODULE.STATE + '/hold', b'off\n')
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'CPU idle is switched off'):
            self.run_mode('boot', machine)
        self.assertEqual((machine.commands, self.journal_calls), ([], []))
        self.assertTrue(self.marker().exists())

    def test_default_journal_command_captures_output_and_bounds_each_call(self):
        self.attempt(self.IDLE + '\n')
        replies = [types.SimpleNamespace(returncode=0, stdout=json.dumps(self.boots)),
                   types.SimpleNamespace(returncode=0, stdout=self.shutdown)]
        machine = Machine(self.root)
        with mock.patch.object(MODULE.subprocess, 'run', side_effect=replies) as command:
            report = MODULE.run(self.root, 'boot', machine, 120, machine.sleep)
        self.assertEqual(report['state'], 'loaded')
        self.assertEqual(command.call_count, 2)
        for call in command.call_args_list:
            self.assertEqual(call.args[0][0], 'journalctl')
            self.assertEqual(call.kwargs, {'capture_output': True, 'text': True, 'check': False, 'timeout': 30})


class CpuIdleStopSignal(unittest.TestCase):
    """A stop inside the settle window (#130): SIGTERM (clean stop) must clear the attempt
    marker and release the lock, so the next boot loads; SIGKILL (abrupt death) must keep the
    marker, so the next boot refuses until sudo m4-cpu-idle --start. The child runs the real
    main() and is signaled only after it is provably inside the window. A child that ignored
    SIGTERM would still be waiting on the release file at FAILSAFE_SECONDS, so exiting within
    EXIT_TIMEOUT is caused by the signal, not by the harness."""

    # The same fake-machine fixture as CpuIdleLoad, reused by assignment so its tests do not rerun.
    setUp = CpuIdleLoad.setUp
    put = CpuIdleLoad.put
    marker = CpuIdleLoad.marker
    boost_ready = CpuIdleLoad.boost_ready
    restart = CpuIdleLoad.restart
    run_mode = CpuIdleLoad.run_mode

    READY_TIMEOUT = 15.0
    EXIT_TIMEOUT = 15.0

    @contextlib.contextmanager
    def blocked_child(self, mode, window='settle'):
        """The real main() in a child process, blocked at the requested window; reaped and its
        pipes drained and closed on every exit path. child.out / child.err hold the captures."""
        with tempfile.TemporaryDirectory() as sync:
            proc = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), '--child-settle', mode,
                 str(self.root), sync, window],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            child = types.SimpleNamespace(proc=proc, sync=Path(sync), out=None, err=None)
            try:
                deadline = time.monotonic() + self.READY_TIMEOUT
                while not (child.sync / 'ready').exists():
                    if proc.poll() is not None:
                        self.fail('child exited before the ' + window + ' window: '
                                  + (proc.stderr.read() or '<no stderr>'))
                    if time.monotonic() > deadline:
                        self.fail('child never reached the ' + window + ' window')
                    time.sleep(0.02)
                yield child
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
                child.out, child.err = proc.stdout.read(), proc.stderr.read()
                proc.stdout.close()
                proc.stderr.close()

    def blocked_entry(self, sync):
        return json.loads((sync / 'blocked.json').read_text())

    def test_sigterm_during_boot_settle_clears_the_marker_for_the_next_boot(self):
        with self.blocked_child('boot') as child:
            self.assertEqual(self.blocked_entry(child.sync),
                             {'phase': 'settle', 'seconds': 120, 'marked': True, 'lock_free': False},
                             'the signal must land inside the settle window, under the marker and the lock')
            child.proc.send_signal(signal.SIGTERM)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        # The exit code and the stderr note are the orderly-stop convention; the marker, the
        # lock and the next boot are the contract. A completed run would print a report.
        self.assertEqual((child.proc.returncode, child.out), (0, ''))
        self.assertIn('stopped', child.err)
        self.assertFalse(self.marker().exists(), 'a clean stop during settle is not a strike')
        with MODULE.Locked(self.root):  # the stopped loader must not keep the lock
            pass
        self.restart()
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('boot', machine)['state'], 'loaded',
                         'the boot after a clean stop loads the driver again')
        self.assertEqual(machine.commands, [['insmod', str(self.root / MODULE.STAGED)]])

    def test_sigkill_during_boot_settle_stops_the_next_boot_until_start(self):
        with self.blocked_child('boot') as child:
            self.assertTrue(self.blocked_entry(child.sync)['marked'])
            child.proc.send_signal(signal.SIGKILL)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual(child.proc.returncode, -signal.SIGKILL)
        self.assertEqual(child.out, '')
        self.assertTrue(self.marker().exists(), 'an abrupt kill keeps the strike marker')
        self.restart()
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])
        self.assertTrue(self.marker().exists(), 'no auto-clearing a strike of unknown reason')
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('start', machine)['state'], 'loaded',
                         'sudo m4-cpu-idle --start still recovers an already-struck install')
        self.assertFalse(self.marker().exists())

    def test_sigterm_during_start_load_settle_clears_the_marker_for_the_next_boot(self):
        self.put(MODULE.STATE + '/attempt', b'loading the CPU idle driver\n')  # a struck install
        with self.blocked_child('start') as child:
            self.assertEqual(self.blocked_entry(child.sync),
                             {'phase': 'settle', 'seconds': 120, 'marked': True, 'lock_free': False},
                             '--start loads the driver and settles under the marker')
            child.proc.send_signal(signal.SIGTERM)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual((child.proc.returncode, child.out), (0, ''))
        self.assertFalse(self.marker().exists(), 'a clean stop of a --start load is not a strike')
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('boot', machine)['status'], 'current',
                         'the driver the stopped --start loaded stays in charge')
        self.assertEqual(machine.commands, [])
        self.restart()
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('boot', machine)['state'], 'loaded',
                         'a full boot after the stop loads the driver again')

    def test_sigkill_during_start_load_settle_stops_the_next_boot_until_start(self):
        with self.blocked_child('start') as child:
            self.assertTrue(self.blocked_entry(child.sync)['marked'])
            child.proc.send_signal(signal.SIGKILL)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual(child.proc.returncode, -signal.SIGKILL)
        self.assertTrue(self.marker().exists(), 'a kill while --start settles is still a strike')
        self.restart()
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('start', machine)['state'], 'loaded',
                         'sudo m4-cpu-idle --start still recovers an already-struck install')
        self.assertFalse(self.marker().exists())

    def test_sigterm_during_guard_retry_settle_clears_the_marker_for_the_next_boot(self):
        self.assertEqual(self.run_mode('boot', Machine(self.root))['state'], 'loaded')
        self.boost_ready()
        with self.blocked_child('start') as child:
            self.assertEqual(self.blocked_entry(child.sync),
                             {'phase': 'settle', 'seconds': 120, 'marked': True, 'lock_free': False},
                             'the signal must land inside the guard-only settle window')
            child.proc.send_signal(signal.SIGTERM)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual((child.proc.returncode, child.out), (0, ''))
        self.assertFalse(self.marker().exists(), 'a clean stop while adding the guard is not a strike')
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('boot', machine)['status'], 'current',
                         'the next boot is not refused; the guard it loaded stays')
        self.assertEqual(machine.commands, [])
        self.restart()
        machine = Machine(self.root)
        report = self.run_mode('boot', machine)
        self.assertEqual((report['state'], report['boost']), ('loaded', 'on'),
                         'a full boot after the stop loads idle and the guard')
        self.assertEqual(machine.commands, [['insmod', str(self.root / MODULE.STAGED)],
                                            ['insmod', str(self.root / MODULE.GUARD_STAGED)]])

    def test_sigkill_during_guard_retry_settle_stops_the_next_boot_until_start(self):
        self.assertEqual(self.run_mode('boot', Machine(self.root))['state'], 'loaded')
        self.boost_ready()
        with self.blocked_child('start') as child:
            self.assertTrue(self.blocked_entry(child.sync)['marked'])
            child.proc.send_signal(signal.SIGKILL)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual(child.proc.returncode, -signal.SIGKILL)
        self.assertTrue(self.marker().exists())
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])
        machine = Machine(self.root)
        self.assertEqual(self.run_mode('start', machine)['status'], 'current',
                         'sudo m4-cpu-idle --start clears the strike; the loaded guard stays')
        self.assertFalse(self.marker().exists())
        self.restart()
        report = self.run_mode('boot', Machine(self.root))
        self.assertEqual((report['state'], report['boost']), ('loaded', 'on'),
                         'a full boot after the recovery loads idle and the guard')

    def test_sigterm_during_a_pending_module_load_stays_a_strike(self):
        """The orderly-stop handling covers only the settle window: a stop while a module load
        is still pending keeps the default disposition, the marker survives and the next boot
        refuses, because that load's outcome is unknowable."""
        with self.blocked_child('boot', window='load') as child:
            self.assertEqual(self.blocked_entry(child.sync),
                             {'phase': 'load', 'seconds': None, 'marked': True, 'lock_free': False},
                             'the signal lands while the insmod is pending, under the marker')
            child.proc.send_signal(signal.SIGTERM)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual(child.proc.returncode, -signal.SIGTERM,
                         'outside the settle window SIGTERM keeps its default disposition')
        self.assertTrue(self.marker().exists(), 'a pending load is never silently cleared')
        self.restart()
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])

    def test_sigterm_during_a_pending_guard_load_stays_a_strike(self):
        """Same scoping at boot with the boost devicetree: the idle driver is already loaded and
        the guard's insmod is pending; a stop there is still a strike, not a silent clear."""
        self.boost_ready()
        with self.blocked_child('boot', window='guard-load') as child:
            self.assertEqual(self.blocked_entry(child.sync),
                             {'phase': 'guard-load', 'seconds': None, 'marked': True, 'lock_free': False},
                             'the signal lands while the guard insmod is pending, under the marker')
            child.proc.send_signal(signal.SIGTERM)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual(child.proc.returncode, -signal.SIGTERM,
                         'outside the settle window SIGTERM keeps its default disposition')
        self.assertTrue(self.marker().exists(), 'a pending guard load is never silently cleared')
        self.restart()
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])

    def test_sigterm_during_a_pending_guard_load_on_start_stays_a_strike(self):
        """Same scoping for the guard-only --start retry: the guard's insmod is pending inside
        start_guard's attempt window; a stop there is still a strike."""
        self.assertEqual(self.run_mode('boot', Machine(self.root))['state'], 'loaded')
        self.boost_ready()
        with self.blocked_child('start', window='guard-load') as child:
            self.assertEqual(self.blocked_entry(child.sync),
                             {'phase': 'guard-load', 'seconds': None, 'marked': True, 'lock_free': False},
                             'the signal lands while the guard insmod is pending, under the marker')
            child.proc.send_signal(signal.SIGTERM)
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual(child.proc.returncode, -signal.SIGTERM,
                         'outside the settle window SIGTERM keeps its default disposition')
        self.assertTrue(self.marker().exists(), 'a pending guard load is never silently cleared')
        self.restart()
        machine = Machine(self.root)
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            self.run_mode('boot', machine)
        self.assertEqual(machine.commands, [])

    def test_an_undisturbed_child_boot_loads_and_clears_the_marker(self):
        """Harness control: released from the settle window, the child boots, settles and exits 0."""
        with self.blocked_child('boot') as child:
            self.assertEqual(self.blocked_entry(child.sync)['phase'], 'settle')
            (child.sync / 'release').write_text('go\n')
            child.proc.wait(timeout=self.EXIT_TIMEOUT)
        self.assertEqual((child.proc.returncode, child.err), (0, ''))
        self.assertEqual(json.loads(child.out)['state'], 'loaded')
        self.assertFalse(self.marker().exists())

    def test_the_settle_window_restores_the_previous_sigterm_disposition(self):
        original = signal.getsignal(signal.SIGTERM)
        calls = []
        signal.signal(signal.SIGTERM, lambda signum, frame: calls.append(signum))
        self.addCleanup(signal.signal, signal.SIGTERM, original)
        sentinel = signal.getsignal(signal.SIGTERM)
        self.run_mode('boot', Machine(self.root))
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel, 'restored after a normal settle')
        self.restart()
        with self.assertRaisesRegex(MODULE.Refused, 'no core powered down'):
            self.run_mode('boot', Machine(self.root, idles=False))
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel, 'restored after a failing settle')
        self.assertEqual(calls, [], 'no signal arrived; the sentinel must never run')

    def test_the_settle_window_restores_the_disposition_when_the_sleep_itself_raises(self):
        original = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda signum, frame: None)
        self.addCleanup(signal.signal, signal.SIGTERM, original)
        sentinel = signal.getsignal(signal.SIGTERM)
        machine = Machine(self.root)

        def exploding_sleep(seconds):
            raise OSError('the wait itself failed')

        with self.assertRaises(OSError):
            MODULE.run(self.root, 'boot', machine, settle_seconds=120, sleep=exploding_sleep)
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel,
                      'restored after an exception raised inside the settle wait')
        self.assertFalse(self.marker().exists(), 'the finally still clears the marker')
        with MODULE.Locked(self.root):  # the lock is released too
            pass

    def test_sigterm_in_the_settle_wait_unwinds_as_stopped_with_the_prior_handler_restored(self):
        original = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGTERM, lambda signum, frame: None)
        self.addCleanup(signal.signal, signal.SIGTERM, original)
        sentinel = signal.getsignal(signal.SIGTERM)
        machine = Machine(self.root)

        def signaling_sleep(seconds):
            os.kill(os.getpid(), signal.SIGTERM)

        with self.assertRaises(MODULE.Stopped):
            MODULE.run(self.root, 'boot', machine, settle_seconds=120, sleep=signaling_sleep)
        self.assertIs(signal.getsignal(signal.SIGTERM), sentinel,
                      'the handler restores the previous disposition before unwinding')
        self.assertFalse(self.marker().exists(), 'an orderly stop clears the marker')
        with MODULE.Locked(self.root):
            pass


class CpuIdleInstallScriptlet(unittest.TestCase):
    """The package scriptlet explains boot recovery and --start, keeps its existing notices,
    and never clears the marker or starts anything itself."""

    INSTALL = ROOT / 'packaging/omarchy-m4/omarchy-m4-cpu/omarchy-m4-cpu.install'

    def scriptlet_root(self, struck):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        (root / 'bin').mkdir()
        systemctl = root / 'bin' / 'systemctl'
        systemctl.write_text('#!/bin/sh\necho "systemctl $@" >> "$SYSTEMCTL_LOG"\n')
        systemctl.chmod(0o755)
        if struck:
            marker = root / 'var/lib/omarchy-m4/cpu-idle/attempt'
            marker.parent.mkdir(parents=True)
            marker.write_text('loading the CPU idle driver\n')
        return root

    def run_hook(self, root, hook):
        env = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                   SYSTEMCTL_LOG=str(root / 'systemctl.log'))
        return subprocess.run(['bash', '-c', 'source "$1" && shift && "$@"', 'bash',
                               str(self.INSTALL), hook],
                              cwd=root, env=env, capture_output=True, text=True, check=True).stdout

    def systemctl_calls(self, root):
        log = root / 'systemctl.log'
        return log.read_text().splitlines() if log.exists() else []

    def test_post_upgrade_points_a_struck_install_at_start_and_preserves_the_marker(self):
        root = self.scriptlet_root(struck=True)
        out = self.run_hook(root, 'post_upgrade')
        self.assertIn('Restart once to let idle CPU cores power down', out, 'the install notice stays')
        self.assertIn('sudo m4-cpu-idle --start', out, 'the recovery is discoverable')
        self.assertIn('next boot retries once if that boot shut down in order', out)
        self.assertIn('no retry was already used', out)
        self.assertTrue((root / 'var/lib/omarchy-m4/cpu-idle/attempt').exists(),
                        'the scriptlet never clears a marker: its reason is unknowable')
        self.assertEqual(self.systemctl_calls(root), ['systemctl enable m4-cpu-idle.service'],
                         'the hook starts nothing')

    def test_post_upgrade_is_quiet_about_a_marker_that_is_not_there(self):
        out = self.run_hook(self.scriptlet_root(struck=False), 'post_upgrade')
        self.assertIn('Restart once to let idle CPU cores power down', out)
        self.assertNotIn('--start', out)

    def test_post_install_and_pre_remove_are_unchanged(self):
        root = self.scriptlet_root(struck=False)
        out = self.run_hook(root, 'post_install')
        self.assertIn('Restart once to let idle CPU cores power down', out)
        self.assertNotIn('--start', out)
        out = self.run_hook(root, 'pre_remove')
        self.assertIn('stays until the next restart', out)
        self.assertEqual(self.systemctl_calls(root),
                         ['systemctl enable m4-cpu-idle.service', 'systemctl disable m4-cpu-idle.service'])


class ModuleSource(unittest.TestCase):
    """The idle module never writes the locked M4 register and serves only T8132."""

    def setUp(self):
        self.source = (ROOT / 'cpu/j713/j713_cpuidle.c').read_text()
        start = self.source.index('asm(')
        self.asm = self.source[start:self.source.index(');', start)]

    def test_no_system_register_write(self):
        self.assertNotIn('msr', self.asm.lower())
        self.assertNotIn('s3_5_c15_c5_0', self.asm)

    def test_power_down_saves_state_around_wfi(self):
        self.assertIn('cpu_pm_enter()', self.source)
        self.assertIn('cpu_pm_exit()', self.source)
        pushes = self.asm.index('stp x18, x19'), self.asm.index('"wfi\\n"'), self.asm.index('ldp x18, x19')
        self.assertEqual(sorted(pushes), list(pushes), 'registers must be saved before WFI and restored after')
        self.assertIn('mrs x0, ISR_EL1', self.asm)
        enter = self.source[self.source.index('static __cpuidle int j713_enter_pd'):]
        enter = enter[:enter.index('\n}\n')]
        calls = [enter.index(c) for c in ('cpu_pm_enter()', 'ct_cpuidle_enter();', 'j713_cpu_wfi_saved();',
                                           'ct_cpuidle_exit();', 'cpu_pm_exit();')]
        self.assertEqual(sorted(calls), calls, 'the power-down state must save, idle in WFI, then restore')

    def test_t8132_only_with_a_safe_state_that_never_executes_wfi(self):
        self.assertIn('of_machine_is_compatible("apple,t8132")', self.source)
        self.assertIn('.safe_state_index = J713_IDLE_SPIN', self.source)
        spin = self.source[self.source.index('static __cpuidle int j713_enter_spin'):]
        spin = spin[:spin.index('}')]
        self.assertNotIn('wfi', spin.lower())
        self.assertNotIn('cpu_do_idle', spin)


if __name__ == '__main__':
    if sys.argv[1:2] == ['--child-settle']:
        sys.exit(child_settle_main(sys.argv[2:]))
    unittest.main()
