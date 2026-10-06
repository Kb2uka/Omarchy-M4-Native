#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Check the compiled J713 MTP reset GPIOs against a captured ADT.

Usage: python3 tests/check_mtp_reset.py air-adt.bin candidate.dtb
Uses the existing ADT and FDT readers; never connects to or writes hardware.
"""

import importlib.util
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import adt

spec = importlib.util.spec_from_file_location("adt_audit", TOOLS / "adt-audit.py")
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def check(adt_path, dtb_path):
    raw = Path(adt_path).read_bytes()
    root, end = adt.parse(raw)
    if not (end <= len(raw) and not any(raw[end:])):
        raise ValueError("nonzero trailing ADT bytes")
    dt = audit.parse_dtb(Path(dtb_path).read_bytes())
    if not ("apple,j713" in audit.node_compatibles(dt)):
        raise ValueError("expected J713 DTB")
    handles = audit.fdt_phandles(dt)
    adt_handles = adt.phandles(root)
    hid_nodes = [n for n in audit.fdt_walk(dt)
                 if "apple,dockchannel-hid" in audit.node_compatibles(n)]
    if not (len(hid_nodes) == 1):
        raise ValueError("expected one DockChannel HID consumer")
    hid = hid_nodes[0]
    failures = []
    for child, function in (("multi-touch", "afe-reset"), ("stm", "stm-reset")):
        path = "/arm-io/dockchannel-mtp/mtp-transport/" + child
        node = adt.find_path(root, path)
        if not (node is not None):
            raise ValueError("missing ADT node: " + path)
        words = adt.u32s(node["props"]["function-" + function])
        if not (len(words) == 4):
            raise ValueError("unexpected reset function shape")
        provider_path, provider = adt_handles[words[0]]
        if not (provider_path == "/arm-io/smc/iop-smc-nub/smc-pmu"):
            raise ValueError('invalid ADT reset provider path')
        if not ("smc-pmu" in adt.strings(provider["props"]["compatible"])):
            raise ValueError('invalid ADT reset provider compatible')
        if not (words[1].to_bytes(4, "big") == b"pKW4"):
            raise ValueError("unexpected reset function")
        key = words[2].to_bytes(4, "big").decode("ascii")
        if not (key.startswith("gP")):
            raise ValueError("expected upper-bank SMC GPIO key")
        if not (all(c in "0123456789abcdef" for c in key[2:])):
            raise ValueError("invalid GPIO key")
        expected_offset = int(key[2:], 16)
        prop = "apple," + function + "-gpios"
        cells = audit.be32s(hid.props[prop])
        if not (len(cells) == 3):
            raise ValueError("expected phandle, offset and flags")
        gpio = handles[cells[0]]
        if not ("apple,smc-gpio" in audit.node_compatibles(gpio)):
            raise ValueError("wrong GPIO bank/provider")
        if not (gpio.parent is not None and "apple,t8132-smc" in audit.node_compatibles(gpio.parent)):
            raise ValueError('invalid DT reset provider parent')
        if not (audit.be32s(gpio.props["#gpio-cells"]) == [2]):
            raise ValueError('invalid DT reset provider cells')
        # Pin identity follows the ADT key. Polarity remains the existing DT contract.
        if not (cells[2] == 1):
            raise ValueError("existing active-low polarity changed")
        ok = cells[1] == expected_offset
        print(f"{'PASS' if ok else 'FAIL'} {function}: ADT {key} -> GPIO {expected_offset}; DT GPIO {cells[1]}")
        if not ok:
            failures.append(function)
    if not (not failures):
        raise ValueError("ADT/DT reset pin mismatch: " + ", ".join(failures))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_mtp_reset.py <air-adt.bin> <candidate.dtb>")
    check(*sys.argv[1:])
