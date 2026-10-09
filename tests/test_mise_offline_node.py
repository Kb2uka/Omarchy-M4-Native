# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Exercise the patched upstream script with real Bash and tar in scratch directories."""
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class OfflineNodeArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="m4 node setup ")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        external = os.environ.get("OWNER_NODE_SOURCE")
        if external:
            source = Path(external).read_text()
        else:
            fixture = REPO / "tests/fixtures/mise-work-v4.0.4.sh"
            self.assertEqual(hashlib.sha256(fixture.read_bytes()).hexdigest(),
                             "b8920d1d2d5b3f7db64401c52858136f33fa9f476c353ccd1000d42ee1443bc3")
            source_file = self.root / "install/user/mise-work.sh"
            source_file.parent.mkdir(parents=True)
            source_file.write_bytes(fixture.read_bytes())
            subprocess.run(["git", "apply", str(REPO / "patches/omarchy/offline-node-arm64.patch")],
                           cwd=self.root, check=True, capture_output=True)
            source = source_file.read_text()
        # Redirect only the two absolute package roots; leave selection/extraction unchanged.
        for original, replacement in (
            ("NODE_PACKAGE_DIR=/opt/packages", "NODE_PACKAGE_DIR=$FIXTURE_ISO_PACKAGES"),
            ("NODE_PACKAGE_DIR=/var/lib/omarchy/provisioning/packages", "NODE_PACKAGE_DIR=$FIXTURE_OWNER_PACKAGES"),
        ):
            self.assertEqual(source.count(original), 1)
            source = source.replace(original, replacement)
        self.source = self.root / "setup.sh"
        self.source.write_text(source)

    def run_setup(self, architecture, context, archives):
        root = self.root / str(len(list(self.root.glob("case-*"))))
        root = root.with_name("case-" + root.name)
        root.mkdir()
        home, commands = root / "home", root / "commands"
        home.mkdir()
        commands.mkdir()
        iso, owner = root / "iso packages", root / "owner packages"
        for directory in (iso, owner):
            directory.mkdir()
            for arch in archives:
                with tarfile.open(directory / f"node-v26.11.1-linux-{arch}.tar.gz", "w:gz") as archive:
                    member = tarfile.TarInfo(f"node-v26.11.1-linux-{arch}/bin/node")
                    data = arch.encode()
                    member.size, member.mode = len(data), 0o755
                    archive.addfile(member, io.BytesIO(data))
        (commands / "uname").write_text('#!/bin/sh\nprintf "%s\\n" "$FIXTURE_ARCH"\n')
        (commands / "mise").write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOME/mise-calls"\n')
        for command in commands.iterdir():
            command.chmod(0o755)
        env = {"PATH": str(commands) + ":" + os.defpath, "HOME": str(home),
               "FIXTURE_ARCH": architecture, "FIXTURE_ISO_PACKAGES": str(iso),
               "FIXTURE_OWNER_PACKAGES": str(owner), "OMARCHY_SETUP_CONTEXT": context}
        result = subprocess.run(["bash", "-e", str(self.source)], env=env,
                                text=True, capture_output=True)
        return result, home, (home / "mise-calls").read_text().splitlines()

    def test_both_archives_choose_host_for_each_entry(self):
        for machine, expected in (("x86_64", "x64"), ("aarch64", "arm64")):
            for context in ("iso-chroot", "provision-owner"):
                with self.subTest(machine=machine, context=context):
                    result, home, calls = self.run_setup(machine, context, ("x64", "arm64"))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual((home / ".local/share/mise/installs/node/26.11.1/bin/node").read_text(), expected)
                    self.assertIn("use -g node@26.11.1", calls)
                    self.assertNotIn("use -g node@latest", calls)

    def test_arm_archive_does_not_fall_back_to_network(self):
        for context in ("iso-chroot", "provision-owner"):
            with self.subTest(context=context):
                result, home, calls = self.run_setup("aarch64", context, ("arm64",))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((home / ".local/share/mise/installs/node/26.11.1/bin/node").read_text(), "arm64")
                self.assertNotIn("use -g node@latest", calls)

    def test_unknown_architecture_does_not_unpack_x64(self):
        for context in ("iso-chroot", "provision-owner"):
            with self.subTest(context=context):
                result, home, calls = self.run_setup("riscv64", context, ("x64",))
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unsupported architecture", result.stderr)
                self.assertFalse((home / ".local/share/mise/installs/node").exists())
                self.assertFalse(any(call.startswith("use ") for call in calls))

    def test_missing_archive_keeps_existing_context_policy(self):
        result, _, calls = self.run_setup("aarch64", "iso-chroot", ())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tarball missing", result.stderr)
        self.assertFalse(any(call.startswith("use ") for call in calls))
        result, _, calls = self.run_setup("aarch64", "provision-owner", ())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("use -g node@latest", calls)

    def test_runtime_keeps_network_selection(self):
        result, home, calls = self.run_setup("riscv64", "runtime", ("x64", "arm64"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("use -g node@latest", calls)
        self.assertFalse((home / ".local/share/mise/installs/node").exists())


if __name__ == "__main__":
    unittest.main()
