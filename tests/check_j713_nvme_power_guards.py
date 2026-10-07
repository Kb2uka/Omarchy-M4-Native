#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Exercise the compiled-DT power-domain verifier with changed fixtures."""
import contextlib
import copy
import io
import struct
import sys
from unittest.mock import patch

import check_j713_nvme_power as checker


def run(original_path, candidate_path):
    original, before = checker.read_tree(original_path)
    candidate, after = checker.read_tree(candidate_path)
    cells = checker.audit.be32s(after[checker.NVME].props["power-domains"])
    cases = [
        ("unchanged attachment", checker.NVME, "power-domains", before[checker.NVME].props["power-domains"], "expected corrected"),
        ("reversed domain order", checker.NVME, "power-domains", struct.pack(">2I", *reversed(cells)), "expected corrected"),
        ("extra consumer domain", checker.NVME, "power-domains", struct.pack(">3I", *cells, cells[0]), "expected corrected"),
        ("invalid provider", checker.NVME, "power-domains", struct.pack(">2I", cells[0], 0xffffffff), "expected valid"),
        ("wrong provider cell count", checker.SYS_ST, "#power-domain-cells", struct.pack(">I", 1), "expected valid"),
        ("missing parent domain", checker.SYS_ST, "power-domains", struct.pack(">I", cells[0]), "unexpected storage power ancestry"),
        ("wrong provider register", checker.SYS_ST, "reg", struct.pack(">2I", 0x540, 4), "unexpected storage power provider"),
        ("wrong provider label", checker.SYS_ST, "label", b"apcie_st\0", "unexpected storage power provider"),
        ("wrong provider compatible", checker.SYS_ST, "compatible", b"other,domain\0", "unexpected storage power provider"),
        ("wrong NVMe compatible", checker.NVME, "compatible", b"other,nvme\0", "expected T8132"),
        ("NVMe status change", checker.NVME, "status", b"disabled\0", "unrelated DT property"),
        ("wrong names", checker.NVME, "power-domain-names", b"ans\0wrong\0", "unexpected NVMe"),
        ("wrong board", candidate.path, "compatible", b"apple,j715\0", "expected J713"),
        ("unrelated model change", candidate.path, "model", b"changed\0", "unrelated DT property"),
    ]
    checker.check(original_path, candidate_path)

    def reject(label, first, second, expected):
        with patch.object(checker.audit, "parse_dtb", side_effect=[first, second]):
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    checker.check(original_path, candidate_path)
                except ValueError as error:
                    if not str(error).startswith(expected):
                        raise RuntimeError("wrong rejection: " + label) from error
                else:
                    raise RuntimeError("guard did not reject: " + label)
        print("PASS rejected " + label)

    for label, node_path, key, value, expected in cases:
        modified = copy.deepcopy(candidate)
        node = next(n for n in checker.audit.fdt_walk(modified) if n.path == node_path)
        node.props[key] = value
        reject(label, original, modified, expected)
    for label, path, key, value, expected in (
        ("both ST ancestry changed", checker.ST, "power-domains", b"", "unexpected storage power ancestry"),
        ("both ANS identity changed", checker.ANS, "label", b"wrong\0", "unexpected storage power provider"),
        ("both ST identity changed", checker.ST, "reg", struct.pack(">2I", 0x544, 4), "unexpected storage power provider"),
    ):
        first, second = copy.deepcopy(original), copy.deepcopy(candidate)
        for tree in (first, second):
            node = next(n for n in checker.audit.fdt_walk(tree) if n.path == path)
            node.props[key] = value
        reject(label, first, second, expected)
    reject("already corrected baseline", candidate, candidate, "expected original")
    for label in ("added node", "removed node"):
        modified = copy.deepcopy(candidate)
        if label == "added node":
            modified.children.append(checker.audit.FdtNode("unexpected", modified))
        else:
            node = next(n for n in checker.audit.fdt_walk(modified)
                        if n.parent is not None and not n.children and n.path not in
                        (checker.NVME, checker.ANS, checker.ST, checker.SYS_ST))
            node.parent.children.remove(node)
        reject(label, original, modified, "candidate changed node inventory")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_j713_nvme_power_guards.py original.dtb candidate.dtb")
    run(*sys.argv[1:])
