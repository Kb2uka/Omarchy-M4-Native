# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""The one-paste connect script: check the setup script, run it once, leave nothing behind."""
import hashlib
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / 'tools/m4-connect.sh'
SPEC = importlib.util.spec_from_file_location('publish_m4_packages', ROOT / 'tools/publish-m4-packages.py')
PUBLISH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUBLISH)

# Stands in for omarchy-m4-setup.py: records the arguments it was run with.
SETUP = b'import sys\nopen(sys.argv[0] + ".ran", "w").write(" ".join(sys.argv[1:]))\n'


class ConnectScript(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        self.server = self.home / 'server'
        self.server.mkdir()
        (self.server / 'omarchy-m4-setup.py').write_bytes(SETUP)
        self.tmp = self.home / 'tmp'
        self.tmp.mkdir()
        bin_dir = self.home / 'bin'
        bin_dir.mkdir()
        self.sudo_log = self.home / 'sudo.log'
        # A sudo that records its arguments, copies the setup script out of the
        # temporary directory (which the script removes) and then runs it.
        (bin_dir / 'sudo').write_text(
            '#!/usr/bin/env bash\n'
            f'echo "$*" >> {self.sudo_log}\n'
            f'cp "$2" {self.home}/setup-copy.py\n'
            f'exec "$1" {self.home}/setup-copy.py "${{@:3}}"\n')
        (bin_dir / 'sudo').chmod(0o755)
        self.env = dict(os.environ, M4_CONNECT_BASE=self.server.as_uri(), TMPDIR=str(self.tmp),
                        PATH=f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')

    def published(self, setup_bytes=SETUP):
        return PUBLISH.connect_script(hashlib.sha256(setup_bytes).hexdigest())

    def run_script(self, text, piped=False):
        if piped:
            return subprocess.run(['bash'], input=text, env=self.env, capture_output=True, text=True)
        script = self.home / 'm4-connect.sh'
        script.write_text(text)
        return subprocess.run(['bash', script], env=self.env, capture_output=True, text=True)

    def assert_setup_applied_once(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('omarchy-m4-setup.py: OK', result.stdout)
        self.assertEqual((self.home / 'setup-copy.py.ran').read_text(), '--apply')
        self.assertEqual(len(self.sudo_log.read_text().splitlines()), 1)
        self.assertEqual(list(self.tmp.iterdir()), [], 'the temporary directory is removed')

    def test_published_copy_checks_then_applies_setup(self):
        self.assert_setup_applied_once(self.run_script(self.published()))

    def test_works_piped_into_bash(self):
        self.assert_setup_applied_once(self.run_script(self.published(), piped=True))

    def test_mismatched_setup_script_runs_nothing(self):
        (self.server / 'omarchy-m4-setup.py').write_bytes(SETUP + b'# tampered\n')
        result = self.run_script(self.published())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('nothing was changed', result.stderr)
        self.assertFalse(self.sudo_log.exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_unpublished_template_refuses_before_downloading(self):
        (self.server / 'omarchy-m4-setup.py').unlink()  # a download would fail loudly
        result = self.run_script(TEMPLATE.read_text())
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('no published checksum', result.stderr)
        self.assertFalse(self.sudo_log.exists())

    def test_missing_setup_script_runs_nothing(self):
        (self.server / 'omarchy-m4-setup.py').unlink()
        result = self.run_script(self.published())
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sudo_log.exists())
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_template_has_one_hash_slot(self):
        self.assertEqual(TEMPLATE.read_text().count(PUBLISH.CONNECT_HASH), 1)
        self.assertNotIn(PUBLISH.CONNECT_HASH, self.published())


if __name__ == '__main__':
    unittest.main()
