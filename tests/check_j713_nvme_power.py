#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Verify the isolated J713 NVMe power-domain correction in compiled DTBs."""
import hashlib
import importlib.util
from pathlib import Path
import sys

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location("adt_audit", TOOLS / "adt-audit.py")
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)

NVME = "/soc/nvme@4c5cc0000"
PMGR = "/soc/power-management@380700000"
ANS = PMGR + "/power-controller@538"
ST = PMGR + "/power-controller@540"
SYS_ST = PMGR + "/power-controller@548"


def read_tree(path):
    root = audit.parse_dtb(Path(path).read_bytes())
    if "apple,j713" not in audit.node_compatibles(root):
        raise ValueError("expected J713 DTB")
    return root, {node.path: node for node in audit.fdt_walk(root)}


def domains(root, node):
    handles = audit.fdt_phandles(root)
    result = []
    for cell in audit.be32s(node.props["power-domains"]):
        provider = handles.get(cell)
        if provider is None or audit.be32s(provider.props.get("#power-domain-cells", b"")) != [0]:
            raise ValueError("expected valid zero-cell power provider")
        result.append(provider.path)
    return result


def check(original_path, candidate_path):
    original, before = read_tree(original_path)
    candidate, after = read_tree(candidate_path)
    if before.keys() != after.keys():
        raise ValueError("candidate changed node inventory")
    for root, nodes in ((original, before), (candidate, after)):
        if "apple,t8132-nvme-ans2" not in audit.node_compatibles(nodes[NVME]):
            raise ValueError("expected T8132 NVMe compatible")
        if audit.cstrings(nodes[NVME].props["power-domain-names"]) != ["ans", "apcie0"]:
            raise ValueError("unexpected NVMe power-domain names")
        for path, label, offset in ((ANS, "ans", 0x538), (ST, "apcie_st", 0x540),
                                    (SYS_ST, "apcie_sys_st", 0x548)):
            node = nodes[path]
            if (audit.cstrings(node.props["label"]) != [label]
                    or audit.be32s(node.props["reg"]) != [offset, 4]
                    or "apple,t8132-pmgr-pwrstate" not in audit.node_compatibles(node)):
                raise ValueError("unexpected storage power provider")
        if domains(root, nodes[ST]) != [ANS] or domains(root, nodes[SYS_ST]) != [ANS, ST]:
            raise ValueError("unexpected storage power ancestry")
    if domains(original, before[NVME]) != [ANS, ST]:
        raise ValueError("expected original ANS/APCIE_ST attachment")
    if domains(candidate, after[NVME]) != [ANS, SYS_ST]:
        raise ValueError("expected corrected ANS/APCIE_SYS_ST attachment")
    for path in before:
        old, new = dict(before[path].props), dict(after[path].props)
        if path == NVME:
            old.pop("power-domains")
            new.pop("power-domains")
        if old != new:
            raise ValueError("unrelated DT property changed: " + path)
    for label, path in (("original", original_path), ("candidate", candidate_path)):
        print(label + "_sha256=" + hashlib.sha256(Path(path).read_bytes()).hexdigest())
    print("PASS: J713 NVMe attaches ANS/APCIE_SYS_ST; all other DT properties unchanged")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: check_j713_nvme_power.py original.dtb candidate.dtb")
    check(*sys.argv[1:])
