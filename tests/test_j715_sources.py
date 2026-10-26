# SPDX-License-Identifier: MIT
"""Behavioral source import tests; fixture pins never qualify production bytes."""
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import j715_sources as sources

SPEC = importlib.util.spec_from_file_location("prepare_j715_sources", ROOT / "tools/prepare-j715-sources.py")
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)
DONOR = Path(os.environ.get("J715_DONOR_SOURCE", ROOT.parent / "donor-source"))


def pin(name: str, data: bytes, mode: str = "100644") -> sources.FilePin:
    return sources.FilePin(name, mode, hashlib.sha1(
        b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(), len(data), sources.digest(data))


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.output = self.base / "prepared"
        self.lock = sources.SourceLock.read(ROOT / "audio/j715/source-lock.json")

    def checkout(self):
        destination = self.base / "donor"
        shutil.copytree(DONOR, destination, symlinks=True)
        return destination

    def archive(self, entries):
        """Return a fixture archive plus its explicitly synthetic lock."""
        raw = io.BytesIO()
        pins = []
        with tarfile.open(fileobj=raw, mode="w") as archive:
            for name, data, kind in entries:
                member = tarfile.TarInfo("fixture/" + name)
                member.type = kind
                member.mode = 0o644
                if kind == tarfile.SYMTYPE:
                    member.linkname = data.decode()
                else:
                    member.size = len(data)
                archive.addfile(member, io.BytesIO(data) if kind == tarfile.REGTYPE else None)
                pins.append(pin(name, data, "120000" if kind == tarfile.SYMTYPE else "100644"))
        data = raw.getvalue()
        path = self.base / "fixture.tar"
        path.write_bytes(data)
        lock = sources.SourceLock(b"", "fixture-only", "0" * 40, tuple(pins),
                                   sources.ArchivePin(len(data), sources.digest(data), "fixture"))
        return path, lock

    def test_local_cli_preserves_complete_closure_and_independent_build(self):
        # Given: real pinned checkouts and a new output.
        environment = os.environ.copy()
        environment.update(TMPDIR=str(self.base), NODE_COMPILE_CACHE=str(self.base / "node"),
                           BUN_RUNTIME_TRANSPILER_CACHE_PATH=str(self.base / "bun"),
                           PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
        # When: use the real public CLI, not a mocked verifier.
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/prepare-j715-sources.py"),
             "--donor-source", str(DONOR), "--output", str(self.output)],
            check=False, capture_output=True, text=True, env=environment)
        # Then: every original is exact, licenses retained, and build bytes independent.
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads((self.output / "source-receipt.json").read_bytes())
        self.assertEqual(receipt["audio"]["commit"], self.lock.commit)
        self.assertEqual(receipt["input_form"], "local-checkout")
        self.assertFalse(receipt["archive_verified"])
        self.assertFalse(receipt["hardware_qualified"])
        self.assertEqual(receipt["input_identity"],
                         {"repository": self.lock.repository, "commit": self.lock.commit})
        self.assertEqual(receipt["checkout_shallow"], sources.git(
            DONOR, ["rev-parse", "--is-shallow-repository"]) == "true")
        self.assertEqual({f["path"] for f in receipt["audio"]["files"]},
                         {f.path for f in self.lock.files})
        for file in self.lock.files:
            for directory in ("original", "build"):
                file.verify((self.output / directory / file.path).read_bytes())
        for file in sources.SourceLock.read(ROOT / "display/j715/source-lock.json").files:
            file.verify((self.output / "upstream-original" / file.path).read_bytes())
        model = "audio/j715/j715.conf"
        (self.output / "build" / model).write_bytes(b"later build changes")
        self.assertEqual((self.output / "original" / model).read_bytes(), (DONOR / model).read_bytes())

    def test_changed_bytes_are_refused_before_output(self):
        # Given
        donor = self.checkout()
        (donor / "audio/j715/j715.conf").write_bytes(b"substitution")
        # When / Then
        with self.assertRaises(sources.SourceError):
            builder.prepare(donor, self.output)
        self.assertFalse(self.output.exists())

    def test_omitted_file_is_refused_before_output(self):
        # Given
        donor = self.checkout()
        (donor / "audio/j713/speakersafetyd/Cargo.lock").unlink()
        # When / Then
        with self.assertRaises(FileNotFoundError):
            builder.prepare(donor, self.output)
        self.assertFalse(self.output.exists())

    def test_wrong_commit_is_refused_even_with_matching_worktree(self):
        # Given: exact donor bytes but a lock for the actual upstream commit.
        upstream = sources.SourceLock.read(ROOT / "display/j715/source-lock.json")
        wrong = replace(self.lock, commit=upstream.commit)
        # When / Then
        with self.assertRaises(sources.SourceError):
            sources.local_sources(DONOR, wrong)

    def test_omitted_lock_entry_cannot_shrink_selected_checkout_closure(self):
        # Given: valid pin identities, but one committed file was removed from the lock.
        incomplete = replace(self.lock, files=self.lock.files[:-1])
        # When / Then: the selected Git tree, not the remaining list, defines completeness.
        with self.assertRaises(sources.SourceError):
            sources.local_sources(DONOR, incomplete)

    def test_oversized_local_file_is_refused_before_reading_bytes(self):
        # Given: a regular source file larger than its committed identity.
        donor = self.checkout()
        target = donor / "audio/j715/j715.conf"
        with target.open("ab") as stream:
            stream.truncate(sources.MAX_EXPANDED + 1)
        real_open = Path.open

        def bounded_open(path, *args, **kwargs):
            self.assertNotEqual(path, target, "Oversized input was opened")
            return real_open(path, *args, **kwargs)

        # When / Then: reject by metadata before allocating an oversized byte buffer.
        with (mock.patch.object(Path, "open", bounded_open),
              self.assertRaises(sources.SourceError)):
            sources.local_sources(donor, self.lock)

    def test_branch_name_does_not_authorize_substituted_content(self):
        # Given: HEAD still names the pin, but the branch worktree was replaced.
        donor = self.checkout()
        target = "audio/j713/kernel/m4-audio-board.h"
        (donor / target).write_bytes((ROOT / "audio/j713/kernel/j713-audio.h").read_bytes())
        # When / Then
        with self.assertRaises(sources.SourceError):
            builder.prepare(donor, self.output)

    def test_escaping_local_link_is_refused(self):
        # Given
        donor = self.checkout()
        target = donor / "audio/j715/j715.conf"
        target.unlink()
        target.symlink_to("/etc/passwd")
        # When / Then
        with self.assertRaises(sources.SourceError):
            builder.prepare(donor, self.output)

    def test_existing_output_is_preserved(self):
        # Given
        self.output.mkdir()
        sentinel = self.output / "owner"
        sentinel.write_bytes(b"preserve")
        # When / Then
        with self.assertRaises(sources.SourceError):
            builder.prepare(DONOR, self.output)
        self.assertEqual(sentinel.read_bytes(), b"preserve")

    def test_output_overlap_and_symlink_ancestors_are_refused(self):
        # Given
        donor = self.checkout()
        alias = self.base / "alias"
        alias.symlink_to(donor, target_is_directory=True)
        for output in (alias / "new", donor / "audio/../new"):
            # When / Then
            with self.subTest(output=output), self.assertRaises(sources.SourceError):
                builder.prepare(donor, output)

    def test_valid_archive_retains_in_tree_link_and_licenses(self):
        # Given
        path, lock = self.archive([("LICENSE", b"license", tarfile.REGTYPE),
                                   ("src/source.c", b"source", tarfile.REGTYPE),
                                   ("src/reference.c", b"source.c", tarfile.SYMTYPE)])
        # When
        files = sources.archive_sources(path, lock)
        sources.materialize(self.output, files)
        # Then
        self.assertEqual((self.output / "LICENSE").read_bytes(), b"license")
        self.assertTrue((self.output / "src/reference.c").is_symlink())
        self.assertEqual((self.output / "src/reference.c").read_bytes(), b"source")

    def test_archive_hash_mismatch_is_refused(self):
        # Given
        path, lock = self.archive([("LICENSE", b"license", tarfile.REGTYPE)])
        data = bytearray(path.read_bytes())
        data[-1] ^= 1
        path.write_bytes(data)
        # When / Then
        with self.assertRaises(sources.SourceError):
            sources.archive_sources(path, lock)

    def test_hostile_archive_members_are_refused(self):
        for entries in [
            [("../outside", b"bad", tarfile.REGTYPE)],
            [("/absolute", b"bad", tarfile.REGTYPE)],
            [("same", b"one", tarfile.REGTYPE), ("same", b"two", tarfile.REGTYPE)],
            [("link", b"../../escape", tarfile.SYMTYPE)],
            [("link", b"/etc/passwd", tarfile.SYMTYPE)],
            [("pipe", b"", tarfile.FIFOTYPE)],
            [("hardlink", b"", tarfile.LNKTYPE)],
        ]:
            # Given
            with self.subTest(entries=entries):
                path, lock = self.archive(entries)
                # When / Then
                with self.assertRaises(sources.SourceError):
                    sources.archive_sources(path, lock)

    def test_archive_missing_file_is_refused(self):
        # Given
        path, lock = self.archive([("LICENSE", b"license", tarfile.REGTYPE)])
        lock = replace(lock, files=(*lock.files, pin("omitted", b"missing")))
        # When / Then
        with self.assertRaises(sources.SourceError):
            sources.archive_sources(path, lock)

    def test_cyclic_archive_links_are_refused(self):
        # Given: individually in-tree links that never resolve to retained source bytes.
        path, lock = self.archive([("a", b"b", tarfile.SYMTYPE),
                                   ("b", b"a", tarfile.SYMTYPE)])
        # When / Then
        with self.assertRaises(sources.SourceError):
            sources.archive_sources(path, lock)

    def test_archive_member_and_expansion_limits_are_enforced(self):
        # Given: a fixture with two members and twelve expanded bytes.
        path, lock = self.archive([("LICENSE", b"license", tarfile.REGTYPE),
                                   ("src", b"bytes", tarfile.REGTYPE)])
        for name, limit in (("MAX_MEMBERS", 1), ("MAX_EXPANDED", 11)):
            # When / Then
            with (self.subTest(limit=name), mock.patch.object(sources, name, limit),
                  self.assertRaises(sources.SourceError)):
                sources.archive_sources(path, lock)

    def test_malformed_locks_are_refused(self):
        original = json.loads(self.lock.raw)
        for mutation in ("duplicate", "license", "traversal", "selection",
                         "outside-selection", "byte-count", "archive-size",
                         "archive-hash", "archive-root"):
            # Given
            with self.subTest(mutation=mutation):
                spec = json.loads(self.lock.raw)
                if mutation == "duplicate":
                    spec["files"].append(spec["files"][0])
                elif mutation == "license":
                    spec["files"] = [f for f in spec["files"] if f["path"] not in original["licenses"]]
                elif mutation == "traversal":
                    spec["files"][0]["path"] = "../escape"
                elif mutation == "selection":
                    spec["selected_paths"] = []
                elif mutation == "outside-selection":
                    spec["selected_paths"] = ["audio/j715"]
                elif mutation == "byte-count":
                    spec["tracked_file_bytes"] += 1
                elif mutation == "archive-size":
                    spec["archive"]["size"] = -1
                elif mutation == "archive-hash":
                    spec["archive"]["sha256"] = "not-a-digest"
                else:
                    spec["archive"]["root"] = "outer/inner"
                path = self.base / "lock.json"
                path.write_text(json.dumps(spec))
                # When / Then
                with self.assertRaises(sources.SourceError):
                    sources.SourceLock.read(path)

    def test_duplicate_json_keys_are_refused(self):
        # Given
        path = self.base / "lock.json"
        path.write_text('{"schema": 1, "schema": 2}')
        # When / Then
        with self.assertRaises(sources.SourceError):
            sources.SourceLock.read(path)


if __name__ == "__main__":
    unittest.main()
