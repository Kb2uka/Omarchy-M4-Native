# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The boot-time GPU module swap: only known states, one pin line, always consistent."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_gpu_module', ROOT / 'tools/m4-gpu-module.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

OLD, PRIOR, NEW = b'alpha.1 gpu module', b'earlier fixed gpu module', b'fixed gpu module'
STOCK = {'sha256': hashlib.sha256(OLD).hexdigest(), 'size': len(OLD)}
PREVIOUS = {'sha256': hashlib.sha256(PRIOR).hexdigest(), 'size': len(PRIOR)}
FIXED = {'sha256': hashlib.sha256(NEW).hexdigest(), 'size': len(NEW)}
MESA = 'f9f3247a5b0b425c15f29cf0773092e95c691e347594cd0b32278f303dc2ab05  /opt/m4-mesa/lib/libEGL.so.1.0.0\n'


def sums(digest):
    # The layout of the installed runtime.sha256: the module line first, then the Mesa files.
    return f'{digest}  /{MODULE.MODULE}\n{MESA}'.encode()


class GpuModule(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for target, values in ((MODULE.STOCK, STOCK), (MODULE.FIXED, FIXED)):
            patcher = mock.patch.dict(target, values)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(MODULE, 'PREVIOUS', (PREVIOUS,))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.module, self.sums = self.root / MODULE.MODULE, self.root / MODULE.SUMS
        self.install(OLD, sums(STOCK['sha256']))
        self.put(MODULE.STAGED, NEW)

    def put(self, name, data, mode=0o644):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)

    def install(self, module, runtime):
        self.put(MODULE.MODULE, module)
        self.put(MODULE.SUMS, runtime, 0o664)

    def run_tool(self, *args):
        output = io.StringIO()
        with mock.patch('sys.stdout', output), mock.patch('sys.stderr', io.StringIO()):
            code = MODULE.main(['--root', str(self.root), *args])
        return code, json.loads(output.getvalue()) if code == 0 else None

    def state(self):
        return self.module.read_bytes(), self.sums.read_bytes()

    def test_inspection_changes_nothing(self):
        before = self.state()
        code, report = self.run_tool()
        self.assertEqual((code, report['status']), (0, 'staged; applies at the next boot'))
        self.assertEqual(self.state(), before)

    def test_reports_the_values_the_air_gate_checks(self):
        # A refused GPU has no desktop and alpha.1's module logs nothing, so the
        # report the unit journals on every boot carries the gate's inputs.
        self.assertEqual(self.run_tool()[1]['gpu'],
                         {'core_mask': None, 'max_power_mw': None, 'firmware_build': None})
        node = MODULE.GPU_NODE + '/'
        self.put(node + 'apple,m4-core-mask', (0xff).to_bytes(4, 'big'))
        self.put(node + 'apple,m4-max-power-mw', (24304).to_bytes(4, 'big'))
        self.put(node + 'apple,firmware-build', b'mBoot-18000.120.36\0')
        before = self.state()
        self.assertEqual(self.run_tool()[1]['gpu'], {'core_mask': '0xff', 'max_power_mw': 24304,
                                                     'firmware_build': 'mBoot-18000.120.36'})
        self.assertEqual(self.run_tool('--boot')[1]['gpu']['core_mask'], '0xff')
        self.assertNotEqual(self.state(), before, 'reporting must not stop the swap')
        self.put(node + 'apple,m4-core-mask', b'\0\0\x03')
        self.put(node + 'apple,firmware-build', b'\xff\0')
        self.assertEqual(self.run_tool()[1]['gpu'], {'core_mask': None, 'max_power_mw': 24304,
                                                     'firmware_build': None})
        self.put(node + 'apple,firmware-build', b'\0\0')
        self.assertIsNone(self.run_tool()[1]['gpu']['firmware_build'])

    def test_boot_applies_module_and_only_its_pin(self):
        code, report = self.run_tool('--boot')
        self.assertEqual(code, 0)
        self.assertIn('applied', report['status'])
        self.assertEqual(self.state(), (NEW, sums(FIXED['sha256'])))
        self.assertEqual(self.module.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.sums.stat().st_mode & 0o777, 0o664)
        stage = self.root / MODULE.STATE
        self.assertEqual((stage / 'alpha1-module.ko').read_bytes(), OLD)
        self.assertEqual((stage / 'alpha1-runtime.sha256').read_bytes(), sums(STOCK['sha256']))
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'current')

    def test_previous_fix_moves_to_the_new_one(self):
        # An Air on omarchy-m4-gpu 1-1 or 1-2 (#57) runs the earlier fix with its own pin.
        self.install(PRIOR, sums(PREVIOUS['sha256']))
        before = self.state()
        code, report = self.run_tool()
        self.assertEqual((code, report['state'], report['status']),
                         (0, 'previous', 'newer fix staged; applies at the next boot'))
        self.assertEqual(self.state(), before)
        code, report = self.run_tool('--boot')
        self.assertEqual(code, 0)
        self.assertIn('applied', report['status'])
        self.assertEqual(self.state(), (NEW, sums(FIXED['sha256'])))
        self.assertEqual(self.sums.stat().st_mode & 0o777, 0o664)
        # Only the alpha.1 files are ever kept as the rollback target.
        self.assertFalse((self.root / MODULE.STATE / 'alpha1-module.ko').exists())
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'current')

    def test_interrupted_upgrade_is_completed(self):
        real = MODULE.write

        def fail_on_sums(path, data, mode):
            if path == self.sums:
                raise OSError('power cut')
            real(path, data, mode)
        self.install(PRIOR, sums(PREVIOUS['sha256']))
        with mock.patch.object(MODULE, 'write', side_effect=fail_on_sums):
            self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.state(), (NEW, sums(PREVIOUS['sha256'])))
        self.assertEqual(self.run_tool()[1]['state'], 'interrupted')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (NEW, sums(FIXED['sha256'])))

    def test_earlier_interrupted_swap_moves_to_the_new_fix(self):
        # 1-1 or 1-2 lost power between its writes, then 1-3 arrived before the next boot.
        self.install(PRIOR, sums(STOCK['sha256']))
        self.assertEqual(self.run_tool()[1]['state'], 'interrupted')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (NEW, sums(FIXED['sha256'])))

    def test_rollback_from_the_previous_fix_restores_alpha1(self):
        # 1-1 kept the alpha.1 files when it first applied #57's module.
        self.run_tool('--boot')
        self.install(PRIOR, sums(PREVIOUS['sha256']))
        self.assertEqual(self.run_tool('--rollback')[0], 0)
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (OLD, sums(STOCK['sha256'])))
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'held after a rollback; nothing applied')

    def test_test_build_install_is_current(self):
        # gpu/j713/m4-gpu-test-module leaves the same module and pin, plus its own backup.
        self.install(NEW, sums(FIXED['sha256']))
        self.put(MODULE.TEST_BACKUP + '/asahi_m4.ko', OLD)
        before = self.state()
        code, report = self.run_tool('--boot')
        self.assertEqual((code, report['status']), (0, 'current'))
        self.assertEqual(self.state(), before)

    def test_interrupted_swap_is_completed_before_the_module_loads(self):
        real = MODULE.write

        def fail_on_sums(path, data, mode):
            if path == self.sums:
                raise OSError('power cut')
            real(path, data, mode)
        with mock.patch.object(MODULE, 'write', side_effect=fail_on_sums):
            self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.run_tool()[1]['state'], 'interrupted')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (NEW, sums(FIXED['sha256'])))

    def test_loaded_module_is_never_swapped(self):
        (self.root / 'sys/module' / MODULE.NAME).mkdir(parents=True)
        before = self.state()
        self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.state(), before)

    def test_unknown_or_unsafe_states_change_nothing(self):
        cases = {
            'hand-patched module': lambda: self.put(MODULE.MODULE, b'b.hi patched module'),
            'pin ahead of the module': lambda: self.put(MODULE.SUMS, sums(FIXED['sha256'])),
            'pin ahead of the previous fix': lambda: self.install(PRIOR, sums(FIXED['sha256'])),
            'previous fix pinned, stock module': lambda: self.put(MODULE.SUMS, sums(PREVIOUS['sha256'])),
            'own build pinned': lambda: self.install(b'local build', sums(hashlib.sha256(b'local build').hexdigest())),
            'pin edited': lambda: self.put(MODULE.SUMS, sums('0' * 64)),
            'module pinned twice': lambda: self.put(MODULE.SUMS, sums(STOCK['sha256']) * 2),
            'module not pinned': lambda: self.put(MODULE.SUMS, MESA.encode()),
            'staged module differs': lambda: self.put(MODULE.STAGED, b'tampered'),
            'staged module missing': lambda: (self.root / MODULE.STAGED).unlink(),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.setUp()
                change()
                before = self.state()
                self.assertEqual(self.run_tool('--boot')[0], 1)
                self.assertEqual(self.state(), before)

    def test_rollback_restores_alpha1_and_holds(self):
        self.run_tool('--boot')
        code, report = self.run_tool('--rollback')
        self.assertIn('restart', report['status'])
        self.assertEqual(self.state()[0], NEW, 'rollback waits for the next boot')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (OLD, sums(STOCK['sha256'])))
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'held after a rollback; nothing applied')
        self.assertEqual(self.state()[0], OLD)

    def test_rollback_of_a_test_build_uses_its_backup(self):
        self.install(NEW, sums(FIXED['sha256']))
        self.put(MODULE.TEST_BACKUP + '/asahi_m4.ko', OLD)
        self.put(MODULE.TEST_BACKUP + '/runtime.sha256', sums(STOCK['sha256']))
        self.run_tool('--rollback')
        self.assertEqual(self.run_tool('--boot')[0], 0)
        self.assertEqual(self.state(), (OLD, sums(STOCK['sha256'])))
        self.assertEqual(self.run_tool('--boot')[1]['status'], 'held after a rollback; nothing applied')

    def test_rollback_without_a_good_backup_refuses(self):
        self.install(NEW, sums(FIXED['sha256']))
        self.run_tool('--rollback')
        self.assertEqual(self.run_tool('--boot')[0], 1)
        self.put(MODULE.TEST_BACKUP + '/asahi_m4.ko', b'not alpha.1')
        self.put(MODULE.TEST_BACKUP + '/runtime.sha256', sums(STOCK['sha256']))
        self.assertEqual(self.run_tool('--boot')[0], 1)
        self.assertEqual(self.state(), (NEW, sums(FIXED['sha256'])))

    def test_pins_are_the_shipped_and_tested_modules(self):
        reloaded = importlib.util.module_from_spec(SPEC)  # unpatched pins
        SPEC.loader.exec_module(reloaded)
        helper = (ROOT / 'gpu/j713/m4-gpu-test-module').read_text()
        self.assertIn('stock=' + reloaded.STOCK['sha256'], helper)
        readme = (ROOT / 'gpu/j713/README.md').read_text()
        self.assertIn(reloaded.FIXED['sha256'], readme)
        # 3cf47fa7 is #57's module, shipped in 1-1 and 1-2; Airs on it must upgrade.
        self.assertEqual([old['sha256'][:8] for old in reloaded.PREVIOUS], ['3cf47fa7'])
        for old in reloaded.PREVIOUS:
            self.assertIn(old['sha256'], readme)
            self.assertNotIn(reloaded.FIXED['sha256'], (old['sha256'], reloaded.STOCK['sha256']))
        channel = json.loads((ROOT / 'packaging/omarchy-m4/channel.json').read_text())
        self.assertEqual(channel['gpu_module'], {reloaded.STAGED: reloaded.FIXED['sha256']})


if __name__ == '__main__':
    unittest.main()
