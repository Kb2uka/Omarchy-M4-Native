#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Exercise reset-checker guards with captured ADT and compiled DT fixtures.

Usage: python3 tests/check_mtp_reset_guards.py air-adt.bin candidate.dtb
Mutations stay in copied parser objects; no firmware or hardware is touched.
"""
import contextlib
import copy
import io
from pathlib import Path
import struct
import sys
from unittest.mock import patch

import check_mtp_reset as checker


def run(adt_path, dtb_path):
    raw = Path(adt_path).read_bytes()
    root, end = checker.adt.parse(raw)
    dt = checker.audit.parse_dtb(Path(dtb_path).read_bytes())
    nodes = list(checker.audit.fdt_walk(dt))
    hid = next(n for n in nodes if "apple,dockchannel-hid" in checker.audit.node_compatibles(n))
    handles = checker.audit.fdt_phandles(dt)
    prop = "apple,afe-reset-gpios"
    cells = checker.audit.be32s(hid.props[prop])
    gpio = handles[cells[0]]
    cases = [
        ("polarity", hid.path, prop, struct.pack(">3I", cells[0], cells[1], 0), "existing active-low polarity changed"),
        ("pin", hid.path, prop, struct.pack(">3I", cells[0], 8, cells[2]), "ADT/DT reset pin mismatch: afe-reset"),
        ("provider", gpio.path, "compatible", b"unrelated,gpio\0", "wrong GPIO bank/provider"),
        ("provider-parent", gpio.parent.path, "compatible", b"unrelated,smc\0", "invalid DT reset provider parent"),
        ("gpio-cells", gpio.path, "#gpio-cells", struct.pack(">I", 1), "invalid DT reset provider cells"),
        ("gpio-shape", hid.path, prop, struct.pack(">2I", cells[0], cells[1]), "expected phandle, offset and flags"),
        ("target", dt.path, "compatible", b"apple,another-model\0", "expected J713 DTB"),
        ("missing-hid", hid.path, "compatible", b"unrelated,hid\0", "expected one DockChannel HID consumer"),
    ]
    stm = checker.audit.be32s(hid.props["apple,stm-reset-gpios"])
    cases.extend([
        ("afe-higher-pin", hid.path, prop, struct.pack(">3I", cells[0], 29, cells[2]), "ADT/DT reset pin mismatch: afe-reset"),
        ("stm-higher-pin", hid.path, "apple,stm-reset-gpios", struct.pack(">3I", stm[0], 30, stm[2]), "ADT/DT reset pin mismatch: stm-reset"),
        ("stm-pin", hid.path, "apple,stm-reset-gpios", struct.pack(">3I", stm[0], 24, stm[2]), "ADT/DT reset pin mismatch: stm-reset"),
        ("stm-polarity", hid.path, "apple,stm-reset-gpios", struct.pack(">3I", stm[0], stm[1], 0), "existing active-low polarity changed"),
    ])
    with contextlib.redirect_stdout(io.StringIO()):
        checker.check(adt_path, dtb_path)
    print("PASS: unmodified captured fixtures")
    for label, node_path, key, value, expected in cases:
        mutated = copy.deepcopy(dt)
        node = next(n for n in checker.audit.fdt_walk(mutated) if n.path == node_path)
        node.props[key] = value
        with patch.object(checker.audit, "parse_dtb", return_value=mutated):
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    checker.check(adt_path, dtb_path)
                except ValueError as error:
                    if not str(error).startswith(expected):
                        raise RuntimeError("wrong rejection for " + label) from error
                else:
                    raise RuntimeError("guard did not reject " + label)
        print("PASS: rejected " + label)
    child_path = "/arm-io/dockchannel-mtp/mtp-transport/multi-touch"
    provider_path = "/arm-io/smc/iop-smc-nub/smc-pmu"
    function = checker.adt.find_path(root, child_path)["props"]["function-afe-reset"]
    words = checker.adt.u32s(function)
    def word_value(index, value):
        changed = list(words)
        changed[index] = int.from_bytes(value, "big")
        return struct.pack("<4I", *changed)
    adt_cases = [
        ("missing-child", child_path, "name", b"renamed\0", "missing ADT node"),
        ("function-shape", child_path, "function-afe-reset", function[:8], "unexpected reset function shape"),
        ("provider-path", provider_path, "name", b"renamed\0", "invalid ADT reset provider path"),
        ("provider-compatible", provider_path, "compatible", b"unrelated,pmu\0", "invalid ADT reset provider compatible"),
        ("function-code", child_path, "function-afe-reset", word_value(1, b"nope"), "unexpected reset function"),
        ("lower-bank", child_path, "function-afe-reset", word_value(2, b"gp1c"), "expected upper-bank SMC GPIO key"),
        ("invalid-key", child_path, "function-afe-reset", word_value(2, b"gPzz"), "invalid GPIO key"),
    ]
    for label, node_path, key, value, expected in adt_cases:
        mutated = copy.deepcopy(root)
        checker.adt.find_path(mutated, node_path)["props"][key] = value
        with patch.object(checker.adt, "parse", return_value=(mutated, end)):
            try:
                checker.check(adt_path, dtb_path)
            except ValueError as error:
                if not str(error).startswith(expected):
                    raise RuntimeError("wrong rejection for " + label) from error
            else:
                raise RuntimeError("guard did not reject " + label)
        print("PASS: rejected " + label)
    with patch.object(checker.adt, "parse", return_value=(root, end - 1)):
        # Use a nonzero trailing byte, independent of original padding content.
        with patch.object(Path, "read_bytes", return_value=raw[:end - 1] + b"\x01"):
            try:
                checker.check(adt_path, dtb_path)
            except ValueError as error:
                if str(error) != "nonzero trailing ADT bytes":
                    raise
            else:
                raise RuntimeError("guard did not reject trailing ADT bytes")
    print("PASS: rejected nonzero trailing ADT bytes")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_mtp_reset_guards.py <air-adt.bin> <candidate.dtb>")
    run(*sys.argv[1:])
