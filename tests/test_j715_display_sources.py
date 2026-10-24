# SPDX-License-Identifier: MIT
"""Pinned display sources remain usable after integration-only commits."""
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_j715_display_profile import PROFILE, ROOT, environment
from j715_sources import FilePin, SourceError, SourceLock, local_sources


class DisplaySources(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.env = environment()
        self.env.update(GIT_AUTHOR_NAME="Source fixture", GIT_COMMITTER_NAME="Source fixture",
                        GIT_AUTHOR_EMAIL="fixture@example.invalid",
                        GIT_COMMITTER_EMAIL="fixture@example.invalid")
        self.git("init", "-q")
        self.target = self.root / "display/client.c"
        self.target.parent.mkdir()
        self.data = b"locked display source\n"
        self.target.write_bytes(self.data)
        self.commit()
        revision = self.git("rev-parse", "HEAD")
        pin = FilePin("display/client.c", "100644",
                      hashlib.sha1(b"blob " + str(len(self.data)).encode() + b"\0" + self.data).hexdigest(),
                      len(self.data), PROFILE.sha(self.data))
        self.lock = SourceLock(b"", "synthetic", revision, (pin,), None, ("display",))

    def git(self, *args: str) -> str:
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       env=self.env, stderr=subprocess.PIPE, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("commit", "-qm", "Synthetic source fixture")

    def test_integration_only_descendant_retains_exact_pinned_closure(self):
        (self.root / "integration.txt").write_bytes(b"integration only\n")
        self.commit()
        self.assertNotEqual(self.git("rev-parse", "HEAD"), self.lock.commit)
        result = local_sources(self.root, self.lock)
        self.assertEqual([(item.pin.path, item.data) for item in result],
                         [("display/client.c", self.data)])

    def test_committed_mutation_refuses_even_with_original_worktree_bytes(self):
        self.target.write_bytes(b"substituted source\n")
        self.commit()
        self.target.write_bytes(self.data)
        with self.assertRaises(SourceError):
            local_sources(self.root, self.lock)

    def test_uncommitted_locked_bytes_refuse(self):
        self.target.write_bytes(b"substituted source\n")
        with self.assertRaises(SourceError):
            local_sources(self.root, self.lock)

    def test_added_committed_selected_file_refuses(self):
        (self.target.parent / "extra.c").write_bytes(b"extra source\n")
        self.commit()
        with self.assertRaises(SourceError):
            local_sources(self.root, self.lock)

    def test_unrelated_history_with_matching_files_refuses(self):
        tree = self.git("rev-parse", "HEAD^{tree}")
        unrelated = self.git("commit-tree", tree, "-m", "Unrelated synthetic root")
        self.git("update-ref", "HEAD", unrelated)
        with self.assertRaises(SourceError):
            local_sources(self.root, self.lock)

    def test_real_display_lock_matches_unchanged_upstream_tree(self):
        lock = SourceLock.read(ROOT / "display/j715/source-lock.json")
        self.assertEqual(len(local_sources(ROOT, lock)), len(lock.files))


if __name__ == "__main__":
    unittest.main()
