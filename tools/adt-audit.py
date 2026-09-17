#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Audit a compiled Apple Linux DTB against a live Apple Device Tree.

The parser is deliberately standalone: Python's standard library, tools/adt.py,
and a DTB produced by dtc are the only inputs.  ADT integer cells are little
endian; flattened-device-tree cells are big endian.
"""

from __future__ import annotations

import argparse
import dataclasses
import pathlib
import re
import struct
import sys
from collections import Counter

import adt


FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9

# The M4 ANS controller register block is ADT reg[9].  Firmware declares a
# 64 KiB secure BAR, while the proven controller sequence accesses through
# offset 0x24910.  Linux therefore maps 0x28000, and this one exact resource
# is allowed to exceed its ADT tuple.  This is intentionally not a general
# containment exception.
T8132_NVME_DT_PATH = "/soc/nvme@4c5cc0000"
T8132_NVME_DT_WINDOW = (0x4C5CC0000, 0x28000)
T8132_NVME_ADT_PATH = "/arm-io/ans"
T8132_NVME_ADT_REG_INDEX = 9
T8132_NVME_ADT_WINDOW = (0x4C5CC0000, 0x10000)
T8132_NVME_OVERSIZE_NOTE = "oversized by design: reference accesses to 0x24910"


@dataclasses.dataclass
class FdtNode:
    name: str
    parent: "FdtNode | None"
    props: dict[str, bytes] = dataclasses.field(default_factory=dict)
    children: list["FdtNode"] = dataclasses.field(default_factory=list)

    @property
    def path(self) -> str:
        if self.parent is None:
            return "/"
        prefix = self.parent.path.rstrip("/")
        return prefix + "/" + self.name


def align4(value: int) -> int:
    return (value + 3) & ~3


def be32s(value: bytes) -> list[int]:
    if len(value) % 4:
        raise ValueError("FDT property length is not a multiple of four")
    return list(struct.unpack(">%dI" % (len(value) // 4), value))


def be_int(cells: list[int]) -> int:
    value = 0
    for cell in cells:
        value = (value << 32) | cell
    return value


def cstrings(value: bytes) -> list[str]:
    return [part.decode("ascii", "replace") for part in value.rstrip(b"\0").split(b"\0") if part]


def parse_dtb(data: bytes) -> FdtNode:
    if len(data) < 40:
        raise ValueError("short DTB")
    header = struct.unpack_from(">10I", data, 0)
    magic, total, off_struct, off_strings, _, _, _, _, size_strings, size_struct = header
    if magic != 0xD00DFEED:
        raise ValueError("not a flattened device tree")
    if total > len(data):
        raise ValueError("truncated DTB")
    strings_block = data[off_strings:off_strings + size_strings]
    pos = off_struct
    end = off_struct + size_struct
    root = None
    current = None
    while pos < end:
        token, = struct.unpack_from(">I", data, pos)
        pos += 4
        if token == FDT_BEGIN_NODE:
            nul = data.index(0, pos, end)
            node = FdtNode(data[pos:nul].decode("ascii", "replace"), current)
            pos = align4(nul + 1)
            if current is not None:
                current.children.append(node)
            else:
                root = node
            current = node
        elif token == FDT_END_NODE:
            if current is None:
                raise ValueError("unbalanced FDT_END_NODE")
            current = current.parent
        elif token == FDT_PROP:
            length, nameoff = struct.unpack_from(">II", data, pos)
            pos += 8
            nul = strings_block.index(0, nameoff)
            prop_name = strings_block[nameoff:nul].decode("ascii", "replace")
            if current is None:
                raise ValueError("property outside a node")
            current.props[prop_name] = bytes(data[pos:pos + length])
            pos = align4(pos + length)
        elif token == FDT_NOP:
            continue
        elif token == FDT_END:
            break
        else:
            raise ValueError("unknown FDT token %#x" % token)
    if root is None:
        raise ValueError("DTB has no root")
    return root


def fdt_walk(node: FdtNode):
    yield node
    for child in node.children:
        yield from fdt_walk(child)


def inherited(node: FdtNode | None, prop: str) -> bytes | None:
    while node is not None:
        if prop in node.props:
            return node.props[prop]
        node = node.parent
    return None


def one_be32(value: bytes | None, default: int) -> int:
    return be32s(value)[0] if value else default


def fdt_phandles(root: FdtNode) -> dict[int, FdtNode]:
    out = {}
    for node in fdt_walk(root):
        for key in ("phandle", "linux,phandle"):
            if key in node.props and len(node.props[key]) == 4:
                out[be32s(node.props[key])[0]] = node
    return out


def node_compatibles(node: FdtNode) -> list[str]:
    return cstrings(node.props.get("compatible", b""))


def decode_fdt_reg(node: FdtNode) -> list[tuple[int, int]]:
    if node.parent is None or "reg" not in node.props:
        return []
    acells = one_be32(inherited(node.parent, "#address-cells"), 2)
    scells = one_be32(inherited(node.parent, "#size-cells"), 1)
    if scells == 0:
        return []
    width = acells + scells
    cells = be32s(node.props["reg"])
    if width == 0 or len(cells) % width:
        return []
    return [
        (be_int(cells[pos:pos + acells]), be_int(cells[pos + acells:pos + width]))
        for pos in range(0, len(cells), width)
    ]


def translate_fdt_address(bus: FdtNode, address: int, size: int) -> int | None:
    """Translate an address from *bus* through its ancestors to root."""
    current = bus
    value = address
    while current.parent is not None:
        ranges = current.props.get("ranges")
        if ranges is not None:
            if ranges:
                child_ac = one_be32(current.props.get("#address-cells"), 2)
                child_sc = one_be32(current.props.get("#size-cells"), 1)
                parent_ac = one_be32(inherited(current.parent, "#address-cells"), 2)
                cells = be32s(ranges)
                width = child_ac + parent_ac + child_sc
                if width == 0 or len(cells) % width:
                    return None
                translated = None
                for pos in range(0, len(cells), width):
                    child = be_int(cells[pos:pos + child_ac])
                    parent = be_int(cells[pos + child_ac:pos + child_ac + parent_ac])
                    span = be_int(cells[pos + child_ac + parent_ac:pos + width])
                    if child <= value and value + size <= child + span:
                        translated = parent + value - child
                        break
                if translated is None:
                    return None
                value = translated
        elif current.path not in ("/", "/soc"):
            # An absent ranges property does not define a child-to-parent map.
            return None
        current = current.parent
    return value


def fdt_mmio_windows(node: FdtNode) -> list[tuple[int, int]]:
    if node.parent is None:
        return []
    if any("pmgr-pwrstate" in item for item in node_compatibles(node)):
        return []
    ancestor = node.parent
    while ancestor is not None:
        if ancestor is not node and b"pci\0" in ancestor.props.get("device_type", b""):
            return []
        ancestor = ancestor.parent
    out = []
    for address, size in decode_fdt_reg(node):
        translated = translate_fdt_address(node.parent, address, size)
        if translated is not None and translated >= 0x100000000 and size:
            out.append((translated, size))
    return out


def decode_adt_ranges(arm_io: dict) -> list[tuple[int, int, int]]:
    cells = adt.u32s(arm_io["props"]["ranges"])
    width = 6  # two child-address, two parent-address, two size cells
    if len(cells) % width:
        raise ValueError("/arm-io ranges does not contain six-cell tuples")
    out = []
    for pos in range(0, len(cells), width):
        child = cells[pos] | cells[pos + 1] << 32
        parent = cells[pos + 2] | cells[pos + 3] << 32
        size = cells[pos + 4] | cells[pos + 5] << 32
        out.append((child, parent, size))
    return out


def translate_adt(address: int, size: int, ranges: list[tuple[int, int, int]]) -> int | None:
    matches = []
    for child, parent, span in ranges:
        if child <= address and address + size <= child + span:
            matches.append((span, parent + address - child))
    if not matches:
        return None
    return min(matches)[1]


@dataclasses.dataclass(frozen=True)
class AdtWindow:
    path: str
    node: dict
    index: int
    address: int
    size: int
    source: str = "reg"


def adt_windows(root: dict, ranges: list[tuple[int, int, int]]) -> list[AdtWindow]:
    out = []
    for path, node in adt.walk(root):
        if not path.startswith("/arm-io/"):
            continue
        raw = node["props"].get("reg")
        if raw and len(raw) % 16 == 0:
            for index, (address, size) in enumerate(adt.regs(raw)):
                translated = translate_adt(address, size, ranges)
                if translated is not None and size:
                    out.append(AdtWindow(path, node, index, translated, size))
        # RTKit SRAM is described by an absolute physical base/size pair.
        rb = node["props"].get("region-base")
        rs = node["props"].get("region-size")
        if rb and rs and len(rb) in (4, 8) and len(rs) in (4, 8):
            base_cells = adt.u32s(rb)
            size_cells = adt.u32s(rs)
            base = sum(cell << (32 * i) for i, cell in enumerate(base_cells))
            span = sum(cell << (32 * i) for i, cell in enumerate(size_cells))
            if base >= 0x100000000 and span:
                out.append(AdtWindow(path, node, 0, base, span, "region"))
    return out


def path_tokens(path: str) -> set[str]:
    return {token for token in re.split(r"[^a-z0-9]+", path.lower()) if token and not token.isdigit()}


def is_t8132_nvme_oversize(dt_node: FdtNode, dt_window: tuple[int, int], adt_window: AdtWindow) -> bool:
    """Accept only the evidenced T8132 split-NVMe secure-BAR overmap."""
    return (
        dt_node.path == T8132_NVME_DT_PATH
        and dt_window == T8132_NVME_DT_WINDOW
        and adt_window.path == T8132_NVME_ADT_PATH
        and adt_window.index == T8132_NVME_ADT_REG_INDEX
        and (adt_window.address, adt_window.size) == T8132_NVME_ADT_WINDOW
    )


def adt_window_covers(dt_node: FdtNode, dt_window: tuple[int, int], adt_window: AdtWindow) -> bool:
    address, size = dt_window
    return (
        adt_window.address <= address
        and address + size <= adt_window.address + adt_window.size
    ) or is_t8132_nvme_oversize(dt_node, dt_window, adt_window)


def choose_adt_candidate(dt_node: FdtNode, windows: list[tuple[int, int]], adt_ws: list[AdtWindow]) -> AdtWindow | None:
    if not windows:
        return None
    address, _ = windows[0]
    choices = [w for w in adt_ws if adt_window_covers(dt_node, windows[0], w)]
    if not choices:
        return None
    dt_tokens = path_tokens(dt_node.path)
    for compatible in node_compatibles(dt_node):
        dt_tokens |= path_tokens(compatible)
    choices.sort(key=lambda w: (
        0 if w.address == address else 1,
        -len(dt_tokens & path_tokens(w.path)),
        w.size,
        w.path,
    ))
    return choices[0]


def format_windows(windows: list[tuple[int, int]]) -> str:
    return ", ".join("%#x/%#x" % item for item in windows) if windows else "-"


def find_interrupt_parent(node: FdtNode, phandles: dict[int, FdtNode]) -> FdtNode | None:
    current = node
    while current is not None:
        raw = current.props.get("interrupt-parent")
        if raw and len(raw) == 4:
            return phandles.get(be32s(raw)[0])
        current = current.parent
    return None


def fdt_interrupts(node: FdtNode, phandles: dict[int, FdtNode]) -> list[int]:
    out = []
    raw_ext = node.props.get("interrupts-extended")
    if raw_ext:
        cells = be32s(raw_ext)
        pos = 0
        while pos < len(cells):
            handle = cells[pos]
            pos += 1
            if handle == 0:
                continue
            provider = phandles.get(handle)
            if provider is None:
                break
            count = one_be32(provider.props.get("#interrupt-cells"), 1)
            spec = cells[pos:pos + count]
            pos += count
            if len(spec) != count:
                break
            out.append(spec[1] if count == 3 else spec[0])
        return out
    raw = node.props.get("interrupts")
    if not raw:
        return out
    provider = find_interrupt_parent(node, phandles)
    count = one_be32(provider.props.get("#interrupt-cells") if provider else None, 1)
    cells = be32s(raw)
    if count == 0 or len(cells) % count:
        return out
    for pos in range(0, len(cells), count):
        spec = cells[pos:pos + count]
        out.append(spec[1] if count == 3 else spec[0])
    return out


def fdt_iommus(node: FdtNode, phandles: dict[int, FdtNode]) -> list[tuple[str, int]]:
    raw = node.props.get("iommus")
    if not raw:
        return []
    cells = be32s(raw)
    pos = 0
    out = []
    while pos < len(cells):
        provider = phandles.get(cells[pos])
        pos += 1
        if provider is None:
            break
        count = one_be32(provider.props.get("#iommu-cells"), 0)
        args = cells[pos:pos + count]
        pos += count
        if len(args) != count:
            break
        if args:
            out.append((provider.path, args[0]))
    return out


def fdt_power_domains(node: FdtNode, phandles: dict[int, FdtNode]) -> list[str]:
    raw = node.props.get("power-domains")
    if not raw:
        return []
    cells = be32s(raw)
    pos = 0
    out = []
    while pos < len(cells):
        provider = phandles.get(cells[pos])
        pos += 1
        if provider is None:
            break
        count = one_be32(provider.props.get("#power-domain-cells"), 0)
        pos += count
        labels = cstrings(provider.props.get("label", b""))
        out.append(labels[0] if labels else provider.name.split("@")[0])
    return out


def norm_name(value: str) -> str:
    value = value.lower().replace("-", "_")
    value = re.sub(r"[^a-z0-9_]+", "_", value)
    return re.sub(r"_+", "_", value).strip("_")


@dataclasses.dataclass
class PmgrDevice:
    index: int
    flags: int
    id1: int
    id2: int
    parents: tuple[int, int]
    addr_offset: int
    psreg_idx: int
    group_and_offset: int
    name: str

    @property
    def virtual(self) -> bool:
        return bool(self.flags & 0x10)

    @property
    def always_on(self) -> bool:
        return bool(self.flags & 0x08)


def decode_pmgr_devices(raw: bytes) -> list[PmgrDevice]:
    if len(raw) % 48:
        raise ValueError("pmgr devices length is not divisible by 48")
    out = []
    for index in range(len(raw) // 48):
        record = raw[index * 48:(index + 1) * 48]
        out.append(PmgrDevice(
            index=index,
            flags=record[0],
            id1=record[3],
            id2=struct.unpack_from("<H", record, 26)[0],
            parents=struct.unpack_from("<2H", record, 4),
            addr_offset=record[10],
            psreg_idx=record[11],
            group_and_offset=struct.unpack_from("<I", record, 16)[0],
            name=record[32:48].split(b"\0", 1)[0].decode("ascii", "replace"),
        ))
    return out


def decode_psregs(raw: bytes) -> list[tuple[int, int, int]]:
    """Decode (pmgr-reg-index, offset, valid-device-mask) records."""
    if len(raw) % 12:
        raise ValueError("pmgr ps-regs length is not divisible by 12")
    return [struct.unpack_from("<III", raw, pos) for pos in range(0, len(raw), 12)]


@dataclasses.dataclass
class PmgrProvider:
    source_label: str
    unit: int
    reg: int
    label: str
    parents: list[str]
    always_on: bool


def parse_pmgr_source(path: pathlib.Path) -> list[PmgrProvider]:
    text = path.read_text()
    pattern = re.compile(
        r"(?m)^\s*(?P<src>[A-Za-z0-9_]+):\s+power-controller@(?P<unit>[0-9a-fA-F]+)\s*\{(?P<body>.*?)^\s*\};",
        re.S,
    )
    out = []
    for match in pattern.finditer(text):
        body = match.group("body")
        reg_match = re.search(r"\breg\s*=\s*<\s*(0x[0-9a-fA-F]+|[0-9]+)\s+", body)
        label_match = re.search(r'\blabel\s*=\s*"([^"]+)"', body)
        if not reg_match or not label_match:
            continue
        pd_match = re.search(r"\bpower-domains\s*=\s*(.*?);", body, re.S)
        parents = re.findall(r"&([A-Za-z0-9_]+)", pd_match.group(1)) if pd_match else []
        out.append(PmgrProvider(
            source_label=match.group("src"),
            unit=int(match.group("unit"), 16),
            reg=int(reg_match.group(1), 0),
            label=label_match.group(1),
            parents=parents,
            always_on="apple,always-on" in body,
        ))
    return out


def pmgr_expected_offset(device: PmgrDevice, psregs: list[tuple[int, int, int]]) -> tuple[int, int]:
    if device.group_and_offset:
        index = device.group_and_offset >> 24
    else:
        index = device.psreg_idx
    reg_index, base, _ = psregs[index]
    return reg_index, base + (device.addr_offset << 3)


def adt_iommu_sids(candidate: AdtWindow, adt_handles: dict[int, tuple[str, dict]], wanted: int) -> list[int]:
    raw = candidate.node["props"].get("iommu-parent")
    if not raw:
        # Some ADT devices (e.g. dockchannel-mtp) carry iommu-parent on a
        # transport child rather than on the MMIO node itself.
        for child in candidate.node.get("children", []):
            raw = child["props"].get("iommu-parent")
            if raw:
                break
    if not raw or len(raw) % 4:
        return []
    target = adt_handles.get(adt.u32s(raw)[0])
    if target is None:
        return []
    _, mapper = target
    mapper_reg = mapper["props"].get("reg")
    mapper_sid = adt.u32s(mapper_reg)[0] if mapper_reg and len(mapper_reg) == 4 else None
    parent = None
    for path, node in adt_handles.values():
        if mapper in node.get("children", []):
            parent = node
            break
    if wanted > 1 and parent is not None and parent["props"].get("sid"):
        return adt.u32s(parent["props"]["sid"])
    return [mapper_sid] if mapper_sid is not None else []


def audit_nodes(fdt_root: FdtNode, adt_root: dict, adt_ws: list[AdtWindow], pmgr_real: set[str]):
    phandles = fdt_phandles(fdt_root)
    adt_handles = adt.phandles(adt_root)
    rows = []
    matched_adt_paths = set()
    for node in fdt_walk(fdt_root):
        if not node.path.startswith("/soc/"):
            continue
        windows = fdt_mmio_windows(node)
        if not windows:
            continue
        candidate = choose_adt_candidate(node, windows, adt_ws)
        if candidate is None:
            rows.append(("DT-ONLY", node.path, "-", format_windows(windows), "-", "-", "-"))
            continue
        matched_adt_paths.add(candidate.path)
        uncovered = []
        oversized = []
        for window in windows:
            if not any(adt_window_covers(node, window, w) for w in adt_ws):
                uncovered.append(window)
            elif any(is_t8132_nvme_oversize(node, window, w) for w in adt_ws):
                oversized.append(window)
        verdict = "MISMATCH" if uncovered else ("MATCH-OVERSIZE" if oversized else "MATCH")
        candidate_windows = [
            (window.address, window.size) for window in adt_ws
            if window.path == candidate.path and window.source == "reg"
        ]
        if oversized and not uncovered:
            reg_detail = "DT %s; ADT %s; MATCH-OVERSIZE (%s)" % (
                format_windows(windows), format_windows(candidate_windows),
                T8132_NVME_OVERSIZE_NOTE,
            )
        else:
            reg_status = (format_windows(uncovered) + " uncovered") if uncovered else "contained"
            reg_detail = "DT %s; ADT %s (%s)" % (
                format_windows(windows), format_windows(candidate_windows),
                reg_status,
            )

        dt_irqs = fdt_interrupts(node, phandles)
        adt_irqs = adt.u32s(candidate.node["props"].get("interrupts", b""))
        irq_detail = "-"
        direct_irq_parent = find_interrupt_parent(node, phandles) if "interrupts" in node.props else None
        local_irq = direct_irq_parent is not None and not any(
            "aic" in item for item in node_compatibles(direct_irq_parent)
        )
        if dt_irqs and not local_irq:
            expected = adt_irqs
            if any("nvme" in item for item in node_compatibles(node)) and adt_irqs:
                idx_raw = candidate.node["props"].get("nvme-interrupt-idx")
                idx = adt.u32s(idx_raw)[0] if idx_raw else 0
                expected = [adt_irqs[idx]] if idx < len(adt_irqs) else []
            irq_ok = bool(expected) and all(value in expected for value in dt_irqs)
            irq_detail = "%s vs %s" % (dt_irqs, expected)
            if not irq_ok:
                verdict = "MISMATCH"
        elif dt_irqs:
            irq_detail = "%s (local interrupt domain)" % dt_irqs

        dt_iommus = fdt_iommus(node, phandles)
        iommu_detail = "-"
        if dt_iommus:
            dt_sids = [sid for _, sid in dt_iommus]
            expected_sids = adt_iommu_sids(candidate, adt_handles, len(dt_sids))
            iommu_detail = "%s vs %s" % (dt_sids, expected_sids)
            if expected_sids and dt_sids != expected_sids[:len(dt_sids)]:
                verdict = "MISMATCH"
            elif not expected_sids and verdict == "MATCH":
                # The ADT gave no mapper/sid list to compare against: the
                # stream IDs are not verified, so do not report a full MATCH.
                verdict = "SID-UNVERIFIED"

        domains = fdt_power_domains(node, phandles)
        power_detail = (",".join(domains) + " (live PMGR)") if domains else "-"
        if any(norm_name(item) not in pmgr_real for item in domains):
            power_detail += " (absent from live PMGR)"
            verdict = "MISMATCH"
        rows.append((verdict, node.path, candidate.path, reg_detail, irq_detail, iommu_detail, power_detail))

    adt_only = []
    dt_windows = [
        item for node in fdt_walk(fdt_root) if node.path.startswith("/soc/")
        for item in fdt_mmio_windows(node)
    ]
    seen = set()
    for window in adt_ws:
        if window.path.count("/") != 2 or window.path in seen:
            continue
        seen.add(window.path)
        node_windows = [(w.address, w.size) for w in adt_ws if w.path == window.path and w.source == "reg"]
        if node_windows and not any(
            aw <= dw and dw + ds <= aw + az
            for aw, az in node_windows for dw, ds in dt_windows
        ):
            adt_only.append((window.path, format_windows(node_windows)))
    return rows, adt_only


def audit_pmgr(pmgr_node: dict, providers: list[PmgrProvider]):
    devices = decode_pmgr_devices(pmgr_node["props"]["devices"])
    psregs = decode_psregs(pmgr_node["props"]["ps-regs"])
    real_by_name = {norm_name(device.name): device for device in devices if not device.virtual}
    any_by_name = {norm_name(device.name): device for device in devices}
    source_by_name = {norm_name(provider.label): provider for provider in providers}
    id_map = {device.id2: device for device in devices if device.id2}
    for device in devices:
        if device.id1:
            id_map.setdefault(device.id1, device)

    def represented_parents(device: PmgrDevice, trail=None) -> set[str]:
        trail = set() if trail is None else trail
        out = set()
        for parent_id in device.parents:
            if not parent_id or parent_id in trail:
                continue
            parent = id_map.get(parent_id)
            if parent is None:
                continue
            if norm_name(parent.name) in source_by_name and not parent.virtual:
                out.add(norm_name(parent.name))
            else:
                out |= represented_parents(parent, trail | {parent_id})
        return out

    rows = []
    for provider in providers:
        key = norm_name(provider.label)
        device = any_by_name.get(key)
        verdicts = []
        adt_offset = "-"
        adt_name = "-"
        adt_parents = set()
        if device is None:
            verdicts.append("absent")
        elif device.virtual:
            verdicts.append("virtual-only")
            adt_name = device.name
        else:
            adt_name = device.name
            reg_index, offset = pmgr_expected_offset(device, psregs)
            adt_offset = "%d:%#x" % (reg_index, offset)
            expected_reg_index = 1 if provider.source_label.startswith("ps_nub_") or provider.source_label in {
                "ps_debug_gated", "ps_debug_switch", "ps_atc0_usb_aon", "ps_atc1_usb_aon",
                "ps_atc2_usb_aon", "ps_atc3_usb_aon", "ps_atc0_usb", "ps_atc1_usb",
                "ps_atc2_usb", "ps_atc3_usb",
            } else 0
            if provider.reg != offset or reg_index != expected_reg_index or provider.unit != provider.reg:
                verdicts.append("offset")
            adt_parents = represented_parents(device)
            # Source phandle labels use ps_* while label properties do not.
            dt_parent_names = {
                norm_name(next((p.label for p in providers if p.source_label == parent), parent.removeprefix("ps_")))
                for parent in provider.parents
            }
            if dt_parent_names != adt_parents:
                verdicts.append("parents")
            if provider.always_on != device.always_on:
                verdicts.append("always-on")
        rows.append((
            "MATCH" if not verdicts else "MISMATCH",
            provider.source_label,
            provider.label,
            "%#x" % provider.reg,
            adt_name,
            adt_offset,
            ",".join(provider.parents) or "-",
            ",".join(sorted(adt_parents)) or "-",
            "yes" if provider.always_on else "no",
            "yes" if device and not device.virtual and device.always_on else "no",
            ",".join(verdicts) or "-",
        ))
    return devices, psregs, rows, real_by_name


def markdown_table(headers, rows):
    print("| " + " | ".join(headers) + " |")
    print("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        print("| " + " | ".join(str(item).replace("|", "\\|") for item in row) + " |")


def hpm_summary(adt_root: dict):
    rows = []
    for path, node in adt.walk(adt_root):
        if path in ("/arm-io/nub-spmi-a0/hpm0", "/arm-io/nub-spmi-a0/hpm1", "/arm-io/nub-spmi-a0/hpm5"):
            props = node["props"]
            reg = adt.u32s(props["reg"])
            ints = adt.u32s(props["interrupts"])
            kinds = adt.u32s(props["interrupt-type"])
            location = adt.strings(props.get("port-location", b""))
            port = adt.u32s(props.get("port-number", b"\0\0\0\0"))[0]
            rows.append((path.rsplit("/", 1)[-1], "%#x" % reg[0], ints, kinds, location[0] if location else "internal", port))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("adt", type=pathlib.Path)
    parser.add_argument("dtb", type=pathlib.Path)
    parser.add_argument("--pmgr-source", type=pathlib.Path)
    args = parser.parse_args()

    adt_root, end = adt.parse(args.adt.read_bytes())
    if end != args.adt.stat().st_size:
        raise ValueError("trailing data after ADT root")
    fdt_root = parse_dtb(args.dtb.read_bytes())
    arm_io = adt.find_path(adt_root, "/arm-io")
    pmgr = adt.find_path(adt_root, "/arm-io/pmgr")
    if arm_io is None or pmgr is None:
        raise ValueError("ADT lacks /arm-io or /arm-io/pmgr")
    ranges = decode_adt_ranges(arm_io)
    windows = adt_windows(adt_root, ranges)

    providers = parse_pmgr_source(args.pmgr_source) if args.pmgr_source else []
    devices, psregs, pmgr_rows, real_by_name = audit_pmgr(pmgr, providers)
    node_rows, adt_only = audit_nodes(fdt_root, adt_root, windows, set(real_by_name))

    print("# T8132 live-ADT audit output")
    print()
    print("## ADT `/arm-io` ranges")
    print()
    markdown_table(("index", "child", "parent", "size", "translation"), [
        (index, "%#x" % child, "%#x" % parent, "%#x" % size,
         "identity" if child == parent else "%+#x" % (parent - child))
        for index, (child, parent, size) in enumerate(ranges)
    ])
    print()
    print("## DT node audit")
    print()
    markdown_table(("verdict", "DT node", "ADT node", "reg", "IRQs DT vs ADT", "SIDs DT vs ADT", "power domains"), node_rows)
    print()
    counts = Counter(row[0] for row in node_rows)
    print("Node summary: " + ", ".join("%s=%d" % item for item in sorted(counts.items())))
    print()
    print("## ADT-only MMIO nodes")
    print()
    markdown_table(("ADT node", "translated registers"), adt_only)
    print()
    print("ADT-only summary: %d" % len(adt_only))
    print()
    print("## PMGR decode")
    print()
    print("Devices: %d records x 48 bytes; virtual records: %d; group_and_offset nonzero: %d." % (
        len(devices), sum(device.virtual for device in devices), sum(bool(device.group_and_offset) for device in devices)))
    print()
    markdown_table(("psreg", "reg index", "offset", "valid mask"), [
        (index, reg_index, "%#x" % offset, "%#x" % mask)
        for index, (reg_index, offset, mask) in enumerate(psregs)
    ])
    if pmgr_rows:
        print()
        markdown_table(("verdict", "source", "DT label", "DT offset", "ADT device", "ADT reg:offset", "DT parents", "ADT parents", "DT AON", "ADT AON", "reason"), pmgr_rows)
        counts = Counter(row[0] for row in pmgr_rows)
        print()
        print("PMGR provider summary: " + ", ".join("%s=%d" % item for item in sorted(counts.items())))
    print()
    print("## HPM/SPMI decode")
    print()
    markdown_table(("HPM", "USID", "ADT interrupts", "ADT types", "location", "port-number"), hpm_summary(adt_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
