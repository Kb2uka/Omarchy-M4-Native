"""Offline build guards; execution is deferred under the maintainer's test waiver."""
import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'tools/build-j713-poweroff-dtb.py'
SPEC = importlib.util.spec_from_file_location('poweroff_dtb', SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def dtb(with_child=False, other_changed=False):
    strings = b'compatible\0status\0'
    words = bytearray()
    def u32(*values):
        words.extend(struct.pack('>' + 'I' * len(values), *values))
    def name(value):
        words.extend(value + b'\0')
        words.extend(bytes((-len(words)) % 4))
    def prop(offset, value):
        u32(3, len(value), offset)
        words.extend(value)
        words.extend(bytes((-len(words)) % 4))
    u32(1); name(b''); prop(0, b'apple,j713\0apple,t8132\0')
    u32(1); name(b'soc')
    u32(1); name(b'smc@38c600000'); prop(0, b'apple,t8132-smc\0apple,t8103-smc\0')
    prop(11, b'disabled\0' if other_changed else b'okay\0')
    if with_child:
        u32(1); name(b'reboot'); prop(0, b'apple,smc-reboot\0'); u32(2)
    u32(2, 2, 2, 9)
    start = 56
    header = struct.pack('>10I', 0xd00dfeed, start + len(words) + len(strings), start,
                         start + len(words), 40, 17, 16, 0, len(strings), len(words))
    return header + bytes(16) + words + strings


class BuildGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.base = self.root / 'base.dtb'
        self.base.write_bytes(dtb())
        self.overlay = self.root / 'poweroff.dtso'
        self.overlay.write_bytes(b'source fixture')
        self.output = self.root / 'out.dtb'

    def build(self, candidate):
        def compile_only(command, check):
            destination = Path(command[command.index('-o') + 1])
            destination.write_bytes(candidate if command[0] == 'fdtoverlay' else b'overlay')
        with patch.object(MODULE, 'BASE_SHA256', MODULE.sha(self.base.read_bytes())), \
             patch.object(MODULE.subprocess, 'run', side_effect=compile_only):
            return MODULE.build(self.base, self.output, self.overlay)

    def test_only_child_added(self):
        receipt = self.build(dtb(with_child=True))
        self.assertEqual(self.output.read_bytes(), dtb(with_child=True))
        self.assertTrue(receipt['other_properties_reservations_and_boot_cpu_unchanged'])
        self.assertFalse(receipt['hardware_poweroff_verified'])

    def test_missing_child_refused(self):
        with self.assertRaisesRegex(ValueError, 'beyond the single'):
            self.build(dtb())
        self.assertFalse(self.output.exists())

    def test_other_property_change_refused(self):
        with self.assertRaisesRegex(ValueError, 'beyond the single'):
            self.build(dtb(with_child=True, other_changed=True))
        self.assertFalse(self.output.exists())

    def test_unqualified_base_before_compiler(self):
        with patch.object(MODULE.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'Unqualified'):
                MODULE.build(self.base, self.output, self.overlay)
        command.assert_not_called()

    def test_existing_output_preserved(self):
        self.output.write_bytes(b'custom')
        with self.assertRaisesRegex(ValueError, 'Output must not exist'):
            self.build(dtb(with_child=True))
        self.assertEqual(self.output.read_bytes(), b'custom')

    def test_base_symlink_refused(self):
        real = self.root / 'real.dtb'
        self.base.rename(real)
        self.base.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'regular file'):
            self.build(dtb(with_child=True))


if __name__ == '__main__':
    unittest.main()
