#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Add only profile-bound brightness resources to the unchanged J715 base DTB."""
import argparse
import importlib.util
import json
import struct
import sys
from pathlib import Path

from j715_display_profile import Profile, qualified, require, sha

SPEC = importlib.util.spec_from_file_location(
    "j715_base_parser", Path(__file__).with_name("build-j713-poweroff-dtb.py"))
PARSER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PARSER)
tree = PARSER.tree


def cells(*values: int) -> bytes:
    return struct.pack(f">{len(values)}I", *values)


def strings(values: tuple[str, ...]) -> bytes:
    return b"".join(v.encode() + b"\0" for v in values)


def expected_nodes(old, profile: Profile):
    require(old["/"]["compatible"] == b"apple,j715\0apple,t8132\0apple,arm-platform\0" and
            old["/"]["model"] == b"Apple MacBook Air (15-inch, M4, 2025)\0", "Wrong board")
    require(old["/soc"]["#address-cells"] == cells(2) and
            old["/soc"]["#size-cells"] == cells(2), "Unsupported bus geometry")
    require("/aliases" in old and "dcp" not in old["/aliases"], "DCP alias already exists")
    require(all(r.path not in old for r in profile.resources), "Resource already exists")
    require(all("omarchy,display-handoff-verified" not in props for props in old.values()),
            "Base contains a replayed handoff marker")
    aic = old[profile.interrupt_path]
    require(profile.interrupt_compatible.encode() in aic["compatible"].split(b"\0") and
            aic["#interrupt-cells"] == cells(3), "Wrong interrupt controller")
    power = []
    for dependency in profile.power:
        props = old[dependency.path]
        require(props["label"] == strings((dependency.label,)) and
                props["#power-domain-cells"] == cells(0), "Wrong power dependency")
        power.append(props["phandle"])
    handles = [struct.unpack(">I", props["phandle"])[0] for props in old.values() if "phandle" in props]
    require(len(set(handles)) == len(handles) and max(handles) < 0xfffffffc, "Invalid phandle space")
    top = max(handles)
    expected = {path: dict(props) for path, props in old.items()}
    for index, resource in enumerate(profile.resources, 1):
        props = {
            "compatible": strings(resource.compatible),
            "reg": struct.pack(">QQ", resource.address, resource.size),
            "power-domains": b"".join(power[n] for n in resource.power),
            "phandle": cells(top + index), "status": b"okay\0"}
        if resource.interrupts:
            props.update({"interrupt-parent": aic["phandle"],
                          "interrupts": b"".join(cells(0, irq, 4) for irq in resource.interrupts),
                          "interrupt-names": strings(resource.roles)})
        expected[resource.path] = props
    client, dart, mailbox = (r.path for r in profile.resources)
    expected[dart]["#iommu-cells"] = cells(1)
    expected[mailbox]["#mbox-cells"] = cells(0)
    expected[client].update({
        "iommus": cells(top + 2, profile.stream), "mboxes": cells(top + 3),
        "mbox-names": b"mbox\0", "omarchy,display-profile": strings((profile.digest,)),
        "omarchy,display-firmware": strings((profile.firmware_identity,))})
    expected["/aliases"]["dcp"] = strings((client,))
    return expected


def encode(nodes, reservations, cpu: int) -> bytes:
    """Serialize FDT v17 directly, preserving raw property and reservation bytes."""
    names, offsets, words = bytearray(), {}, bytearray()
    def node(path: str) -> None:
        name = "" if path == "/" else path.rsplit("/", 1)[1]
        words.extend(cells(1) + name.encode("ascii") + b"\0")
        words.extend(bytes(-len(words) % 4))
        for key, value in nodes[path].items():
            if key not in offsets:
                offsets[key] = len(names)
                names.extend(key.encode("ascii") + b"\0")
            words.extend(cells(3, len(value), offsets[key]) + value)
            words.extend(bytes(-len(words) % 4))
        children = [p for p in nodes if p != "/" and (p.rsplit("/", 1)[0] or "/") == path]
        for child in children:
            node(child)
        words.extend(cells(2))
    node("/")
    words.extend(cells(9))
    reserved = b"".join(struct.pack(">QQ", address, size) for address, size in reservations)
    start = 40 + len(reserved)
    header = cells(0xd00dfeed, start + len(words) + len(names), start, start + len(words),
                   40, 17, 16, cpu, len(names), len(words))
    return header + reserved + words + names


def build(base: Path, profile_path: Path, output: Path):
    profile = qualified(profile_path)
    require(base.is_file() and not base.is_symlink(), "Base must be a regular file")
    original = base.read_bytes()
    require(sha(original) == profile.base_dtb_sha256, "Base DTB identity mismatch")
    require(not output.exists() and not output.is_symlink(), "Output must not exist")
    old, reservations, cpu = tree(original)
    expected = expected_nodes(old, profile)
    result = encode(expected, reservations, cpu)
    require(tree(result) == (expected, reservations, cpu), "Unexpected DT property, reservation or boot CPU change")
    require(base.read_bytes() == original, "Base changed during build")
    with output.open("xb") as stream:
        stream.write(result)
    return {"profile_sha256": profile.digest, "output_sha256": sha(result),
            "base_sha256": sha(original), "hardware_success": False, "handoff_marker": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.base, args.profile, args.output), indent=2))
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
