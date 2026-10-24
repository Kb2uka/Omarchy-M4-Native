#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Remote-only compiler check for the embedded DTBO alignment contract.

Run on the fleet test host only (optiplex or a Linux CI box), never on a
development machine. Writes a temporary C file, compiles it with the host
cc in C11, and runs that binary. No network. Does not run dtc and does not
touch j713_isp_dartmap_dtbo.inc.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import embed_dtbo


def minimal_fdt(total: int = 32) -> bytes:
    blob = bytearray(total)
    blob[0:4] = embed_dtbo.FDT_MAGIC
    blob[4:8] = total.to_bytes(4, "big")
    return bytes(blob)


def hex_bytes(src: str) -> bytes:
    vals = []
    for token in src.replace(",", " ").split():
        if token.startswith("0x"):
            vals.append(int(token, 16))
    return bytes(vals)


class EmbedAlignmentTest(unittest.TestCase):
    def test_emitted_bytes_and_compiler_alignment(self) -> None:
        blob = minimal_fdt(32)
        src = embed_dtbo.embed(blob)
        self.assertEqual(hex_bytes(src), blob)
        self.assertIn("static const unsigned int j713_dm_dtbo_len = 32;", src)
        cc = os.environ.get("CC", "cc")
        with tempfile.TemporaryDirectory(prefix="j713-embed-") as td:
            td_path = Path(td)
            c_file = td_path / "align.c"
            exe = td_path / "align"
            # A 4-byte predecessor matches the live .rodata+0x3d4 case when
            # the compiler keeps file order. _Alignas(8) must still hold.
            c_file.write_text(
                "static const unsigned char j713_dm_lead[4] = {\n"
                "\t0x11, 0x22, 0x33, 0x44\n"
                "};\n"
                + src
                + "\n"
                "_Static_assert(__alignof__(j713_dm_dtbo) >= 8,\n"
                '    "j713_dm_dtbo alignment");\n'
                "int main(void)\n"
                "{\n"
                "    if (((unsigned long)j713_dm_dtbo) % 8UL != 0UL)\n"
                "        return 19;\n"
                "    if (j713_dm_dtbo_len != 32u)\n"
                "        return 2;\n"
                "    if (j713_dm_dtbo[0] != 0xd0 || j713_dm_dtbo[1] != 0x0d ||\n"
                "        j713_dm_dtbo[2] != 0xfe || j713_dm_dtbo[3] != 0xed)\n"
                "        return 3;\n"
                "    if (j713_dm_lead[0] != 0x11)\n"
                "        return 4;\n"
                "    return 0;\n"
                "}\n"
            )
            cmd = [
                cc, "-std=c11", "-Wall", "-Wextra", "-Werror", "-O2",
                "-fno-toplevel-reorder", "-o", str(exe), str(c_file),
            ]
            compiled = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60, check=False,
            )
            if compiled.returncode != 0:
                cmd.remove("-fno-toplevel-reorder")
                compiled = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=60, check=False,
                )
            self.assertEqual(
                compiled.returncode, 0,
                compiled.stderr or compiled.stdout,
            )
            ran = subprocess.run(
                [str(exe)], capture_output=True, text=True, timeout=10,
                check=False,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr or ran.stdout)

    def test_refuses_non_fdt(self) -> None:
        with self.assertRaises(SystemExit):
            embed_dtbo.embed(b"\x00" * 16)
        with self.assertRaises(SystemExit):
            embed_dtbo.embed(embed_dtbo.FDT_MAGIC)


if __name__ == "__main__":
    unittest.main()
