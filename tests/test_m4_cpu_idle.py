# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The boot-time CPU idle driver load: pinned inputs only, one strike after a boot dies."""
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
CPUS = 10


class Machine:
    """insmod against a fake sysfs; sleep advances the power-down counters while the driver is loaded."""

    def __init__(self, root, fail=False, idles=True):
        self.root, self.fail, self.idles = root, fail, idles
        self.commands, self.marked, self.slept = [], [], []

    def __call__(self, command):
        self.commands.append(command)
        self.marked.append((self.root / MODULE.STATE / 'attempt').is_file())
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
        if self.idles:
            for usage in (self.root / MODULE.CPUS).glob('cpu*/' + MODULE.POWER_DOWN + '/usage'):
                usage.write_text(str(int(usage.read_text()) + 500) + '\n')


class CpuIdleLoad(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(MODULE, 'PIN', PIN)
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

    def test_disable_while_loaded_says_it_stays_until_restart(self):
        self.run_mode('boot', Machine(self.root))
        report = self.run_mode('disable', Machine(self.root))
        self.assertEqual(report['status'], 'off; the loaded driver stays until the next restart')

    def test_non_root_is_refused_except_inspection(self):
        with mock.patch.object(MODULE.os, 'geteuid', return_value=1000), \
                mock.patch.object(MODULE, 'run') as run:
            self.assertEqual(MODULE.main(['--boot']), 1)
            run.assert_not_called()

    def test_pins_match_the_channel_and_the_package(self):
        import json
        channel = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
        real = importlib.util.module_from_spec(SPEC)
        SPEC.loader.exec_module(real)
        self.assertEqual(channel['cpu_idle_module'], {real.STAGED: real.PIN['sha256']})
        self.assertEqual(real.RELEASE, channel['kernel']['release'])
        pkgbuild = (ROOT / 'packaging/omarchy-m4/omarchy-m4-cpu/PKGBUILD').read_text()
        self.assertIn(real.PIN['sha256'], pkgbuild)
        self.assertIn(f"j713_cpuidle-{real.PIN['sha256'][:8]}.ko", pkgbuild)

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
        pushes = self.asm.index('stp x18, x19'), self.asm.index('wfi'), self.asm.index('ldp x18, x19')
        self.assertEqual(sorted(pushes), list(pushes), 'registers must be saved before WFI and restored after')
        self.assertIn('mrs x0, ISR_EL1', self.asm)

    def test_t8132_only_with_a_safe_state_that_never_executes_wfi(self):
        self.assertIn('of_machine_is_compatible("apple,t8132")', self.source)
        self.assertIn('.safe_state_index = J713_IDLE_SPIN', self.source)
        spin = self.source[self.source.index('static __cpuidle int j713_enter_spin'):]
        spin = spin[:spin.index('}')]
        self.assertNotIn('wfi', spin.lower())
        self.assertNotIn('cpu_do_idle', spin)


if __name__ == '__main__':
    unittest.main()
