# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Luthfi Hizb
"""The boot-time headphone module load: pinned inputs only, one strike after a boot dies."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_headphones', ROOT / 'tools/m4-headphones.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

OVERLAY, CARD = b'headphone overlay module', b'headphone card module'
PINS = tuple((name, filename, {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)})
             for (name, filename, _), data in zip(MODULE.MODULES, (OVERLAY, CARD)))


class Machine:
    """modprobe and insmod against a fake /sys/module; records what ran."""

    def __init__(self, root, fail=None):
        self.root, self.fail, self.commands, self.marked = root, fail, [], []

    def __call__(self, command):
        self.commands.append(command)
        self.marked.append((self.root / MODULE.STATE / 'attempt').is_file())
        if self.fail in (command[0], Path(command[-1]).name):
            return 1
        if command[0] == 'insmod':
            name = next(name for name, staged, _ in MODULE.MODULES if staged == Path(command[1]).name)
            (self.root / 'sys/module' / name).mkdir(parents=True)
        return 0


class HeadphoneLoad(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patcher = mock.patch.object(MODULE, 'MODULES', PINS)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.put(MODULE.COMPATIBLE, b'apple,j713\0apple,t8132\0apple,arm-platform\0')
        self.put(MODULE.OSRELEASE, (MODULE.RELEASE + '\n').encode())
        self.put(MODULE.STAGED + '/j713_hp_dt.ko', OVERLAY)
        self.put(MODULE.STAGED + '/snd-soc-j713-headphones.ko', CARD)
        (self.root / 'sys/module').mkdir(parents=True)

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def marker(self):
        return self.root / MODULE.STATE / 'attempt'

    def test_inspection_changes_nothing(self):
        machine = Machine(self.root)
        report = MODULE.run(self.root, 'inspect', machine)
        self.assertEqual(report['status'], 'loads at the next boot')
        self.assertEqual(machine.commands, [])
        self.assertFalse((self.root / MODULE.STATE).exists())

    def test_boot_loads_dependencies_then_overlay_then_card(self):
        machine = Machine(self.root)
        report = MODULE.run(self.root, 'boot', machine)
        self.assertEqual(machine.commands, [
            ['modprobe', '-a', 'snd-soc-core', 'snd-pcm', 'snd-pcm-dmaengine'],
            ['insmod', str(self.root / MODULE.STAGED / 'j713_hp_dt.ko')],
            ['insmod', str(self.root / MODULE.STAGED / 'snd-soc-j713-headphones.ko')]])
        self.assertEqual(report['state'], 'loaded')
        self.assertTrue(report['status'].startswith('loaded'))
        self.assertEqual(machine.marked, [True, True, True], 'marker must be durable before any load')
        self.assertFalse(self.marker().exists())

    def test_a_boot_that_died_while_loading_stops_the_next_boot(self):
        self.put(MODULE.STATE + '/attempt', b'loading\n')
        machine = Machine(self.root)
        self.assertEqual(MODULE.run(self.root, 'inspect', machine)['state'], 'struck')
        with self.assertRaisesRegex(MODULE.Refused, 'previous boot stopped'):
            MODULE.run(self.root, 'boot', machine)
        self.assertEqual(machine.commands, [])
        self.assertTrue(self.marker().exists())

    def test_start_retries_in_the_session_and_clears_the_marker(self):
        self.put(MODULE.STATE + '/attempt', b'loading\n')
        machine = Machine(self.root)
        self.assertEqual(MODULE.run(self.root, 'start', machine)['state'], 'loaded')
        self.assertEqual(len(machine.commands), 3)
        self.assertFalse(self.marker().exists())

    def test_a_failed_load_clears_the_marker_and_stops(self):
        machine = Machine(self.root, fail='j713_hp_dt.ko')
        with self.assertRaisesRegex(MODULE.Refused, 'Loading j713_hp_dt.ko failed'):
            MODULE.run(self.root, 'boot', machine)
        self.assertEqual(len(machine.commands), 2, 'the card must not load without the overlay')
        self.assertFalse(self.marker().exists(), 'a load that returned is not a strike')

    def test_failed_dependencies_load_nothing(self):
        machine = Machine(self.root, fail='modprobe')
        with self.assertRaisesRegex(MODULE.Refused, 'Loading the dependencies'):
            MODULE.run(self.root, 'boot', machine)
        self.assertEqual(len(machine.commands), 1)

    def test_only_pinned_modules_on_the_pinned_kernel_and_board(self):
        cases = ((MODULE.OSRELEASE, b'7.1.9-other\n', 'running kernel'),
                 (MODULE.COMPATIBLE, b'apple,j613\0apple,t8132\0', 'not a J713'),
                 (MODULE.STAGED + '/snd-soc-j713-headphones.ko', b'other card module', 'differs from the pin'))
        for name, data, message in cases:
            with self.subTest(message):
                original = (self.root / name).read_bytes()
                self.put(name, data)
                machine = Machine(self.root)
                with self.assertRaisesRegex(MODULE.Refused, message):
                    MODULE.run(self.root, 'boot', machine)
                self.assertEqual(machine.commands, [])
                self.assertFalse(self.marker().exists())
                self.put(name, original)

    def test_loaded_card_is_current(self):
        for name, _, _ in PINS:
            (self.root / 'sys/module' / name).mkdir()
        machine = Machine(self.root)
        self.assertEqual(MODULE.run(self.root, 'boot', machine)['status'], 'current')
        self.assertEqual(machine.commands, [])

    def test_a_loaded_overlay_is_not_applied_twice(self):
        (self.root / 'sys/module/j713_hp_dt').mkdir()
        machine = Machine(self.root)
        self.assertEqual(MODULE.run(self.root, 'inspect', machine)['state'], 'partial')
        MODULE.run(self.root, 'start', machine)
        self.assertEqual([c[1] for c in machine.commands if c[0] == 'insmod'],
                         [str(self.root / MODULE.STAGED / 'snd-soc-j713-headphones.ko')])


if __name__ == '__main__':
    unittest.main()
