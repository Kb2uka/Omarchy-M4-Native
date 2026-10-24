#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Render an offline, inactive J715 candidate from exact source and GPIO report."""
import argparse
import json
import math
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

from j715_sources import (
    SourceFile,
    SourceLock,
    digest,
    local_sources,
    require,
    unique_pairs,
)

ROOT: Final = Path(__file__).resolve().parents[1]
POLICY: Final = "audio/j715/policy/"
SHARE: Final = "usr/share/asahi-audio/j715/"
IMPORTS: Final = ("tools/m4-j715-speaker-id.py", "tests/test_m4_j715_speaker_id.py")


@dataclass(frozen=True, slots=True)
class IdReport:
    speaker_id: int
    raw: bytes

    @classmethod
    def parse(cls, raw: bytes) -> "IdReport":
        """Accept only the donor helper's complete successful report."""
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        require(type(value) is dict and
                set(value) == {"board", "chip", "pins", "speaker_id"}, "Bad ID report schema")
        require(value["board"] is True, "Wrong board in ID report")
        require(type(value["chip"]) is str and
                re.fullmatch(r"gpiochip[0-9]+", value["chip"]) is not None, "Bad GPIO chip")
        pins = value["pins"]
        require(type(pins) is dict and set(pins) == {"4", "13"}, "Bad ID pins")
        require(all(type(pins[p]) is int and pins[p] in (0, 1) for p in ("4", "13")),
                "Bad pin values")
        speaker_id = value["speaker_id"]
        require(type(speaker_id) is int and speaker_id in (0, 3) and
                speaker_id == pins["4"] | pins["13"] << 1, "Unsupported or inconsistent speaker ID")
        return cls(speaker_id, raw)


def wav_l1(data: bytes) -> float:
    """Parse RIFF chunks, requiring finite mono float32 samples at 48 kHz."""
    require(len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE",
            "Not a RIFF WAVE filter")
    require(struct.unpack_from("<I", data, 4)[0] == len(data) - 8, "Bad RIFF size")
    chunks = {}
    offset = 12
    while offset < len(data):
        require(offset + 8 <= len(data), "Truncated WAV chunk")
        tag, size = struct.unpack_from("<4sI", data, offset)
        offset += 8
        require(tag not in chunks and offset + size <= len(data), "Bad WAV chunk")
        chunks[tag] = data[offset:offset + size]
        offset += size + size % 2
    require(offset == len(data) and b"fmt " in chunks and b"data" in chunks, "Missing WAV chunks")
    fmt, samples = chunks[b"fmt "], chunks[b"data"]
    require(len(fmt) in (16, 18) and
            struct.unpack_from("<HHIIHH", fmt) == (3, 1, 48000, 192000, 4, 32) and
            (len(fmt) == 16 or fmt[16:] == b"\0\0"), "Wrong WAV format")
    require(len(samples) > 0 and len(samples) % 4 == 0, "Invalid WAV samples")
    taps = tuple(x[0] for x in struct.iter_unpack("<f", samples))
    require(all(math.isfinite(x) for x in taps), "Nonfinite FIR")
    return math.fsum(abs(x) for x in taps)


@dataclass(frozen=True, slots=True)
class Candidate:
    files: tuple[tuple[str, bytes], ...]
    receipt: bytes


def candidate(sources: tuple[SourceFile, ...], report: IdReport, lock: SourceLock) -> Candidate:
    """Consume verified frozen donor bytes; alter only the selected filenames and ID."""
    donor = {entry.pin.path: entry.data for entry in sources}
    spec_raw = (ROOT / "audio/j715/profile-contract.json").read_bytes()
    spec = json.loads(spec_raw, object_pairs_hook=unique_pairs)
    require(spec["schema"] == 1 and spec["board"] == "apple,j715" and
            spec["speaker_ids"] == [0, 3] and spec["graph_gain"] == 0.336 and
            spec["maximum_gain_times_l1"] == 0.976 and spec["lanes"] == 6 and
            all(spec[k] is False for k in ("activated", "profile_accepted", "hardware_qualified")),
            "Unsupported integration profile contract")
    graph = json.loads(donor[POLICY + SHARE + "graph.json"], object_pairs_hook=unique_pairs)
    contract = json.loads(donor[POLICY + SHARE + "contract.json"], object_pairs_hook=unique_pairs)
    require(contract["speaker_id"] == 3 and contract["graph_gain"] == spec["graph_gain"] and
            contract["activated"] is False and contract["profile_accepted"] is False,
            "Unexpected donor candidate contract")
    require([lane["slot"] for lane in contract["lane_order"]] == list(range(6)),
            "Invalid lane order")
    nodes = graph["filter.graph"]["nodes"]
    require(len(nodes) == 8, "Unexpected graph processing")
    for lane, node in enumerate(nodes[2:]):
        old = f"j715-spk3-static-minimal-lane{lane}-48000.wav"
        require(node == {"type": "builtin", "label": "convolver", "name": f"lane{lane}",
                         "config": {"filename": "/" + SHARE + old, "channel": 0,
                                    "gain": spec["graph_gain"], "blocksize": 128}},
                "Unexpected convolver or lane")
        node["config"]["filename"] = "/" + SHARE + old.replace("spk3", f"spk{report.speaker_id}")
    contract["speaker_id"] = report.speaker_id
    files = {name.removeprefix(POLICY): data for name, data in donor.items()
             if name.startswith(POLICY)}
    manifest = json.loads(donor["audio/j715/owner-fir/manifest.json"],
                          object_pairs_hook=unique_pairs)
    require(manifest["schema"] == 1 and manifest["board"] == spec["board"], "Bad FIR manifest")
    selected = manifest["speakers"][str(report.speaker_id)]["outputs"]
    require(set(selected) == {f"j715-spk{report.speaker_id}-static-minimal-lane{i}-48000.wav"
                              for i in range(6)}, "Incomplete FIR lanes")
    largest = 0.0
    for name, pin in selected.items():
        data = donor["audio/j715/filters/" + name]
        require(len(data) == pin["size"] and digest(data) == pin["sha256"], "FIR identity differs")
        largest = max(largest, wav_l1(data))
        files[SHARE + name] = data
    require(spec["graph_gain"] * largest <= spec["maximum_gain_times_l1"], "Excessive FIR L1 gain")
    for name, value in (("graph.json", graph), ("contract.json", contract)):
        files[SHARE + name] = (json.dumps(value, indent=2) + "\n").encode()
    pins = {pin.path: pin for pin in lock.files}
    for name in IMPORTS:
        require((ROOT / name).read_bytes() == donor[name], "Imported helper/test differs")
    receipt = {
        "schema": 1, "activated": False, "profile_accepted": False, "hardware_qualified": False,
        "authorization": "offline-candidate-only", "speaker_id": report.speaker_id,
        "id_report_sha256": digest(report.raw), "source_commit": lock.commit,
        "source_lock_sha256": digest(lock.raw), "profile_contract_sha256": digest(spec_raw),
        "renderer_sha256": digest(Path(__file__).read_bytes()),
        "imports": [asdict(pins[name]) for name in IMPORTS],
        "selected_max_finite_fir_l1": largest,
        "files": {name: {"size": len(data), "sha256": digest(data)}
                  for name, data in sorted(files.items())},
    }
    return Candidate(tuple(sorted(files.items())),
                     (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())


def verify_profile(output: Path, expected: Candidate) -> None:
    """Check exact expected payload and receipt, never trusting cached hashes."""
    require(output.is_dir() and not output.is_symlink() and
            not any(p.is_symlink() for p in output.parents), "Invalid profile directory")
    actual = set()
    for path in output.rglob("*"):
        require(not path.is_symlink(), "Profile contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(output).as_posix())
        else:
            require(path.is_dir(), "Profile contains a special file")
    require(actual == {name for name, _ in expected.files} | {"profile-receipt.json"},
            "Profile closure differs")
    for name, data in (*expected.files, ("profile-receipt.json", expected.receipt)):
        require((output / name).read_bytes() == data, "Profile bytes or stale receipt: " + name)


def render(source: Path, id_report: Path, output: Path) -> Path:
    """Create a new offline payload without installation, GPIO or audio access."""
    output = output.absolute()
    require(not output.exists() and not output.is_symlink() and output.parent.is_dir(),
            "Output must be a new directory with an existing parent")
    require(not any(p.is_symlink() for p in output.parents), "Symlink output ancestor")
    require(not any(output.resolve().is_relative_to(p.resolve()) or
                    p.resolve().is_relative_to(output.resolve()) for p in (ROOT, source)),
            "Output overlaps source")
    report = IdReport.parse(id_report.read_bytes())
    lock = SourceLock.read(ROOT / "audio/j715/source-lock.json")
    expected = candidate(local_sources(source, lock), report, lock)
    output.mkdir()
    try:
        for name, data in (*expected.files, ("profile-receipt.json", expected.receipt)):
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as stream:
                stream.write(data)
    except OSError:
        shutil.rmtree(output)
        raise
    return output / "profile-receipt.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--id-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        receipt = render(args.source, args.id_report, args.output)
    except (OSError, ValueError, TypeError, KeyError, struct.error,
            subprocess.CalledProcessError) as error:
        print(f"J715 profile refused: {error}", file=sys.stderr)
        return 1
    print(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
