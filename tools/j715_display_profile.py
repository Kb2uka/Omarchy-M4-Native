#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Parse J715 display evidence; production commands never accept a synthetic profile."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from j715_sources import SourceLock, local_sources, unique_pairs

ROOT: Final = Path(__file__).resolve().parents[1]
BASE_DTB: Final = "f661d60920c8a202c6d4ab5839dd8681ad302e45234710d217f453aa7bb8aac3"


class ProfileError(ValueError):
    """An input cannot qualify the selected native implementation."""


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ProfileError(reason)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_schema(value, rule, schema) -> None:
    """Evaluate the small, explicit schema vocabulary used by this contract."""
    if "$ref" in rule:
        return check_schema(value, schema["$defs"][rule["$ref"].split("/")[-1]], schema)
    if "const" in rule:
        require(type(value) is type(rule["const"]) and value == rule["const"], "Unsupported identity or geometry")
    if "enum" in rule:
        require(value in rule["enum"], "Unsupported qualification")
    kind = rule.get("type")
    if kind:
        require(type(value) is {"object": dict, "array": list, "string": str,
                               "integer": int, "boolean": bool}[kind], "Invalid profile field type")
    if kind == "object":
        require(set(rule["required"]) <= value.keys(), "Missing profile fields")
        require(value.keys() <= rule["properties"].keys(), "Unknown profile fields")
        for name, item in value.items():
            check_schema(item, rule["properties"][name], schema)
    if kind == "array":
        require(rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", 256), "Invalid array size")
        if rule.get("uniqueItems"):
            require(len({json.dumps(v, sort_keys=True) for v in value}) == len(value), "Duplicate array entry")
        for item in value:
            check_schema(item, rule["items"], schema)
    if kind == "string":
        require(rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", 256), "Invalid string size")
        require("\0" not in value and ("pattern" not in rule or re.fullmatch(rule["pattern"], value)),
                "Invalid profile string")
    if kind == "integer":
        require(rule.get("minimum", 0) <= value <= rule.get("maximum", (1 << 64) - 1), "Invalid numeric range")


@dataclass(frozen=True, slots=True)
class Resource:
    path: str
    address: int
    size: int
    compatible: tuple[str, ...]
    interrupts: tuple[int, ...]
    roles: tuple[str, ...]
    power: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Power:
    path: str
    label: str


@dataclass(frozen=True, slots=True)
class Profile:
    digest: str
    base_dtb_sha256: str
    firmware_identity: str
    firmware_sha256: str
    synthetic: bool
    qualification: str
    resources: tuple[Resource, ...]
    interrupt_path: str
    interrupt_compatible: str
    power: tuple[Power, ...]
    stream: int
    min_nits: int
    max_nits: int
    initial_nits: int
    nub_path: str
    dart_path: str


def parse(path: Path) -> Profile:
    """Parse structural evidence, including explicitly nondeployable test fixtures."""
    require(path.is_file() and not path.is_symlink(), "Profile must be a regular file")
    raw = path.read_bytes()
    require(len(raw) <= 65536, "Profile too large")
    data = json.loads(raw, object_pairs_hook=unique_pairs)
    schema = json.loads((ROOT / "display/j715/profile.schema.json").read_bytes())
    check_schema(data, schema, schema)
    require(data["synthetic"] == (data["qualification"] == "synthetic"), "Synthetic qualification mismatch")
    resources = tuple(Resource(v["path"], v["address"], v["size"], tuple(v["compatible"]),
                               tuple(v["interrupts"]), tuple(v["roles"]), tuple(v["power"]))
                      for v in (data["resources"][n] for n in ("dcp", "dart", "mailbox")))
    expected = (("omarchy,j715-dcp-backlight",), ("apple,t8132-dart", "apple,t8110-dart"),
                ("apple,t8132-asc-mailbox", "apple,asc-mailbox-v4"))
    roles = ((), ("fault",), ("send-empty", "send-not-empty", "recv-empty", "recv-not-empty"))
    for resource, compatible, names in zip(resources, expected, roles, strict=True):
        require(resource.compatible == compatible and resource.roles == names and
                len(resource.interrupts) == len(names), "Unsupported resource protocol or interrupt roles")
        require(resource.path.startswith("/soc/") and resource.path.count("/") == 2 and
                resource.path.endswith("@" + format(resource.address, "x")), "Resource path/address mismatch")
        require(resource.address + resource.size <= 1 << 42 and
                not resource.address % 4096 and not resource.size % 4096, "Invalid MMIO geometry")
        require(max(resource.power) < len(data["power"]), "Unknown power dependency")
    require(len({r.path for r in resources}) == 3, "Duplicate resource")
    dcp, dart, mailbox = resources
    require(dcp.address <= mailbox.address and mailbox.address + mailbox.size <= dcp.address + dcp.size,
            "Mailbox outside DCP resource")
    require(dart.address + dart.size <= dcp.address or dcp.address + dcp.size <= dart.address,
            "DART overlaps DCP")
    require(dart.size >= 0x5000, "DART resource cannot contain supported stream registers")
    panel = data["panel"]
    require(panel["min_nits"] <= panel["initial_nits"] <= panel["max_nits"] and
            panel["min_nits"] < panel["max_nits"], "Invalid panel bounds")
    for entry in data["evidence"].values():
        parts = Path(entry["path"]).parts
        require(parts and parts[0] == "evidence" and len(parts) > 1 and
                not Path(entry["path"]).is_absolute() and ".." not in parts,
                "Evidence path escapes profile directory")
    return Profile(sha(raw), data["base_dtb_sha256"], data["firmware"]["identity"],
                   data["firmware"]["sha256"], data["synthetic"], data["qualification"],
                   resources, data["interrupt_parent"]["path"], data["interrupt_parent"]["compatible"],
                   tuple(Power(**v) for v in data["power"]), data["stream"],
                   panel["min_nits"], panel["max_nits"], panel["initial_nits"],
                   data["adt"]["nub"], data["adt"]["dart"])


def qualified(path: Path) -> Profile:
    profile = parse(path)
    require(not profile.synthetic and profile.qualification == "qualified", "No qualified J715 display profile")
    require(re.fullmatch(r"(?:iBoot|mBoot)-[0-9]+(?:\.[0-9]+){1,3}", profile.firmware_identity)
            is not None, "Exact raw boot firmware identity required")
    require(profile.base_dtb_sha256 == BASE_DTB, "Profile does not name the unchanged J715 base DTB")
    raw = path.read_bytes()
    require(sha(raw) == profile.digest, "Profile changed during validation")
    evidence = json.loads(raw, object_pairs_hook=unique_pairs)["evidence"]
    for record in evidence.values():
        asset = path.parent / record["path"]
        require(asset.resolve().is_relative_to(path.parent.resolve()) and not asset.is_symlink() and
                asset.is_file(), "Evidence asset missing or escaping")
        require(sha(asset.read_bytes()) == record["sha256"], "Evidence hash mismatch")
    require(evidence["firmware"]["sha256"] == profile.firmware_sha256, "Firmware evidence identity differs")
    return profile


def header(profile: Profile) -> str:
    """Only callers holding qualified evidence may publish this as build input."""
    values = {"PROFILE_SHA256": profile.digest, "FIRMWARE_ID": profile.firmware_identity,
              "FIRMWARE_SHA256": profile.firmware_sha256, "STREAM": profile.stream,
              "PANEL_MIN_NITS": profile.min_nits, "PANEL_MAX_NITS": profile.max_nits,
              "PANEL_INITIAL_NITS": profile.initial_nits, "DMA_BITS": 42,
              "ADT_NUB": profile.nub_path, "ADT_DART": profile.dart_path,
              "ADT_DCP": profile.nub_path.rsplit("/", 1)[0],
              "DCP_ADDRESS": profile.resources[0].address,
              "DCP_SIZE": profile.resources[0].size,
              "DART_ADDRESS": profile.resources[1].address,
              "DART_SIZE": profile.resources[1].size}
    return "/* Generated scratch input; never a production profile. */\n" + "".join(
        f"#define J715_{name} {json.dumps(value)}\n" for name, value in values.items())


def stage(profile_path: Path, destination: Path) -> None:
    profile = qualified(profile_path)
    raw = profile_path.read_bytes()
    require(sha(raw) == profile.digest, "Profile changed during staging")
    lock = SourceLock.read(ROOT / "display/j715/source-lock.json")
    sources = local_sources(ROOT, lock)
    require(not destination.exists() and not destination.is_symlink(), "Staging directory already exists")
    destination.mkdir()
    for source in sources:
        target = destination / source.pin.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.data)
    (destination / "j715-profile-generated.h").write_text(header(profile))
    (destination / "profile.json").write_bytes(raw)
    evidence = json.loads(raw, object_pairs_hook=unique_pairs)["evidence"]
    for record in evidence.values():
        data = (profile_path.parent / record["path"]).read_bytes()
        require(sha(data) == record["sha256"], "Evidence changed during staging")
        target = destination / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (destination / "source-receipt.json").write_text(json.dumps({
        "profile_sha256": profile.digest, "source_lock_sha256": sha(lock.raw),
        "commit": lock.commit, "source_lock": json.loads(lock.raw), "hardware_success": False,
        "files": {s.pin.path: s.pin.sha256 for s in sources}}, indent=2) + "\n")


def receipt(profile_path: Path, output: Path, kernel: Path | None) -> None:
    """Bind actual outputs, source closure and supplied immutable builder identity."""
    profile = qualified(profile_path)
    image = os.environ.get("J715_BUILD_IMAGE_ID", "")
    require(re.fullmatch("sha256:[0-9a-f]{64}", image) is not None, "Immutable builder image ID required")
    paths = ["tools/j715_display_profile.py", "tools/j715_sources.py", "tools/build-j715-backlight-dt.py",
             "tools/build-j715-backlight.sh", "tools/m4-j715-backlight.py", "tools/m4-backlight.py",
             "display/j715/profile.schema.json", "display/j715/backlight/Kbuild",
             "display/j715/backlight/client.c", "display/j715/backlight/profile.h",
             "display/j715/build-backlight-loader.sh", "display/j715/m1n1-sep-handoff.patch",
             "display/j715/m1n1-preserve-sep-rng.patch", "display/j715/m1n1-j715-handoff.patch"]
    binaries = sorted(output.glob("*.ko" if kernel else "*.bin"))
    require(len(binaries) == (1 if kernel else 3), "Incomplete output closure")
    record = {"profile_sha256": profile.digest, "builder_image": image, "hardware_success": False,
              "comparison": "not-rebuilt-identical", "patch_sha256": sha((ROOT / paths[-1]).read_bytes()),
              "source_receipt_sha256": sha((output / "source-receipt.json").read_bytes()),
              "integration_sources": {p: sha((ROOT / p).read_bytes()) for p in paths},
              "outputs": {p.name: {"bytes": p.stat().st_size, "sha256": sha(p.read_bytes())} for p in binaries}}
    if kernel:
        inputs = (".config", "Module.symvers", "include/generated/utsrelease.h", "vmlinux")
        record["kernel_inputs"] = {p: sha((kernel / p).read_bytes()) for p in inputs}
        record["kernel_release"] = re.fullmatch(
            r'#define UTS_RELEASE "([^"]+)"\s*', (kernel / inputs[2]).read_text()).group(1)
        record["module_bytes"] = binaries[0].stat().st_size
        record["module_sha256"] = sha(binaries[0].read_bytes())
        record["flags"] = ["ARCH=arm64", "LLVM=/usr/lib/llvm-21/bin/", "LOCALVERSION=", "KCFLAGS=-Werror"]
        environment = os.environ.copy()
        for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
            require(bool(environment.get(key)), "Missing subprocess environment: " + key)
        environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
        record["abi_and_build_notes"] = subprocess.check_output(
            ["/usr/lib/llvm-21/bin/llvm-readelf", "-h", "-n", str(binaries[0])],
            env=environment, text=True)
    else:
        record["flags"] = ["BUILDSTD=1", "USE_CLANG=1", "RELEASE=1", "CHAINLOADING=0", "NO_DISPLAY=1"]
    with (output / ("module.json" if kernel else "loader-receipt.json")).open("x") as stream:
        json.dump(record, stream, indent=2)
        stream.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--kernel", type=Path)
    args = parser.parse_args()
    try:
        if args.receipt:
            receipt(args.profile, args.receipt, args.kernel)
        elif args.stage:
            stage(args.profile, args.stage)
        else:
            print(qualified(args.profile).digest)
    except (OSError, ValueError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
