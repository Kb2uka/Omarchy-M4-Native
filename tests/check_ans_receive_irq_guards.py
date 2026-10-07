#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Exercise receive-IRQ checker guards against copied compiled DT fixtures.

Usage: python3 tests/check_ans_receive_irq_guards.py original.dtb candidate.dtb
"""
import contextlib
import copy
import io
import struct
import sys
from unittest.mock import patch

import check_ans_receive_irq as checker


def run(original_path, candidate_path):
    original, _ = checker.read_tree(original_path)
    candidate, nodes = checker.read_tree(candidate_path)
    path = "/soc/mbox@481608000"
    mailbox = nodes[path]
    cells = checker.audit.be32s(mailbox.props["interrupts"])
    parent = checker.audit.fdt_phandles(candidate)[
        checker.audit.be32s(mailbox.props["interrupt-parent"])[0]]

    def irqs(index, value):
        values = cells.copy()
        values[index] = value
        return struct.pack(">12I", *values)

    cases = [
        ("reversed receive role", path, "interrupts", struct.pack(">12I", *(cells[:6] + [0, 1006, 4, 0, 1005, 4])), "expected recv-empty"),
        ("wrong trigger", path, "interrupts", irqs(8, 1), "expected recv-empty"),
        ("reversed send role", path, "interrupts", struct.pack(">12I", *([0, 1004, 4, 0, 1003, 4] + cells[6:])), "expected send-empty"),
        ("wrong send role", path, "interrupts", irqs(1, 1004), "expected send-empty"),
        ("wrong send-not-empty", path, "interrupts", irqs(4, 1003), "expected send-empty"),
        ("wrong recv-not-empty", path, "interrupts", irqs(10, 1005), "expected recv-empty"),
        ("wrong mailbox", path, "compatible", b"unrelated,mailbox\0", "wrong ANS"),
        ("wrong names", path, "interrupt-names", b"recv-not-empty\0", "unexpected mailbox"),
        ("wrong AIC", parent.path, "compatible", b"unrelated,aic\0", "expected T8132 AIC"),
        ("wrong cell count", parent.path, "#interrupt-cells", struct.pack(">I", 2), "expected T8132 AIC"),
        ("wrong parent shape", path, "interrupt-parent", b"", "invalid interrupt parent"),
        ("wrong board", candidate.path, "compatible", b"apple,other\0", "expected J713"),
        ("unrelated property", candidate.path, "model", b"changed\0", "unrelated DT property"),
    ]
    checker.check(original_path, candidate_path)
    for label, node_path, key, value, expected in cases:
        mutated = copy.deepcopy(candidate)
        node = next(n for n in checker.audit.fdt_walk(mutated) if n.path == node_path)
        node.props[key] = value
        with patch.object(checker.audit, "parse_dtb", side_effect=[original, mutated]):
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    checker.check(original_path, candidate_path)
                except ValueError as error:
                    if not str(error).startswith(expected):
                        raise RuntimeError("wrong rejection for " + label) from error
                else:
                    raise RuntimeError("guard did not reject " + label)
        print("PASS: rejected " + label)

    for label in ("added node", "removed node", "already corrected baseline",
                  "send-corrected baseline", "receive-corrected baseline"):
        mutated = copy.deepcopy(candidate)
        source = original
        expected = "candidate changed node inventory"
        if label == "added node":
            mutated.children.append(checker.audit.FdtNode("unexpected", mutated))
        elif label == "removed node":
            removable = next(n for n in checker.audit.fdt_walk(mutated)
                             if n.parent is not None and not n.children
                             and n.path not in (path, parent.path))
            removable.parent.children.remove(removable)
        elif label == "already corrected baseline":
            source = copy.deepcopy(candidate)
            expected = "expected original ANS IRQ baseline"
        else:
            source = copy.deepcopy(original)
            node = next(n for n in checker.audit.fdt_walk(source) if n.path == path)
            values = checker.audit.be32s(node.props["interrupts"])
            if label == "send-corrected baseline":
                values[:6] = cells[:6]
            else:
                values[6:] = cells[6:]
            node.props["interrupts"] = struct.pack(">12I", *values)
            expected = "expected original ANS IRQ baseline"
        with patch.object(checker.audit, "parse_dtb", side_effect=[source, mutated]):
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    checker.check(original_path, candidate_path)
                except ValueError as error:
                    if not str(error).startswith(expected):
                        raise RuntimeError("wrong rejection for " + label) from error
                else:
                    raise RuntimeError("guard did not reject " + label)
        print("PASS: rejected " + label)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_ans_receive_irq_guards.py <original.dtb> <candidate.dtb>")
    run(*sys.argv[1:])
