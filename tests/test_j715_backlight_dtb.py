# SPDX-License-Identifier: MIT
"""Build actual binary DT resources in scratch; fixtures stay synthetic."""
import dataclasses
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from test_j715_display_profile import FIXTURE, PROFILE, ROOT, environment, load

BUILDER = load("j715_backlight_dt", "tools/build-j715-backlight-dt.py")
FIXTURES = load("j715_fdt_fixture", "tests/test_j713_poweroff_dtb.py")
U32 = FIXTURES.U32


def base_nodes():
    return {
        (): {"compatible": b"apple,j715\0apple,t8132\0apple,arm-platform\0",
             "model": b"Apple MacBook Air (15-inch, M4, 2025)\0"},
        ("aliases",): {"serial0": b"/soc/serial@400000\0"},
        ("soc",): {"#address-cells": U32(2), "#size-cells": U32(2), "ranges": b""},
        ("soc", "interrupt-controller@300000"): {"compatible": b"apple,t8132-aic3\0",
                                                 "#interrupt-cells": U32(3), "phandle": U32(3)},
        ("soc", "power"): {},
        ("soc", "power", "test"): {"label": b"synthetic_display\0", "phandle": U32(7),
                                   "#power-domain-cells": U32(0)},
        ("soc", "serial@400000"): {"arbitrary-data": b"\0\xfe\xef\x01", "status": b"okay\0"}}


class DisplayDT(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.base, self.output = self.directory / "base.dtb", self.directory / "output.dtb"
        data = FIXTURES.dtb(base_nodes())
        header = list(struct.unpack(">10I", data[:40]))
        header[1] += 16
        header[2] += 16
        header[3] += 16
        header[7] = 9
        self.base.write_bytes(struct.pack(">10I", *header) + struct.pack(">QQ", 0x700000, 0x4000) + data[40:])
        self.profile = dataclasses.replace(PROFILE.parse(FIXTURE), base_dtb_sha256=PROFILE.sha(self.base.read_bytes()))

    def build(self):
        # Inject an explicitly synthetic typed profile only inside this test.
        # The actual CLI has no override and always calls qualified().
        with patch.object(BUILDER, "qualified", return_value=self.profile):
            return BUILDER.build(self.base, FIXTURE, self.output)

    def test_binary_builder_preserves_all_unrelated_data_and_adds_only_resources(self):
        original = BUILDER.tree(self.base.read_bytes())
        self.build()
        nodes, reservations, cpu = BUILDER.tree(self.output.read_bytes())
        self.assertEqual(reservations, original[1])
        self.assertEqual(cpu, 9)
        for path, props in original[0].items():
            with self.subTest(path=path):
                actual = dict(nodes[path])
                if path == "/aliases":
                    actual.pop("dcp")
                self.assertEqual(actual, props)
        dcp, dart, mailbox = self.profile.resources
        handles = {r.path: struct.unpack(">I", nodes[r.path]["phandle"])[0] for r in self.profile.resources}
        self.assertEqual(nodes[dcp.path]["iommus"], U32(handles[dart.path], self.profile.stream))
        self.assertEqual(nodes[dcp.path]["mboxes"], U32(handles[mailbox.path]))
        self.assertEqual(nodes[dcp.path]["reg"], struct.pack(">QQ", dcp.address, dcp.size))
        self.assertFalse(any("omarchy,display-handoff-verified" in p or "memory-region" in p or
                             "iommu-addresses" in p or "apple,dma-range" in p for p in nodes.values()))

    def test_identity_mismatch_refuses_before_serialization(self):
        self.base.write_bytes(self.base.read_bytes() + b"tamper")
        with patch.object(BUILDER, "encode") as run, self.assertRaises(ValueError):
            self.build()
        run.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_wrong_board_power_alias_and_replayed_marker_refuse(self):
        for target, field, value in [
            ((), "compatible", b"apple,j713\0apple,t8132\0"),
            (("soc", "power", "test"), "label", b"wrong\0"),
            (("aliases",), "dcp", b"/soc/old\0"),
            ((), "omarchy,display-handoff-verified", b"")]:
            with self.subTest(field=field):
                nodes = base_nodes()
                nodes[target][field] = value
                self.base.write_bytes(FIXTURES.dtb(nodes))
                self.profile = dataclasses.replace(self.profile, base_dtb_sha256=PROFILE.sha(self.base.read_bytes()))
                with self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved(self):
        self.output.write_bytes(b"owner output")
        with self.assertRaises(ValueError):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"owner output")

    def test_serializer_cannot_change_neighbor_reservation_or_boot_cpu(self):
        for offset in (28, 48):
            with self.subTest(offset=offset):
                bad = bytearray(self.base.read_bytes())
                bad[offset] ^= 1
                with patch.object(BUILDER, "encode", return_value=bytes(bad)), self.assertRaises(ValueError):
                    self.build()
                self.assertFalse(self.output.exists())

    def test_real_cli_refuses_synthetic_without_output(self):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/build-j715-backlight-dt.py"),
                                 "--base", str(self.base), "--profile", str(FIXTURE),
                                 "--output", str(self.output)], env=environment(),
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
