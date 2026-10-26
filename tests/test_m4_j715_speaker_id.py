# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Kazuhide Takahashi
"""m4-j715-speaker-id reads pins 4 and 13 as-is and refuses anything but 0 or 3."""
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('m4_j715_speaker_id', ROOT / 'tools/m4-j715-speaker-id.py')
TOOL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TOOL)


class Kernel:
    """Answers the three GPIO ioctls for fake /dev/gpiochipN files."""

    REAL_OPEN = os.open

    def __init__(self, labels, bits, lines=224):
        self.labels, self.bits, self.lines = labels, bits, lines
        self.requests, self.line_fds, self.chips = [], set(), {}

    def open(self, path, flags, *args):
        fd = self.REAL_OPEN(path, flags, *args)
        self.chips[fd] = Path(path).name
        return fd

    def ioctl(self, fd, request, arg):
        if request == TOOL.GPIO_GET_CHIPINFO_IOCTL:
            name = self.chips[fd]
            arg.name, arg.label, arg.lines = name.encode(), self.labels[name].encode(), self.lines
        elif request == TOOL.GPIO_V2_GET_LINE_IOCTL:
            self.requests.append(([arg.offsets[i] for i in range(arg.num_lines)], arg.config.flags,
                                  arg.config.num_attrs, arg.consumer))
            arg.fd = self.REAL_OPEN(os.devnull, os.O_RDONLY)
            self.line_fds.add(arg.fd)
        elif request == TOOL.GPIO_V2_LINE_GET_VALUES_IOCTL:
            assert fd in self.line_fds and arg.mask == 0b11
            arg.bits = self.bits
        else:
            raise OSError(25, 'unexpected ioctl')
        return 0


class SpeakerId(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'dev').mkdir()
        compatible = self.root / TOOL.COMPATIBLE
        compatible.parent.mkdir(parents=True)
        compatible.write_bytes(b'apple,j715\0apple,t8132\0apple,arm-platform\0')
        self.labels = {'gpiochip0': '38c820000.pinctrl', 'gpiochip3': '3881f0000.pinctrl',
                       'gpiochip4': '39a000000.pinctrl'}
        for name in self.labels:
            (self.root / 'dev' / name).write_bytes(b'')

    def run_tool(self, kernel):
        output = io.StringIO()
        with mock.patch.object(TOOL.fcntl, 'ioctl', kernel.ioctl), mock.patch.object(TOOL.os, 'open', kernel.open), \
             mock.patch('sys.stdout', output), \
             mock.patch('sys.stderr', io.StringIO()):
            code = TOOL.main(['--root', str(self.root)])
        return code, json.loads(output.getvalue()) if code == 0 else None

    def test_uapi_layout(self):
        self.assertEqual((TOOL.ctypes.sizeof(TOOL.GpioChipInfo), TOOL.ctypes.sizeof(TOOL.GpioV2LineRequest),
                          TOOL.ctypes.sizeof(TOOL.GpioV2LineValues)), (68, 592, 16))
        self.assertEqual((TOOL.GPIO_GET_CHIPINFO_IOCTL, TOOL.GPIO_V2_GET_LINE_IOCTL,
                          TOOL.GPIO_V2_LINE_GET_VALUES_IOCTL), (0x8044B401, 0xC250B407, 0xC010B40E))

    def test_both_high_is_speaker_id_3(self):
        kernel = Kernel(self.labels, 0b11)
        code, report = self.run_tool(kernel)
        self.assertEqual((code, report['speaker_id'], report['pins'], report['chip']),
                         (0, 3, {'4': 1, '13': 1}, 'gpiochip4'))

    def test_both_low_is_speaker_id_0(self):
        code, report = self.run_tool(Kernel(self.labels, 0b00))
        self.assertEqual((code, report['speaker_id']), (0, 0))

    def test_lines_are_requested_as_is_and_released(self):
        kernel = Kernel(self.labels, 0b11)
        self.run_tool(kernel)
        self.assertEqual(kernel.requests, [([4, 13], 0, 0, b'm4-j715-speaker-id')])
        for fd in kernel.line_fds:
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_mixed_pins_are_refused(self):
        for bits in (0b01, 0b10):
            with self.subTest(bits=bits):
                self.assertEqual(self.run_tool(Kernel(self.labels, bits))[0], 1)

    def test_other_boards_are_refused_before_any_gpio(self):
        (self.root / TOOL.COMPATIBLE).write_bytes(b'apple,j713\0apple,t8132\0')
        kernel = Kernel(self.labels, 0b11)
        self.assertEqual(self.run_tool(kernel)[0], 1)
        self.assertEqual(kernel.requests, [])

    def test_missing_or_ambiguous_chip_is_refused(self):
        for labels in ({'gpiochip0': '38c820000.pinctrl'},
                       {'gpiochip4': '39a000000.pinctrl', 'gpiochip5': '39a000000.pinctrl'}):
            with self.subTest(labels=labels):
                for path in (self.root / 'dev').iterdir():
                    path.unlink()
                for name in labels:
                    (self.root / 'dev' / name).write_bytes(b'')
                kernel = Kernel(labels, 0b11)
                self.assertEqual(self.run_tool(kernel)[0], 1)
                self.assertEqual(kernel.requests, [])

    def test_too_few_lines_is_refused(self):
        kernel = Kernel(self.labels, 0b11, lines=13)
        self.assertEqual(self.run_tool(kernel)[0], 1)
        self.assertEqual(kernel.requests, [])


    def test_live_root_needs_root(self):
        stderr = io.StringIO()
        with mock.patch.object(TOOL.os, 'geteuid', return_value=1000), mock.patch('sys.stderr', stderr), \
             mock.patch.object(TOOL, 'run') as run:
            self.assertEqual(TOOL.main([]), 1)
        run.assert_not_called()
        self.assertIn('run with sudo', stderr.getvalue())

if __name__ == '__main__':
    unittest.main()
