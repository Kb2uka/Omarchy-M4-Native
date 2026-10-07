#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Check the isolated J713 ANS send/receive IRQ correction in a compiled DTB.

Usage: python3 tests/check_ans_receive_irq.py original.dtb candidate.dtb
Checks captured hardware IRQ identities, not general ADT list ordering.
"""
import importlib.util
import hashlib
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("adt_audit", TOOLS / "adt-audit.py")
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)


def read_tree(path):
    root = audit.parse_dtb(Path(path).read_bytes())
    if "apple,j713" not in audit.node_compatibles(root):
        raise ValueError("expected J713 DTB")
    return root, {n.path: n for n in audit.fdt_walk(root)}


def check(original_path, candidate_path):
    original, before = read_tree(original_path)
    candidate, after = read_tree(candidate_path)
    path = "/soc/mbox@481608000"
    old_cells = audit.be32s(before[path].props["interrupts"])
    if old_cells != [0, 1004, 4, 0, 1003, 4, 0, 1006, 4, 0, 1005, 4]:
        raise ValueError("expected original ANS IRQ baseline")
    node = after[path]
    if "apple,t8132-asc-mailbox" not in audit.node_compatibles(node):
        raise ValueError("wrong ANS mailbox compatible")
    names = audit.cstrings(node.props["interrupt-names"])
    if names != ["send-empty", "send-not-empty", "recv-empty", "recv-not-empty"]:
        raise ValueError("unexpected mailbox IRQ names")
    parent_cells = audit.be32s(node.props["interrupt-parent"])
    if len(parent_cells) != 1:
        raise ValueError("invalid interrupt parent")
    parent = audit.fdt_phandles(candidate)[parent_cells[0]]
    if ("apple,t8132-aic3" not in audit.node_compatibles(parent)
            or audit.be32s(parent.props["#interrupt-cells"]) != [3]):
        raise ValueError("expected T8132 AIC with three-cell interrupts")
    cells = audit.be32s(node.props["interrupts"])
    if len(cells) != 12 or cells[6:] != [0, 1005, 4, 0, 1006, 4]:
        raise ValueError("expected recv-empty IRQ1005 and recv-not-empty IRQ1006, level high")
    if cells[:6] != [0, 1003, 4, 0, 1004, 4]:
        raise ValueError("expected send-empty IRQ1003 and send-not-empty IRQ1004, level high")
    if before.keys() != after.keys():
        raise ValueError("candidate changed node inventory")
    for name in before:
        b, a = dict(before[name].props), dict(after[name].props)
        if name == path:
            b.pop("interrupts")
            a.pop("interrupts")
        if b != a:
            raise ValueError("unrelated DT property changed: " + name)
    for label, filename in (("original", original_path), ("candidate", candidate_path)):
        print(label + "_sha256=" + hashlib.sha256(Path(filename).read_bytes()).hexdigest())
    print("PASS: ANS send IRQs1003/1004, receive IRQs1005/1006; all other DT properties unchanged")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_ans_receive_irq.py <original.dtb> <candidate.dtb>")
    check(*sys.argv[1:])
