# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The boot-time CPU idle driver and boost guard load: pinned inputs only, one strike after a boot dies."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
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

    def __call__(self, command):
        self.commands.append(command)
        self.marked.append((self.root / MODULE.STATE / 'attempt').is_file())
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
    unittest.main()
