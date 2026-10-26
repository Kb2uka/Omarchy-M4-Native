# SPDX-License-Identifier: MIT
"""Offline rendering and refusal tests; fixtures never qualify physical playback."""
import copy
import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import test_m4_j715_speaker_id as gpio_tests
from j715_sources import SourceError, SourceLock, digest, local_sources

SPEC = importlib.util.spec_from_file_location("j715_profile", ROOT / "tools/m4-j715-audio-profile.py")
PROFILE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PROFILE
SPEC.loader.exec_module(PROFILE)
DONOR = Path(os.environ.get("J715_DONOR_SOURCE", ROOT.parent / "donor-source"))


def report_bytes(speaker_id: int) -> bytes:
    return json.dumps({"board": True, "chip": "gpiochip4",
                       "pins": {"4": speaker_id & 1, "13": speaker_id >> 1},
                       "speaker_id": speaker_id}).encode()


def synthetic_wav(sample: float) -> bytes:
    fmt = struct.pack("<HHIIHH", 3, 1, 48000, 192000, 4, 32)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data\x04\0\0\0" + struct.pack("<f", sample)
    return b"RIFF" + struct.pack("<I", len(chunks) + 4) + b"WAVE" + chunks


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lock = SourceLock.read(ROOT / "audio/j715/source-lock.json")
        cls.sources = local_sources(DONOR, cls.lock)

    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.scratch = Path(scratch.name)
        self.report = self.scratch / "id.json"
        self.report.write_bytes(report_bytes(3))
        self.output = self.scratch / "candidate"

    def test_exact_payload_when_each_id_is_rendered_through_cli(self):
        # Given: two distinct exact helper reports and a locked, untouched donor.
        environment = os.environ.copy()
        for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
            self.assertTrue(environment.get(key), key)
        environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
        originals = {item.pin.path: item.data for item in self.sources}
        for speaker_id in (0, 3):
            with self.subTest(speaker_id=speaker_id):
                self.report.write_bytes(report_bytes(speaker_id))
                output = self.scratch / f"id{speaker_id}"
                # When: drive the actual supported CLI, with no GPIO or activation.
                result = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "tools/m4-j715-audio-profile.py"),
                     "--source", str(DONOR), "--id-report", str(self.report), "--output", str(output)],
                    env=environment, capture_output=True, text=True, check=False)
                # Then: every routing field survives; exactly the six names and ID change.
                self.assertEqual(result.returncode, 0, result.stderr)
                expected_graph = json.loads(originals[PROFILE.POLICY + PROFILE.SHARE + "graph.json"])
                for node in expected_graph["filter.graph"]["nodes"][2:]:
                    node["config"]["filename"] = node["config"]["filename"].replace("spk3", f"spk{speaker_id}")
                expected_contract = json.loads(originals[PROFILE.POLICY + PROFILE.SHARE + "contract.json"])
                expected_contract["speaker_id"] = speaker_id
                self.assertEqual(json.loads((output / PROFILE.SHARE / "graph.json").read_bytes()), expected_graph)
                self.assertEqual(json.loads((output / PROFILE.SHARE / "contract.json").read_bytes()), expected_contract)
                for name, data in originals.items():
                    if name.startswith(PROFILE.POLICY) and not name.endswith(("graph.json", "contract.json")):
                        self.assertEqual((output / name.removeprefix(PROFILE.POLICY)).read_bytes(), data)
                for lane in range(6):
                    name = f"j715-spk{speaker_id}-static-minimal-lane{lane}-48000.wav"
                    self.assertEqual((output / PROFILE.SHARE / name).read_bytes(),
                                     originals["audio/j715/filters/" + name])
                receipt = json.loads((output / "profile-receipt.json").read_bytes())
                self.assertEqual(receipt["id_report_sha256"], digest(self.report.read_bytes()))
                self.assertEqual(receipt["speaker_id"], speaker_id)
                self.assertFalse(receipt["hardware_qualified"])

    def test_refusal_when_report_schema_or_pins_are_invalid(self):
        # Given: malformed, ambiguous and inconsistent reports.
        good = json.loads(report_bytes(3))
        bad = [b"null", b"[]", b"{", report_bytes(1), report_bytes(2)]
        for key, value in (("board", False), ("board", "apple,j715"), ("speaker_id", True),
                           ("speaker_id", "3"), ("speaker_id", 0), ("chip", "gpiochip4/other"),
                           ("pins", {"4": 1, "13": True}), ("pins", {"4": 1})):
            variant = copy.deepcopy(good)
            variant[key] = value
            bad.append(json.dumps(variant).encode())
        bad.extend([report_bytes(3).replace(b'"board": true', b'"board": false, "board": true'),
                    json.dumps({**good, "boot_id": "unbound-cache"}).encode()])
        for raw in bad:
            with self.subTest(raw=raw):
                self.report.write_bytes(raw)
                # When / Then: refusal precedes any output creation.
                with self.assertRaises((ValueError, TypeError)):
                    PROFILE.render(DONOR, self.report, self.output)
                self.assertFalse(self.output.exists())

    def test_refusal_when_candidate_payload_or_receipt_is_changed(self):
        # Given: a valid candidate and its independently rebuilt expected payload.
        PROFILE.render(DONOR, self.report, self.output)
        expected = PROFILE.candidate(self.sources, PROFILE.IdReport.parse(self.report.read_bytes()), self.lock)
        PROFILE.verify_profile(self.output, expected)
        changes = [
            (PROFILE.SHARE + "graph.json", lambda x: x["filter.graph"]["nodes"][2]["config"].update(gain=0.60)),
            (PROFILE.SHARE + "graph.json", lambda x: x["filter.graph"]["nodes"].pop()),
            (PROFILE.SHARE + "graph.json", lambda x: x["filter.graph"]["nodes"].append({"label": "lowshelf"})),
            (PROFILE.SHARE + "graph.json", lambda x: x["filter.graph"]["outputs"].reverse()),
            (PROFILE.SHARE + "graph.json", lambda x: x["filter.graph"]["nodes"][2]["config"].update(filename="/wrong-ID.wav")),
            (PROFILE.SHARE + "contract.json", lambda x: x.update(speaker_id=0)),
            ("profile-receipt.json", lambda x: x.update(source_commit="0" * 40)),
            ("profile-receipt.json", lambda x: x.update(id_report_sha256="0" * 64)),
        ]
        for name, change in changes:
            with self.subTest(name=name, change=change):
                path = self.output / name
                original = path.read_bytes()
                value = json.loads(original)
                change(value)
                path.write_text(json.dumps(value))
                # When / Then: actual bytes, not the mutable receipt's claims, are checked.
                with self.assertRaises(SourceError):
                    PROFILE.verify_profile(self.output, expected)
                path.write_bytes(original)
        lane = self.output / PROFILE.SHARE / "j715-spk3-static-minimal-lane0-48000.wav"
        lane.write_bytes(b"changed FIR")
        with self.assertRaises(SourceError):
            PROFILE.verify_profile(self.output, expected)

    def test_refusal_when_synthetic_fir_is_invalid_or_exceeds_bound(self):
        # Given: synthetic FIR fixtures with matching synthetic manifest hashes.
        for sample in (4.0, float("nan"), float("inf")):
            with self.subTest(sample=sample):
                wav = synthetic_wav(sample)
                manifest_name = "audio/j715/owner-fir/manifest.json"
                manifest = json.loads(next(x.data for x in self.sources if x.pin.path == manifest_name))
                name = "j715-spk3-static-minimal-lane0-48000.wav"
                manifest["speakers"]["3"]["outputs"][name] = {"size": len(wav), "sha256": digest(wav)}
                sources = tuple(replace(x, data=json.dumps(manifest).encode()) if x.pin.path == manifest_name
                                else replace(x, data=wav) if x.pin.path == "audio/j715/filters/" + name
                                else x for x in self.sources)
                # When / Then: no synthetic success or qualification is emitted.
                with self.assertRaises(SourceError):
                    PROFILE.candidate(sources, PROFILE.IdReport.parse(report_bytes(3)), self.lock)

    def test_refusal_when_wav_format_or_chunks_are_wrong(self):
        # Given: malformed RIFF and wrong sample encoding/channel/rate.
        wav = synthetic_wav(0.25)
        bad = [b"", wav[:-1], wav + b"x", wav.replace(b"data", b"junk")]
        for offset, value in ((20, 1), (22, 2), (24, 44100), (34, 16)):
            variant = bytearray(wav)
            struct.pack_into("<H", variant, offset, value)
            bad.append(bytes(variant))
        for data in bad:
            # When / Then: parsing refuses before the filter is consumed.
            with self.subTest(data=data), self.assertRaises(SourceError):
                PROFILE.wav_l1(data)

    def test_assembly_refused_when_selected_source_payload_is_corrupt(self):
        # Given: isolated corruptions of the bytes consumed by pure assembly.
        graph_path = PROFILE.POLICY + PROFILE.SHARE + "graph.json"
        contract_path = PROFILE.POLICY + PROFILE.SHARE + "contract.json"
        manifest_path = "audio/j715/owner-fir/manifest.json"
        mutations = [
            (graph_path, lambda x: x["filter.graph"]["nodes"].pop()),
            (graph_path, lambda x: x["filter.graph"]["nodes"].append({"label": "lowshelf"})),
            (graph_path, lambda x: x["filter.graph"]["nodes"][2]["config"].update(gain=0.60)),
            (graph_path, lambda x: x["filter.graph"]["nodes"][2]["config"].update(filename="/wrong-ID.wav")),
            (contract_path, lambda x: x.update(speaker_id=0)),
            (contract_path, lambda x: x["lane_order"].reverse()),
            (manifest_path, lambda x: x["speakers"]["3"]["outputs"].pop(
                "j715-spk3-static-minimal-lane5-48000.wav")),
        ]
        for name, mutate in mutations:
            with self.subTest(path=name, mutation=mutate):
                value = json.loads(next(x.data for x in self.sources if x.pin.path == name))
                mutate(value)
                sources = tuple(replace(x, data=json.dumps(value).encode())
                                if x.pin.path == name else x for x in self.sources)
                # When / Then: assembly itself refuses, before any output exists.
                with self.assertRaises(SourceError):
                    PROFILE.candidate(sources, PROFILE.IdReport.parse(report_bytes(3)), self.lock)
        for speaker_id in (0, 3):
            with self.subTest(changed_fir=speaker_id):
                name = f"audio/j715/filters/j715-spk{speaker_id}-static-minimal-lane0-48000.wav"
                sources = tuple(replace(x, data=synthetic_wav(0.25))
                                if x.pin.path == name else x for x in self.sources)
                with self.assertRaises(SourceError):
                    PROFILE.candidate(sources, PROFILE.IdReport.parse(report_bytes(speaker_id)), self.lock)

    def test_closure_refused_when_lane_is_absent_extra_or_linked(self):
        # Given: a rendered candidate with a known independent closure.
        PROFILE.render(DONOR, self.report, self.output)
        expected = PROFILE.candidate(self.sources, PROFILE.IdReport.parse(self.report.read_bytes()), self.lock)
        lane = self.output / PROFILE.SHARE / "j715-spk3-static-minimal-lane5-48000.wav"
        data = lane.read_bytes()
        # When / Then: a missing lane cannot be accepted via its retained receipt.
        lane.unlink()
        with self.assertRaises(SourceError):
            PROFILE.verify_profile(self.output, expected)
        lane.write_bytes(data)
        extra = self.output / "unrecorded"
        extra.write_bytes(b"extra")
        with self.assertRaises(SourceError):
            PROFILE.verify_profile(self.output, expected)
        extra.unlink()
        lane.unlink()
        lane.symlink_to("j715-spk3-static-minimal-lane0-48000.wav")
        with self.assertRaises(SourceError):
            PROFILE.verify_profile(self.output, expected)

    def test_receipt_refused_when_report_is_rebound_to_another_id(self):
        # Given: a candidate from ID 3 and a separate current ID-0 report.
        PROFILE.render(DONOR, self.report, self.output)
        current = PROFILE.IdReport.parse(report_bytes(0))
        expected = PROFILE.candidate(self.sources, current, self.lock)
        # When / Then: an old candidate cannot satisfy the other report's identity.
        with self.assertRaises(SourceError):
            PROFILE.verify_profile(self.output, expected)

    def test_cli_refused_when_arbitrary_activation_id_is_supplied(self):
        # Given: the offline CLI exposes no direct ID or activation switch.
        environment = os.environ.copy()
        for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
            self.assertTrue(environment.get(key), key)
        environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
        # When: an ID override attempts to bypass the report parser.
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/m4-j715-audio-profile.py"),
             "--source", str(DONOR), "--id-report", str(self.report),
             "--output", str(self.output), "--speaker-id", "0", "--activate"],
            env=environment, capture_output=True, text=True, check=False)
        # Then: argparse rejects the unsupported operation, with no payload.
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.output.exists())

    def test_output_preserved_when_existing_or_overlapping(self):
        # Given: an existing directory and protected source paths.
        self.output.mkdir()
        marker = self.output / "owner-file"
        marker.write_bytes(b"keep")
        # When / Then: rendering never reuses output or writes into source.
        for path in (self.output, ROOT / "candidate", DONOR / "candidate"):
            with self.subTest(path=path), self.assertRaises(SourceError):
                PROFILE.render(DONOR, self.report, path)
        self.assertEqual(marker.read_bytes(), b"keep")

    def test_source_refused_when_checkout_or_locked_fir_bytes_differ(self):
        # Given: a detached synthetic materialization, not an authorized donor checkout.
        source = self.scratch / "unverified"
        source.mkdir()
        # When / Then: no cached receipt or directory name substitutes for Git identity.
        with self.assertRaises(subprocess.CalledProcessError):
            PROFILE.render(source, self.report, self.output)
        pin = next(p for p in self.lock.files if p.path.endswith("j715-spk3-static-minimal-lane0-48000.wav"))
        with self.assertRaises(SourceError):
            pin.verify(b"changed")
        self.assertFalse(self.output.exists())


class PermissionFailureTests(unittest.TestCase):
    def test_refusal_when_gpio_open_is_denied(self):
        # Given: a synthetic J715 root with inaccessible GPIO chips.
        fixture = gpio_tests.SpeakerId()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        kernel = gpio_tests.Kernel(fixture.labels, 3)
        # When: permission fails before a GPIO request.
        with mock.patch.object(kernel, "open", side_effect=PermissionError(13, "fixture denied")):
            code, report = fixture.run_tool(kernel)
        # Then: no ID is selected.
        self.assertEqual((code, report, kernel.requests), (1, None, []))


if __name__ == "__main__":
    unittest.main()
