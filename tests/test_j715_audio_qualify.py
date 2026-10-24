# SPDX-License-Identifier: MIT
"""Owner qualification converts first-test evidence once, exactly, or not at all."""
import importlib.util
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

from test_j715_first_qualification import AuthorizedFixture
from test_m4_j715_audio_ready import ROOT, Fixture, s

spec = importlib.util.spec_from_file_location("j715_qualify_tests", ROOT / "tools/m4-j715-audio-qualify.py")
q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(q)
EVIDENCE = {"run": "synthetic", "result_sha256": "0" * 64,
            "owner_confirmation": "synthetic fixture; no hardware observation", "scope": "fixture"}


class QualifyTests(AuthorizedFixture):
    def setUp(self):
        super().setUp()
        s.at(self.root, q.QUALIFICATION).unlink()

    def receipt(self):
        base = s.BASE + "/profiles/" + str(self.speaker_id)
        return json.loads(s.at(self.root, base + "/profile-receipt.json").read_bytes())

    def test_first_test_manifest_becomes_bound_qualification(self):
        result = q.qualify(self.root, self.report, EVIDENCE)
        manifest, _, evidence = s.artifacts(self.root)
        self.assertNotIn("first_qualification_authorization", manifest)
        self.assertEqual(manifest["qualification"], q.QUALIFICATION)
        self.assertTrue(evidence["hardware_qualified"])
        self.assertEqual(evidence["evidence"], EVIDENCE)
        self.assertEqual(evidence["payload_sha256"], s.payload_identity(manifest))
        self.assertEqual(s.selection(self.root, self.report)["speaker_id"], self.speaker_id)
        for name, pin in self.receipt()["files"].items():
            self.assertEqual(self.sha(s.at(self.root, "/" + name).read_bytes()), pin["sha256"])
        self.assertEqual(result["speaker_id"], self.speaker_id)

    def test_second_run_refuses_without_change(self):
        q.qualify(self.root, self.report, EVIDENCE)
        before = s.at(self.root, s.MANIFEST).read_bytes()
        with self.assertRaises(ValueError):
            q.qualify(self.root, self.report, EVIDENCE)
        self.assertEqual(s.at(self.root, s.MANIFEST).read_bytes(), before)

    def test_conflicting_system_file_refuses_before_mutation(self):
        name = next(iter(self.receipt()["files"]))
        self.write("/" + name, b"owner customisation")
        before = s.at(self.root, s.MANIFEST).read_bytes()
        with self.assertRaises(ValueError):
            q.qualify(self.root, self.report, EVIDENCE)
        self.assertEqual(s.at(self.root, s.MANIFEST).read_bytes(), before)
        self.assertFalse(s.at(self.root, q.QUALIFICATION).exists())

    def test_identical_existing_file_is_kept(self):
        name = next(iter(self.receipt()["files"]))
        base = s.BASE + "/profiles/" + str(self.speaker_id)
        self.write("/" + name, s.at(self.root, base + "/" + name).read_bytes())
        result = q.qualify(self.root, self.report, EVIDENCE)
        self.assertNotIn(str(s.at(self.root, "/" + name)), result["created"])

    def test_late_failure_restores_manifest_and_removes_files(self):
        before = s.at(self.root, s.MANIFEST).read_bytes()
        with patch.object(q.s, "selection", side_effect=ValueError("synthetic")), \
                self.assertRaises(ValueError):
            q.qualify(self.root, self.report, EVIDENCE)
        self.assertEqual(s.at(self.root, s.MANIFEST).read_bytes(), before)
        self.assertFalse(s.at(self.root, q.QUALIFICATION).exists())
        for name in self.receipt()["files"]:
            self.assertFalse(s.at(self.root, "/" + name).exists())

    def test_incomplete_evidence_refuses(self):
        for bad in ({}, {**EVIDENCE, "owner_confirmation": ""}, {**EVIDENCE, "extra": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                q.qualify(self.root, self.report, bad)

    def test_cli_reports_refusal_without_traceback(self):
        path = self.root / "evidence.json"
        path.write_text(json.dumps({**EVIDENCE, "scope": ""}))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(q.main(["--evidence", str(path)], self.root, helper=lambda: self.report), 1)
        self.assertEqual(json.loads(err.getvalue())["status"], "not-qualified")


class AlreadyQualified(Fixture):
    def test_qualified_manifest_refuses(self):
        with self.assertRaises(ValueError):
            q.qualify(self.root, self.report, EVIDENCE)


class WaitTests(Fixture):
    def setUp(self):
        super().setUp()
        self.slept = []
        self.props = {("m4-j715-audio-register.service", "UnitFileState"): "enabled"}

    def prop(self, unit, name):
        return self.props.get((unit, name), "active")

    def run_wait(self, seconds=2):
        s.wait_ready(self.root, seconds, sleep=self.slept.append, prop=self.prop)

    def test_waits_bounded_while_protection_starts(self):
        self.run_wait()
        self.assertEqual(len(self.slept), 4)

    def test_returns_at_once_when_ready_held_failed_or_disabled(self):
        cases = {
            "ready": lambda: self.write(s.READY, "{}"),
            "hold": lambda: self.write(s.STATE + "/hold", "held"),
            "failed": lambda: self.props.update({(s.SERVICE, "ActiveState"): "failed"}),
            "disabled": lambda: self.props.update(
                {("m4-j715-audio-register.service", "UnitFileState"): "disabled"}),
        }
        for case, arrange in cases.items():
            with self.subTest(case=case):
                self.setUp()
                arrange()
                self.run_wait()
                self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()
