# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""tools/ioreg-adt.py: a macOS IODeviceTree text dump rebuilt as a binary ADT.

The fixture is a trimmed copy of the shapes the J773s capture contains: data
and string properties, IOKit runtime values, a zeroed memory-map node, an IOKit
driver object and the /options node. The rebuilt tree must parse with
tools/adt.py.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import adt  # noqa: E402

spec = importlib.util.spec_from_file_location('ioreg_adt', ROOT / 'tools/ioreg-adt.py')
IOREG_ADT = importlib.util.module_from_spec(spec)
spec.loader.exec_module(IOREG_ADT)

DUMP = '''\
+-o Root  <class IORegistryEntry, id 0x100000100, retain 35>
  | {
  |   "IOKitBuildVersion" = "Darwin Kernel Version 25.5.0"
  | }
  |
  +-o device-tree  <class IOPlatformExpertDevice, id 0x10000029a, registered>
    | {
    |   "#address-cells" = <02000000>
    |   "compatible" = <"J773sAP","Mac16,11","AppleARM">
    |   "name" = <"device-tree">
    |   "IOBusyInterest" = "IOCommand is not serializable"
    | }
    |
    +-o chosen  <class IOService, id 0x100000101, !registered>
    | | {
    | |   "board-id" = <02000000>
    | |   "name" = <"chosen">
    | | }
    | |
    | +-o memory-map  <class IOService, id 0x100000107, !registered>
    |     {
    |       "TrustCache" = <>
    |       "name" = <>
    |     }
    |
    +-o options  <class IODTNVRAM, id 0x10000029b, registered>
    |   {
    |     "boot-args" = <"">
    |   }
    |
    +-o arm-io@10F00000  <class IOPlatformDevice, id 0x1000002d5, registered>
      | {
      |   "name" = <"arm-io">
      |   "reg" = <0000f01002000000>
      | }
      |
      +-o aic@2400000  <class AppleARMIODevice, id 0x1000002e0, registered>
      | | {
      | |   "logical-cpu-id" = 4
      | |   "name" = <"aic">
      | |   "reg" = <000040020300000000c01c0000000000>
      | | }
      | |
      | +-o AppleInterruptController  <class AppleInterruptController, id 0x100000300>
      |     {
      |       "InterruptControllerName" = "IOInterruptController000000CA"
      |     }
      |
      +-o uart0@29200000  <class AppleARMIODevice, id 0x1000002e1, registered>
          {
            "compatible" = <"uart-1,samsung">
            "name" = <"uart0">
          }
'''


def rebuild(text):
    root, end = adt.parse(IOREG_ADT.encode(IOREG_ADT.device_tree(IOREG_ADT.parse_text(text.splitlines()))))
    return root, end, dict(adt.walk(root))


class IoregAdt(unittest.TestCase):
    def test_tree_parses_with_the_adt_reader(self):
        data = IOREG_ADT.encode(IOREG_ADT.device_tree(IOREG_ADT.parse_text(DUMP.splitlines())))
        root, end = adt.parse(data)
        self.assertEqual(end, len(data), 'trailing bytes after the root node')
        self.assertEqual(sorted(path for path, _ in adt.walk(root)),
                         ['/', '/arm-io', '/arm-io/aic', '/arm-io/uart0', '/chosen', '/chosen/memory-map'])

    def test_data_and_string_properties_keep_their_bytes(self):
        _, _, nodes = rebuild(DUMP)
        self.assertEqual(nodes['/']['props']['compatible'], b'J773sAP\0Mac16,11\0AppleARM\0')
        self.assertEqual(nodes['/']['props']['#address-cells'], b'\x02\0\0\0')
        self.assertEqual(nodes['/chosen']['props']['board-id'], b'\x02\0\0\0')
        self.assertEqual(nodes['/arm-io/aic']['props']['reg'],
                         bytes.fromhex('000040020300000000c01c0000000000'))

    def test_iokit_values_and_driver_objects_are_dropped(self):
        _, _, nodes = rebuild(DUMP)
        self.assertNotIn('IOBusyInterest', nodes['/']['props'])
        self.assertNotIn('logical-cpu-id', nodes['/arm-io/aic']['props'])
        self.assertEqual(nodes['/arm-io/aic']['children'], [])
        self.assertNotIn('/options', nodes)

    def test_a_zeroed_name_takes_the_ioreg_node_name(self):
        _, _, nodes = rebuild(DUMP)
        node = nodes['/chosen/memory-map']
        self.assertEqual(node['props']['name'], b'memory-map\0')
        self.assertEqual(node['props']['TrustCache'], b'')

    def test_names_too_long_for_the_adt_field_are_dropped(self):
        # An ADT property name is a 32-byte field ending in NUL, so 31 bytes fit.
        # 'c' * 30 + U+FFFD (the character errors='replace' inserts) is 31 characters but 33 bytes.
        fits, too_long = 'a' * 31, ('b' * 32, 'bluetooth.function-bootstrap_lock', 'c' * 30 + '\ufffd')
        lines = ''.join(f'      | |   "{key}" = <01000000>\n' for key in (fits,) + too_long)
        dump = DUMP.replace('|   "name" = <"aic">\n', '|   "name" = <"aic">\n' + lines)
        root, end, nodes = rebuild(dump)
        self.assertEqual(nodes['/arm-io/aic']['props'][fits], b'\x01\0\0\0')
        for key in too_long:
            self.assertNotIn(key, nodes['/arm-io/aic']['props'])
        self.assertEqual(sorted(nodes), ['/', '/arm-io', '/arm-io/aic', '/arm-io/uart0', '/chosen', '/chosen/memory-map'])

    def test_only_a_whole_data_value_is_data(self):
        self.assertEqual(IOREG_ADT.value('<0a0b>'), b'\x0a\x0b')
        self.assertEqual(IOREG_ADT.value('<"a","b">'), b'a\0b\0')
        for text in ('<0a0b>,<0c>', '(<00>,<01>)', '<0a0b> trailing', '"IOCommand is not serializable"', '4'):
            with self.subTest(text=text):
                self.assertIsNone(IOREG_ADT.value(text))

    def test_a_dump_without_a_device_tree_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'IODeviceTree'):
            IOREG_ADT.device_tree(IOREG_ADT.parse_text(['+-o Root  <class IORegistryEntry>']))

    def test_the_command_writes_the_rebuilt_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, out = Path(tmp, 'dump.txt'), Path(tmp, 'out.adt')
            source.write_text(DUMP)
            self.assertEqual(IOREG_ADT.main(['ioreg-adt.py', str(source), str(out)]), 0)
            root, end = adt.parse(out.read_bytes())
            self.assertEqual(end, out.stat().st_size)
            self.assertEqual(adt.name(root), 'device-tree')


if __name__ == '__main__':
    unittest.main()
