# SPDX-License-Identifier: MIT
"""Synthetic structural tests never constitute a qualified hardware profile."""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import j715_display_profile as PROFILE

FIXTURE = ROOT / "tests/fixtures/j715-display-synthetic.json"


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def environment():
    result = os.environ.copy()
    for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
        if not result.get(key):
            raise RuntimeError("Test requires isolated " + key)
    result.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
    return result


class ProfileContract(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "profile.json"
        self.source = json.loads(FIXTURE.read_bytes())

    def test_structural_fixture_retains_nonproduction_identity(self):
        profile = PROFILE.parse(FIXTURE)
        self.assertTrue(profile.synthetic)
        self.assertEqual(profile.qualification, "synthetic")
        self.assertEqual(profile.initial_nits, self.source["panel"]["initial_nits"])

    def test_missing_every_required_field_is_rejected(self):
        for key in self.source:
            with self.subTest(field=key):
                candidate = copy.deepcopy(self.source)
                del candidate[key]
                self.path.write_text(json.dumps(candidate))
                with self.assertRaises(PROFILE.ProfileError):
                    PROFILE.parse(self.path)

    def test_protocol_geometry_board_and_limits_are_not_overridable(self):
        mutations = [
            (("board",), "apple,j713"), (("soc",), "apple,t8112"),
            (("stream",), 32), (("stream",), True),
            (("protocol", "endpoint"), 36), (("protocol", "service"), "other-service"),
            (("protocol", "channel"), 4), (("protocol", "native"), "other-frame"),
            (("dma", "page_shift"), 12), (("dma", "address_bits"), 48),
            (("dma", "offset_bits"), 40), (("dma", "table_levels"), 4),
            (("panel", "initial_nits"), 124), (("panel", "min_nits"), 124),
            (("panel", "max_nits"), 1 << 24), (("synthetic",), False),
            (("resources", "dart", "power"), [7]), (("resources", "dart", "size"), 4096),
            (("resources", "dart", "roles"), ["send-empty"]),
            (("resources", "mailbox", "address"), 4096),
            (("evidence", "adt", "path"), "../escape"),
            (("firmware", "sha256"), "bad"), (("base_dtb_sha256",), "bad")]
        for keys, value in mutations:
            with self.subTest(keys=keys):
                candidate = copy.deepcopy(self.source)
                node = candidate
                for key in keys[:-1]:
                    node = node[key]
                node[keys[-1]] = value
                self.path.write_text(json.dumps(candidate))
                with self.assertRaises(PROFILE.ProfileError):
                    PROFILE.parse(self.path)

    def test_unknown_field_and_duplicate_key_are_rejected(self):
        candidate = dict(self.source, force=True)
        self.path.write_text(json.dumps(candidate))
        with self.assertRaises(ValueError):
            PROFILE.parse(self.path)
        self.path.write_text(FIXTURE.read_text().replace('"schema": 1', '"schema": 1, "schema": 1'))
        with self.assertRaises(ValueError):
            PROFILE.parse(self.path)

    def test_j715_refuses_legacy_aic2_interrupt_parent(self):
        candidate = copy.deepcopy(self.source)
        candidate["interrupt_parent"]["compatible"] = "apple,aic2"
        self.path.write_text(json.dumps(candidate))
        with self.assertRaises(PROFILE.ProfileError):
            PROFILE.parse(self.path)

    def test_j715_refuses_legacy_mailbox_identity(self):
        candidate = copy.deepcopy(self.source)
        candidate["resources"]["mailbox"]["compatible"] = [
            "apple,t8122-asc-mailbox", "apple,asc-mailbox-v4"
        ]
        self.path.write_text(json.dumps(candidate))
        with self.assertRaises(PROFILE.ProfileError):
            PROFILE.parse(self.path)

    def test_synthetic_cannot_generate_production_stage(self):
        output = self.path.parent / "staging"
        result = subprocess.run([sys.executable, "-B", str(ROOT / "tools/j715_display_profile.py"),
                                 str(FIXTURE), "--stage", str(output)],
                                env=environment(), capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())

    def test_unqualified_and_missing_evidence_refuse(self):
        for status in ("unqualified", "qualified"):
            with self.subTest(status=status):
                candidate = copy.deepcopy(self.source)
                candidate.update(synthetic=False, qualification=status, base_dtb_sha256=PROFILE.BASE_DTB)
                candidate["firmware"]["identity"] = "iBoot-1.2"
                self.path.write_text(json.dumps(candidate))
                reason = ("No qualified J715 display profile" if status == "unqualified"
                          else "Evidence asset missing or escaping")
                with self.assertRaisesRegex(PROFILE.ProfileError, reason):
                    PROFILE.qualified(self.path)

    def test_substituted_evidence_refuses(self):
        candidate = copy.deepcopy(self.source)
        candidate.update(synthetic=False, qualification="qualified", base_dtb_sha256=PROFILE.BASE_DTB)
        candidate["firmware"]["identity"] = "iBoot-1.2"
        for name, record in candidate["evidence"].items():
            asset = self.path.parent / record["path"]
            asset.parent.mkdir(exist_ok=True)
            asset.write_bytes(name.encode())
            record["sha256"] = PROFILE.sha(asset.read_bytes())
        candidate["firmware"]["sha256"] = candidate["evidence"]["firmware"]["sha256"]
        self.path.write_text(json.dumps(candidate))
        PROFILE.qualified(self.path)
        for name, record in candidate["evidence"].items():
            with self.subTest(evidence=name):
                asset = self.path.parent / record["path"]
                asset.write_bytes(b"substituted")
                with self.assertRaisesRegex(PROFILE.ProfileError, "Evidence hash mismatch"):
                    PROFILE.qualified(self.path)
                asset.write_bytes(name.encode())

    def test_generic_or_marketing_firmware_identity_cannot_qualify(self):
        for identity in ("unknown", "26.5.1", "iBoot-"):
            candidate = copy.deepcopy(self.source)
            candidate.update(synthetic=False, qualification="qualified",
                             base_dtb_sha256=PROFILE.BASE_DTB)
            candidate["firmware"]["identity"] = identity
            self.path.write_text(json.dumps(candidate))
            with self.subTest(identity=identity), self.assertRaisesRegex(
                    PROFILE.ProfileError, "Exact raw boot firmware identity required"):
                PROFILE.qualified(self.path)

    def test_builders_refuse_fixture_before_creating_outputs(self):
        for script in ("tools/build-j715-backlight.sh", "display/j715/build-backlight-loader.sh"):
            with self.subTest(script=script):
                output = self.path.parent / "output"
                args = ([str(output), str(self.path.parent)] if script.startswith("tools/") else
                        [str(self.path.parent), str(output)])
                result = subprocess.run(["bash", str(ROOT / script), *args, str(FIXTURE)],
                                        env=environment(), capture_output=True, text=True, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
