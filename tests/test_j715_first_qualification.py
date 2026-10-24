# SPDX-License-Identifier: MIT
"""First-test authorization is permission, never completed hardware evidence."""
import copy
import hashlib
import json
import os
import unittest

from test_m4_j715_audio_ready import Fixture, s


def repin(fixture: Fixture, path: str, data: bytes) -> None:
    """Publish synthetic fixture bytes with their updated immutable hash."""
    fixture.write(path, data)
    fixture.artifact["files"][path] = fixture.sha(data)
    fixture.write(s.MANIFEST, json.dumps(fixture.artifact))


def reselect(fixture: Fixture, *, registered: bool = True) -> None:
    """Bind the synthetic selected generation and optional registration record."""
    fixture.manifest = s.selection(fixture.root, fixture.report)
    fixture.write(s.SELECTED, json.dumps(fixture.manifest))
    if registered:
        fixture.write("/run/omarchy/j715-audio-registration/registered.json", json.dumps({
            "boot": "fixture-boot",
            "selection_sha256": fixture.sha(json.dumps(fixture.manifest, sort_keys=True).encode())}))


def authorize(fixture: Fixture, *, registered: bool = True) -> None:
    """Replace only fixture admission evidence, keeping hardware state false."""
    old = fixture.artifact["qualification"]
    del fixture.artifact["files"][old]
    fixture.artifact["qualification"] = None
    fixture.artifact["first_qualification_authorization"] = s.FIRST_AUTHORIZATION
    protocol = b'{"scope":"synthetic isolated test; no hardware operations"}'
    repin(fixture, s.FIRST_PROTOCOL, protocol)
    fixture.authorization = {
        "schema": 1, "kind": "first-qualification-authorization",
        "status": "authorized", "scope": "private-protected-test",
        "hardware_qualified": False, "board": "apple,j715", "soc": "apple,t8132",
        "speaker_id": fixture.speaker_id,
        "profile_receipt_sha256": fixture.manifest["profile_receipt_sha256"],
        "boot": "fixture-boot", "expires_monotonic_ns": fixture.now + 300_000_000_000,
        "protocol": s.FIRST_PROTOCOL, "payload_sha256": s.payload_identity(fixture.artifact),
    }
    repin(fixture, s.FIRST_AUTHORIZATION, json.dumps(fixture.authorization).encode())
    reselect(fixture, registered=registered)


class AuthorizedFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.qualified_artifact = copy.deepcopy(self.artifact)
        authorize(self)


class FirstAuthorizationTests(AuthorizedFixture):
    def test_exact_selection_keeps_hardware_flags_false(self):
        _, _, evidence = s.artifacts(self.root)
        self.assertFalse(evidence["hardware_qualified"])
        self.assertEqual(self.manifest["speaker_id"], 3)
        self.assertIsNone(self.manifest["qualification"])
        receipt = s.trusted_json(
            self.root, self.manifest["profiles"]["3"]["root"] + "/profile-receipt.json")[0]
        self.assertFalse(receipt["hardware_qualified"])
        self.assertFalse(receipt["activated"])

    def test_forms_are_exclusive_and_never_fall_back(self):
        variants = [
            {**self.artifact, "qualification": s.BASE + "/qualification.json"},
            {k: v for k, v in self.artifact.items() if k != "qualification"},
            {k: v for k, v in self.artifact.items() if k != "first_qualification_authorization"},
            {**self.artifact, "first_qualification_authorization": None},
            {**self.artifact, "qualification": ""},
        ]
        legacy = copy.deepcopy(self.qualified_artifact)
        legacy["files"][s.FIRST_AUTHORIZATION] = self.sha(
            s.at(self.root, s.FIRST_AUTHORIZATION).read_bytes())
        legacy["qualification"] = s.FIRST_AUTHORIZATION
        variants.append(legacy)
        for manifest in variants:
            self.write(s.MANIFEST, json.dumps(manifest))
            with self.subTest(manifest=manifest), self.assertRaises((ValueError, KeyError)):
                s.artifacts(self.root)

    def test_boot_deadline_kind_scope_and_types_refuse(self):
        good = copy.deepcopy(self.authorization)
        changes = [
            ("boot", "previous"), ("boot", ""), ("expires_monotonic_ns", self.now),
            ("expires_monotonic_ns", True), ("expires_monotonic_ns", str(self.now + 1)),
            ("schema", True), ("kind", "qualification"), ("status", "qualified"),
            ("scope", "ordinary-session"), ("hardware_qualified", True),
            ("hardware_qualified", 0), ("payload_sha256", "0" * 64),
            ("protocol", s.BASE + "/other.json"),
        ]
        for key, value in changes:
            authorization = {**good, key: value}
            repin(self, s.FIRST_AUTHORIZATION, json.dumps(authorization).encode())
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                s.artifacts(self.root)

    def test_exact_id_and_profile_are_bound(self):
        for key, value in (("speaker_id", 0), ("speaker_id", True),
                           ("profile_receipt_sha256", "0" * 64)):
            authorization = {**self.authorization, key: value}
            repin(self, s.FIRST_AUTHORIZATION, json.dumps(authorization).encode())
            with self.subTest(key=key), self.assertRaises(ValueError):
                s.selection(self.root, self.report)

    def test_protocol_and_payload_mutation_refuse(self):
        for path in (s.FIRST_PROTOCOL, s.PROFILE, s.HELPER):
            original = s.at(self.root, path).read_bytes()
            repin(self, path, original + b"changed")
            with self.subTest(path=path), self.assertRaises(ValueError):
                s.artifacts(self.root)
            repin(self, path, original)

    def test_evidence_and_protocol_keep_root_link_and_mode_checks(self):
        for name in (s.FIRST_AUTHORIZATION, s.FIRST_PROTOCOL):
            path = s.at(self.root, name)
            original = path.read_bytes()
            path.chmod(0o666)
            with self.assertRaises(ValueError):
                s.artifacts(self.root)
            path.chmod(0o644)
            self.uid = 1000
            with self.assertRaises(ValueError):
                s.artifacts(self.root)
            self.uid = 0
            alias = self.root / "alias"
            path.rename(alias)
            path.symlink_to(alias)
            with self.assertRaises(ValueError):
                s.artifacts(self.root)
            path.unlink()
            os.link(alias, path)
            with self.assertRaises(ValueError):
                s.artifacts(self.root)
            path.unlink()
            alias.unlink()
            self.write(name, original)

    def test_legacy_identity_and_exact_receipt_exclusion(self):
        legacy = self.qualified_artifact
        canonical = {k: v for k, v in legacy.items() if k not in ("qualification", "files")}
        canonical["files"] = {k: v for k, v in legacy["files"].items()
                              if k != legacy["qualification"]}
        expected = hashlib.sha256(json.dumps(
            canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(s.payload_identity(legacy), expected)
        identity = s.payload_identity(self.artifact)
        original_manifest = self.sha(s.at(self.root, s.MANIFEST).read_bytes())
        changed = {**self.authorization, "expires_monotonic_ns": self.now + 40_000_000_000}
        repin(self, s.FIRST_AUTHORIZATION, json.dumps(changed).encode())
        self.assertEqual(s.payload_identity(self.artifact), identity)
        self.assertNotEqual(self.sha(s.at(self.root, s.MANIFEST).read_bytes()), original_manifest)
        with self.assertRaises(ValueError):
            s.selected(self.root, self.manifest)
        repin(self, s.FIRST_PROTOCOL, b"different protocol")
        self.assertNotEqual(s.payload_identity(self.artifact), identity)

    def test_absent_native_card_preserves_external_session(self):
        import shutil
        shutil.rmtree(self.root / "proc/asound/AppleJ715")
        self.write(s.STATE + "/hold", "held")
        self.assertEqual(s.main([], self.root), 0)
        self.assertEqual(self.service.call_count, 0)


class IdZeroFirstAuthorization(AuthorizedFixture):
    speaker_id = 0

    def test_zero_authorization_is_not_id_three_permission(self):
        self.assertEqual(s.selection(self.root, self.report)["speaker_id"], 0)
        authorization = {**self.authorization, "speaker_id": 3}
        repin(self, s.FIRST_AUTHORIZATION, json.dumps(authorization).encode())
        with self.assertRaises(ValueError):
            s.selection(self.root, self.report)


if __name__ == "__main__":
    unittest.main()
