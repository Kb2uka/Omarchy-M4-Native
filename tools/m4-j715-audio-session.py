#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""J715 manifest trust boundary and optional native session gate."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Final

BASE: Final = "/usr/lib/omarchy-m4/j715/audio"
STATE: Final = "/var/lib/omarchy-m4/j715/audio"
MANIFEST: Final = BASE + "/manifest.json"
SELECTED: Final = "/run/omarchy/j715-audio-registration/selected.json"
READY: Final = "/run/omarchy/j715-audio-readiness/ready.json"
SERVICE: Final = "speakersafetyd-j715.service"
DAEMON: Final = BASE + "/speakersafetyd-j715"
PROFILE: Final = BASE + "/config/apple/j715.conf"
HELPER: Final = BASE + "/m4-j715-speaker-id.py"
FIRST_AUTHORIZATION: Final = BASE + "/first-qualification-authorization.json"
FIRST_PROTOCOL: Final = BASE + "/first-qualification-protocol.json"
MODULES: Final = ("snd_soc_j713_group", "apple_admac", "snd_soc_apple_mca",
                  "snd_soc_tas2764", "snd_soc_macaudio", "j713_audio_bootstrap", "j713_audio_dt3")


class Refused(ValueError):
    """An installed identity or observed lifecycle failed its contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise Refused(message)


def command_environment() -> dict[str, str]:
    return {**os.environ, "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "NODE_COMPILE_CACHE": os.environ.get("NODE_COMPILE_CACHE", "/tmp"),
            "BUN_RUNTIME_TRANSPILER_CACHE_PATH": os.environ.get("BUN_RUNTIME_TRANSPILER_CACHE_PATH", "/tmp"),
            "PYTHONDONTWRITEBYTECODE": "1", "GIT_MASTER": "1"}


def load(filename: str):
    """Load an unchanged owner or sibling shipped alongside these frontends."""
    name = "_j715_" + filename.replace("-", "_").replace(".", "_")
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    require(spec is not None and spec.loader is not None, "Missing lifecycle dependency")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def at(root: Path, name: str) -> Path:
    path = Path(name)
    require(path.is_absolute() and ".." not in path.parts, "Invalid absolute path")
    return root / name.lstrip("/")


def trusted(path: Path, root: Path, directory: bool = False) -> None:
    require(path.is_relative_to(root), "Path escapes fixture/runtime root")
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022, "Untrusted path: " + str(item))
        if item == path:
            require(stat.S_ISDIR(info.st_mode) if directory else
                    stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "Unsafe file type/link")
        if item == root:
            break


def pinned(root: Path, name: str, expected: str | None = None) -> bytes:
    path = at(root, name)
    trusted(path, root)
    with path.open("rb") as stream:
        data = stream.read()
    if expected is not None:
        require(re.fullmatch("[0-9a-f]{64}", expected) is not None and
                hashlib.sha256(data).hexdigest() == expected, "Payload differs: " + name)
    return data


def decode(raw: bytes):
    return json.loads(raw, object_pairs_hook=load("j715_sources.py").unique_pairs)


def trusted_json(root: Path, name: str):
    raw = pinned(root, name)
    require(len(raw) <= 262144, "Manifest exceeds 256 KiB")
    return decode(raw), hashlib.sha256(raw).hexdigest()


def admission(manifest) -> tuple[str, bool]:
    """Select exactly one evidence form; a bad qualification never falls back."""
    qualification = manifest["qualification"]
    if qualification is None:
        authorization = manifest["first_qualification_authorization"]
        require(type(authorization) is str and authorization == FIRST_AUTHORIZATION,
                "Invalid first-qualification authorization path")
        return authorization, True
    require(type(qualification) is str and bool(qualification) and
            "first_qualification_authorization" not in manifest, "Ambiguous qualification evidence")
    return qualification, False


def payload_identity(manifest) -> str:
    """Qualification binds the whole immutable payload, without a hash cycle."""
    evidence, _ = admission(manifest)
    value = {k: v for k, v in manifest.items() if k not in (
        "qualification", "first_qualification_authorization", "files")}
    value["files"] = {k: v for k, v in manifest["files"].items()
                      if k != evidence}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def artifacts(root: Path):
    manifest, digest = trusted_json(root, MANIFEST)
    require(manifest["schema"] == 1 and manifest["board"] == "apple,j715" and
            manifest["soc"] == "apple,t8132" and manifest["service"] == SERVICE and
            manifest["profile"] == "conservative-v1", "Wrong native manifest")
    tags = at(root, "/sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
    require(b"apple,j715" in tags and b"apple,t8132" in tags, "Expected J715/T8132")
    require(at(root, "/proc/sys/kernel/osrelease").read_text().strip() ==
            manifest["kernel_release"], "Kernel differs")
    require(not at(root, STATE + "/hold").exists(), "Native audio held")
    evidence, first = admission(manifest)
    required = {DAEMON, PROFILE, HELPER, evidence,
                manifest["build_receipt"], manifest["source_receipt"]}
    required.update(BASE + "/" + name for name in (
        "m4-j715-audio-session.py", "m4-j715-audio-register.py", "m4-j715-audio-ready.py",
        "m4-j713-audio-register.py", "m4-j713-audio-ready.py", "m4-j715-audio-profile.py", "j715_sources.py"))
    require(required <= manifest["files"].keys(),
            "Incomplete immutable payload")
    for name, sha in manifest["files"].items():
        require(name.startswith(BASE + "/"), "Payload outside J715 namespace")
        pinned(root, name, sha)
    require(set(manifest["module_artifacts"]) == set(MODULES) and
            manifest["modules"] == {n: e["note"] for n, e in manifest["module_artifacts"].items()},
            "Module maps differ")
    for name, entry in manifest["module_artifacts"].items():
        require(entry["module"] == name and manifest["files"].get(entry["path"]) == entry["sha256"]
                and re.fullmatch("(?:[0-9a-f]{2})+", entry["note"]) is not None, "Unpinned module")
    build = decode(pinned(root, manifest["build_receipt"]))
    source = decode(pinned(root, manifest["source_receipt"], build["source_receipt_sha256"]))
    require(source["schema"] == 1 and source["board"] == "apple,j715" and
            source["commit"] == manifest["source_commit"] and
            source["lock_sha256"] == manifest["source_lock_sha256"], "Build source receipt differs")
    require(build["schema"] == 1 and build["board"] == "apple,j715" and
            build["profile"] == manifest["profile"] and build["kernel_release"] == manifest["kernel_release"]
            and build["runtime_profile_sha256"] == manifest["files"][PROFILE], "Build receipt differs")
    for name in [DAEMON, *(e["path"] for e in manifest["module_artifacts"].values())]:
        pin = build["outputs"][Path(name).name]
        require(pin["sha256"] == manifest["files"][name] and
                pin["size"] == len(pinned(root, name)), "Build output differs")
    require(bool(manifest["abi_files"]), "Missing runtime ABI")
    for name, pin in manifest["abi_files"].items():
        path = at(root, name)
        built = build["external_inputs"]["sysroot"][name.lstrip("/")]
        if "symlink" in pin:
            trusted(path.parent, root, directory=True)
            require(path.lstat().st_uid == 0 and path.is_symlink() and
                    os.readlink(path) == pin["symlink"] and built == "link:" + pin["symlink"] and
                    "/" not in pin["symlink"] and
                    str(Path(name).with_name(pin["symlink"])) in manifest["abi_files"], "ABI link differs")
        else:
            require(built == pin["sha256"] and len(pinned(root, name, pin["sha256"])) == pin["size"],
                    "Runtime ABI differs")
    qualification = decode(pinned(root, evidence))
    require(type(qualification["schema"]) is int and qualification["schema"] == 1 and
            qualification["payload_sha256"] == payload_identity(manifest) and
            qualification["board"] == "apple,j715" and qualification["soc"] == "apple,t8132",
            "Missing or rejected native qualification")
    if first:
        boot = at(root, "/proc/sys/kernel/random/boot_id").read_text().strip()
        require(qualification["kind"] == "first-qualification-authorization" and
                qualification["status"] == "authorized" and
                qualification["scope"] == "private-protected-test" and
                qualification["hardware_qualified"] is False and
                type(qualification["boot"]) is str and bool(boot) and qualification["boot"] == boot and
                type(qualification["expires_monotonic_ns"]) is int and
                qualification["expires_monotonic_ns"] > time.monotonic_ns() and
                qualification["protocol"] == FIRST_PROTOCOL and FIRST_PROTOCOL in manifest["files"],
                "Missing, stale or rejected first-qualification authorization")
        require(bool(pinned(root, FIRST_PROTOCOL, manifest["files"][FIRST_PROTOCOL])),
                "First-qualification protocol is empty")
    else:
        require(qualification["status"] == "qualified" and qualification["hardware_qualified"] is True,
                "Missing or rejected native qualification")
    return manifest, digest, qualification


def profile(root: Path, manifest, speaker_id: int) -> str:
    """Consume N3's exact Candidate/verify_profile contract from pinned staged bytes."""
    renderer = load("m4-j715-audio-profile.py")
    entry = manifest["profiles"][str(speaker_id)]
    base = entry["root"]
    require(base == BASE + "/profiles/" + str(speaker_id), "Profile namespace differs")
    receipt_path = base + "/profile-receipt.json"
    raw = pinned(root, receipt_path, manifest["files"][receipt_path])
    receipt = decode(raw)
    require(receipt["schema"] == 1 and receipt["speaker_id"] == speaker_id and
            receipt["source_commit"] == manifest["source_commit"] and
            receipt["source_lock_sha256"] == manifest["source_lock_sha256"] and
            receipt["authorization"] == "offline-candidate-only" and
            all(receipt[k] is False for k in ("activated", "profile_accepted", "hardware_qualified")),
            "N3 selection receipt differs")
    files = []
    for name, pin in receipt["files"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "Unsafe profile member")
        full = base + "/" + name
        require(manifest["files"].get(full) == pin["sha256"], "Profile not in immutable closure")
        data = pinned(root, full, pin["sha256"])
        require(len(data) == pin["size"], "Profile size differs")
        files.append((name, data))
    renderer.verify_profile(at(root, base), renderer.Candidate(tuple(sorted(files)), raw))
    return hashlib.sha256(raw).hexdigest()


def selection(root: Path, report: bytes):
    manifest, digest, qualification = artifacts(root)
    parsed = load("m4-j715-audio-profile.py").IdReport.parse(report)
    receipt_sha = profile(root, manifest, parsed.speaker_id)
    require(type(qualification["speaker_id"]) is int and
            qualification["speaker_id"] == parsed.speaker_id and
            qualification["profile_receipt_sha256"] == receipt_sha, "Wrong exact-ID qualification")
    return {**manifest, "artifact_manifest_sha256": digest, "speaker_id": parsed.speaker_id,
            "profile_receipt_sha256": receipt_sha, "id_report": report.decode(),
            "id_report_sha256": hashlib.sha256(report).hexdigest(),
            "boot": at(root, "/proc/sys/kernel/random/boot_id").read_text().strip()}


def selected(root: Path, manifest) -> None:
    """Reconstruct the same immutable selection, not a caller-supplied authorization."""
    expected = selection(root, manifest["id_report"].encode())
    require(manifest == expected, "Selected generation differs")


def main(argv: list[str] | None = None, root: Path = Path("/")) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-registration", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.require_registration:
            ready = load("m4-j715-audio-ready.py")
            ready.payload(root, trusted_json(root, SELECTED)[0])
            return 0
        # With no native card, a hold or absent qualification never blocks USB,
        # Bluetooth or other existing session audio. A raw native card fails shut.
        if not at(root, "/proc/asound/AppleJ715").exists():
            return 0
        require(not admission(trusted_json(root, SELECTED)[0])[1],
                "First qualification cannot admit an ordinary session")
        ready = load("m4-j715-audio-ready.py")
        return ready.main(["gate", "--manifest", SELECTED, "--ready", READY], root=root)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"J715 session refused: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
