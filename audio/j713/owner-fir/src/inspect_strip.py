# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import hashlib
import json
from pathlib import Path
import plistlib
import struct


def decode_blocks(data):
    blocks = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 12:
            raise ValueError("truncated block header")
        scope, element, count = struct.unpack_from(">III", data, offset)
        offset += 12
        if count > (len(data) - offset) // 8:
            raise ValueError("truncated parameter records")
        params = []
        for _ in range(count):
            ident, value = struct.unpack_from(">If", data, offset)
            params.append({"id": ident, "value": value, "bytes": data[offset:offset+8].hex()})
            offset += 8
        if len({p["id"] for p in params}) != count:
            raise ValueError("duplicate parameter ID within block")
        blocks.append({"scope": scope, "element": element, "params": params})
    return blocks


def main():
    source = Path("source")
    target = Path("decoded")
    target.mkdir()
    data = plistlib.loads((source / "builtin_speaker_out_general.austrip").read_bytes())
    report = {"strip_keys": list(data), "effects": []}
    for strip in data["strips"]:
        for effect in strip["effects"]:
            preset = effect["aupreset"]
            name = effect["displayname"]
            row = {"name": name, "bypass": effect["bypass"],
                   "unit": effect["unit"], "preset_keys": list(preset),
                   "preset_name": preset.get("name"),
                   "subtype": preset["subtype"].to_bytes(4, "big").decode(),
                   "data_bytes": len(preset.get("data", b"")),
                   "data_sha256": hashlib.sha256(preset.get("data", b"")).hexdigest()}
            if name in {"Tone1", "Tone2", "Tone3", "ControlFreakLF", "ControlFreakHF", "LSM", "SysID", "AUVolumeTaper"}:
                blocks = decode_blocks(preset["data"])
                row["blocks"] = [{"scope": b["scope"], "element": b["element"], "count": len(b["params"])} for b in blocks]
                (target / (name + ".json")).write_text(json.dumps(blocks, indent=2) + "\n")
                (target / (name + ".aupreset")).write_bytes(plistlib.dumps(preset, fmt=plistlib.FMT_BINARY))
            report["effects"].append(row)
    (target / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    prop = plistlib.loads((source / "builtin_speaker_out_general.propstrip").read_bytes())
    (target / "propstrip.json").write_text(json.dumps(prop, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
