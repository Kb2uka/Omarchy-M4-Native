#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Contract for the DTBO and .inc that `make dtbo` already wrote.

dtc -@ exports DTS labels into root /__symbols__. External symbolic
references use /__fixups__. Both require a live root /__symbols__. This Air tree has none, so
of_overlay_fdt_apply rejects it ("symbols in overlay, but not in live tree").
The AIC interrupt-parent is a numeric external phandle and the fragment uses
target-path, so no exported symbol is required.

/plugin/ stays. It makes dtc emit __local_fixups__ for the three local iommus
phandles, at byte offsets 0, 8, and 16. This test only reads those two build
products. It does not invoke dtc, so putting -@ back in the Makefile fails here
instead of being hidden by a second compiler command.
"""

from __future__ import annotations

import pathlib
import re
import struct
import sys

FDT_BEGIN_NODE = 1
FDT_END_NODE = 2
FDT_PROP = 3
FDT_NOP = 4
FDT_END = 9


def usage() -> None:
    raise SystemExit(
        "usage: test_dtbo_contract.py [dtbo inc]\n"
        "paths default to the files next to this script"
    )


def paths(argv: list[str]) -> tuple[pathlib.Path, pathlib.Path]:
    if len(argv) == 1:
        here = pathlib.Path(__file__).resolve().parent
        return here / "j713-isp-dart-map-overlay.dtbo", here / "j713_isp_dartmap_dtbo.inc"
    if len(argv) == 3:
        return pathlib.Path(argv[1]), pathlib.Path(argv[2])
    usage()
    raise AssertionError("usage")


def parse_fdt(raw: bytes) -> dict[str, dict[str, bytes]]:
    header = struct.unpack_from(">10I", raw)
    if header[0] != 0xD00DFEED or header[1] != len(raw):
        raise AssertionError("DTBO is not one complete FDT blob")
    strings = raw[header[3]:header[3] + header[8]]
    pos, stack = header[2], []
    props: dict[str, dict[str, bytes]] = {}
    while True:
        token, = struct.unpack_from(">I", raw, pos)
        pos += 4
        if token == FDT_BEGIN_NODE:
            end = raw.index(0, pos)
            name = raw[pos:end].decode()
            pos = (end + 4) & ~3
            stack.append(name)
            props["/" + "/".join(stack[1:])] = {}
        elif token == FDT_END_NODE:
            stack.pop()
        elif token == FDT_PROP:
            size, off = struct.unpack_from(">II", raw, pos)
            pos += 8
            name = strings[off:strings.index(0, off)].decode()
            props["/" + "/".join(stack[1:])][name] = raw[pos:pos + size]
            pos = (pos + size + 3) & ~3
        elif token == FDT_NOP:
            pass
        elif token == FDT_END:
            break
        else:
            raise AssertionError(("bad token", token, pos))
    return props


def refuse_exported_symbols(props: dict[str, dict[str, bytes]]) -> None:
    # Root children. A label export is exactly what the live tree rejects.
    exported = [path for path in props if path == "/__symbols__" or path.startswith("/__symbols__/")
                or path == "/__fixups__" or path.startswith("/__fixups__/")]
    if exported:
        raise AssertionError(
            "exported symbols require a live root /__symbols__; found " + ", ".join(sorted(exported))
        )


def check(raw: bytes, inc_text: str) -> None:
    if "_Alignas(8)" not in inc_text:
        raise AssertionError("embedded DTBO lost _Alignas(8)")
    props = parse_fdt(raw)
    refuse_exported_symbols(props)

    base = "/fragment@0/__overlay__"
    if props["/fragment@0"]["target-path"] != b"/soc\0":
        raise AssertionError("fragment target-path is not /soc")
    expected_nodes = [f"{base}/iommu@{addr:x}" for addr in (0x48E8C0000, 0x48E8E0000, 0x48E900000)]
    master = base + "/j713-isp-dart-map-test"
    owned = {path for path in props if path.startswith(base + "/")}
    if owned != set(expected_nodes + [master]):
        raise AssertionError(("owned nodes", sorted(owned)))

    phandles = []
    for path, addr in zip(expected_nodes, (0x48E8C0000, 0x48E8E0000, 0x48E900000)):
        node = props[path]
        if node["reg"] != struct.pack(">QQ", addr, 0xC000):
            raise AssertionError((path, "reg"))
        if node["compatible"] != b"apple,t8110-dart\0":
            raise AssertionError((path, "compatible"))
        if node["#iommu-cells"] != struct.pack(">I", 1):
            raise AssertionError((path, "#iommu-cells"))
        if node["interrupt-parent"] != struct.pack(">I", 13):
            raise AssertionError((path, "interrupt-parent"))
        if node["interrupts"] != struct.pack(">3I", 0, 387, 4):
            raise AssertionError((path, "interrupts"))
        if node["apple,dma-range"] != struct.pack(">2Q", 0, 1 << 36):
            raise AssertionError((path, "apple,dma-range"))
        phandle, = struct.unpack(">I", node["phandle"])
        if not phandle or phandle == 13:
            raise AssertionError((path, "phandle", phandle))
        phandles.append(phandle)
        if set(node) != {
            "reg", "compatible", "#iommu-cells", "interrupt-parent",
            "interrupts", "apple,dma-range", "phandle",
        }:
            raise AssertionError((path, "properties", sorted(node)))
    if len(set(phandles)) != 3:
        raise AssertionError(("phandles", phandles))
    if props[master]["compatible"] != b"kb2uka,j713-isp-dart-map-test\0":
        raise AssertionError("master compatible")
    # SID 0 on each local phandle. These cells are what __local_fixups__ patches.
    if props[master]["iommus"] != b"".join(struct.pack(">2I", phandle, 0) for phandle in phandles):
        raise AssertionError("iommus")
    fixups = props.get("/__local_fixups__" + master)
    if fixups is None or fixups.get("iommus") != struct.pack(">3I", 0, 8, 16):
        raise AssertionError("local phandle fixups must be 0, 8, 16")

    embedded = bytes(int(value, 16) for value in re.findall(r"0x([0-9a-f]{2})", inc_text))
    if embedded != raw:
        raise AssertionError("embedded .inc bytes differ from the DTBO")


def main(argv: list[str]) -> int:
    dtbo_path, inc_path = paths(argv)
    raw = dtbo_path.read_bytes()
    inc_text = inc_path.read_text()
    check(raw, inc_text)
    print(
        "PASS DTBO contract: no /__symbols__ or /__fixups__, "
        "local fixups 0/8/16, 3 ranges, AIC 13, SID 0, dma window, embedded bytes"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
