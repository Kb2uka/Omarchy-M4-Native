#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Record owner-observed J715 hardware qualification and install the exact-ID profile.

Converts a verified first-test manifest to the qualified form: writes
qualification.json bound to the payload identity and the fresh speaker-ID
report, then installs the pinned profile members at their system paths.
Existing identical files are kept; any other existing file refuses before
mutation. Any later failure restores the manifest and removes created files.
Protection, calibration, gains, units and module bytes are never modified.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location("_j715_session_qualify",
                                              Path(__file__).with_name("m4-j715-audio-session.py"))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)

QUALIFICATION = s.BASE + "/qualification.json"
EVIDENCE_KEYS = ("run", "result_sha256", "owner_confirmation", "scope")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic(path: Path, data: bytes) -> None:
    fd, name = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    except BaseException:
        if os.path.lexists(name):
            os.unlink(name)
        raise


def qualify(root: Path, report: bytes, evidence: dict) -> dict:
    s.require(set(evidence) == set(EVIDENCE_KEYS) and all(
        type(evidence[k]) is str and evidence[k] for k in EVIDENCE_KEYS), "Incomplete owner evidence")
    manifest, _ = s.trusted_json(root, s.MANIFEST)
    s.require(manifest["qualification"] is None and
              manifest.get("first_qualification_authorization") == s.FIRST_AUTHORIZATION,
              "Manifest is not in first-test form")
    s.require(not os.path.lexists(s.at(root, QUALIFICATION)), "Qualification file already exists")
    speaker_id = s.load("m4-j715-audio-profile.py").IdReport.parse(report).speaker_id
    base = manifest["profiles"][str(speaker_id)]["root"]
    receipt_raw = s.pinned(root, base + "/profile-receipt.json",
                           manifest["files"][base + "/profile-receipt.json"])
    receipt = s.decode(receipt_raw)
    installs = []
    for name, pin in receipt["files"].items():
        data = s.pinned(root, base + "/" + name, pin["sha256"])
        dest = s.at(root, "/" + name)
        if os.path.lexists(dest):
            s.require(dest.is_file() and not dest.is_symlink() and sha(dest.read_bytes()) == pin["sha256"],
                      "Conflicting existing file: /" + name)
            continue
        installs.append((dest, data))

    qualified = json.loads(json.dumps(manifest))
    del qualified["first_qualification_authorization"]
    qualified["qualification"] = QUALIFICATION
    record = {"schema": 1, "kind": "hardware-qualification", "status": "qualified",
              "hardware_qualified": True, "board": "apple,j715", "soc": "apple,t8132",
              "speaker_id": speaker_id, "profile_receipt_sha256": sha(receipt_raw),
              "payload_sha256": s.payload_identity(qualified), "evidence": evidence}
    record_raw = (json.dumps(record, indent=2, sort_keys=True) + "\n").encode()
    qualified["files"][QUALIFICATION] = sha(record_raw)

    manifest_path = s.at(root, s.MANIFEST)
    original = manifest_path.read_bytes()
    created = []
    try:
        with open(s.at(root, QUALIFICATION), "xb") as stream:
            stream.write(record_raw)
        os.chmod(s.at(root, QUALIFICATION), 0o644)
        created.append(s.at(root, QUALIFICATION))
        atomic(manifest_path, (json.dumps(qualified, indent=2, sort_keys=True) + "\n").encode())
        s.artifacts(root)
        s.selection(root, report)
        for dest, data in installs:
            dest.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            with open(dest, "xb") as stream:
                stream.write(data)
            os.chmod(dest, 0o644)
            created.append(dest)
    except BaseException:
        atomic(manifest_path, original)
        for path in reversed(created):
            if os.path.lexists(path):
                os.unlink(path)
        raise
    return {"status": "qualified", "speaker_id": speaker_id,
            "payload_sha256": record["payload_sha256"], "created": [str(p) for p in created]}


def main(argv: list[str] | None = None, root: Path = Path("/"), helper=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, type=Path,
                        help="JSON with run, result_sha256, owner_confirmation and scope")
    args = parser.parse_args(argv)
    try:
        s.require(os.geteuid() == 0, "Root required")
        evidence = json.loads(args.evidence.read_bytes())
        report = helper() if helper else subprocess.run(
            [shutil.which("python3") or "/usr/bin/python3", "-B", str(s.at(root, s.HELPER))],
            capture_output=True, check=True, timeout=10, env=s.command_environment()).stdout
        print(json.dumps(qualify(root, report, evidence), indent=1))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(json.dumps({"status": "not-qualified", "error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
