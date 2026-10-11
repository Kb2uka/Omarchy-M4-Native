# SPDX-License-Identifier: MIT
# Copyright (c) 2026 studioxradio aka kb2uka
from contextlib import redirect_stderr
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('bootstrap', Path(__file__).resolve().parents[1] /
                                             'tools/bootstrap-brave-widevine.py')
b = importlib.util.module_from_spec(spec)
spec.loader.exec_module(b)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.marker = self.home / '.config/omarchy/pending/widevine-Brave-Browser.json'
        self.addCleanup(patch.stopall)
        patch.object(b.Path, 'home', return_value=self.home).start()
        self.child = patch.object(b.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)).start()

    def seed(self, **changes):
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        for path in self.marker.parents:
            path.chmod(0o700)
            if path == self.home:
                break
        value = dict(schema=1, profile='Brave-Browser', consent='owner-requested')
        value.update(changes)
        self.marker.write_text(json.dumps(value))
        self.marker.chmod(0o600)
        return self.marker.read_bytes()

    def invoke(self, profile='Brave-Browser'):
        with redirect_stderr(io.StringIO()) as output:
            result = b.main(['--profile', profile])
        return result, output.getvalue()

    def test_no_marker_leaves_existing_owner_and_files_untouched(self):
        self.assertEqual(self.invoke(), (0, ''))
        self.child.assert_not_called()
        self.assertEqual(list(self.home.iterdir()), [])

    def test_exact_consent_calls_bounded_selected_helper_then_consumes_request_once(self):
        self.seed()
        self.assertEqual(self.invoke(), (0, ''))
        self.child.assert_called_once_with(
            [b.sys.executable, str(b.SETUP), '--profile', 'Brave-Browser', '--initialize', '--timeout', '15'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.invoke(), (0, ''))
        self.assertEqual(self.child.call_count, 1)
        self.assertFalse((self.home / '.config/BraveSoftware').exists())

    def test_missing_consent_unknown_schema_or_profile_refuses_without_child(self):
        for change in ({'consent': 'pending-choice'}, {'schema': 2}, {'schema': True}, {'extra': 'unknown'}, {'profile': 'Brave-Browser-M4'}):
            with self.subTest(change=change):
                raw = self.seed(**change)
                self.assertEqual(self.invoke()[0], 1)
                self.assertEqual(self.marker.read_bytes(), raw)
        self.child.assert_not_called()

    def test_offline_or_busy_child_leaves_pending_for_next_launch(self):
        raw = self.seed()
        self.child.return_value.returncode = 1
        result, error = self.invoke()
        self.assertEqual(result, 1)
        self.assertIn('Continue opening the browser', error)
        self.assertEqual(self.marker.read_bytes(), raw)
        self.child.return_value.returncode = 0
        self.assertEqual(self.invoke(), (0, ''))
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.child.call_count, 2)

    def test_timeout_preserves_request_and_does_not_wait_again(self):
        raw = self.seed()
        self.child.side_effect = subprocess.TimeoutExpired('owned setup child', 20)
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.marker.read_bytes(), raw)
        self.assertEqual(self.child.call_count, 1)

    def test_concurrent_launch_does_not_start_another_download(self):
        self.seed()
        lock = self.marker.parent / '.widevine-setup.lock'
        with lock.open('w') as stream:
            lock.chmod(0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.invoke()[0], 1)
        self.child.assert_not_called()
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.invoke(), (0, ''))

    def test_changed_request_not_consumed(self):
        self.seed()
        def changed(*args, **kwargs):
            self.marker.write_text('{"changed":true}')
            return subprocess.CompletedProcess([], 0)
        self.child.side_effect = changed
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.marker.read_text(), '{"changed":true}')

    def test_symlink_or_writable_marker_and_lock_refuse(self):
        raw = self.seed()
        self.marker.chmod(0o666)
        self.assertEqual(self.invoke()[0], 1)
        self.marker.chmod(0o600)
        target = self.marker.with_name('original.json')
        self.marker.rename(target)
        self.marker.symlink_to(target)
        self.assertEqual(self.invoke()[0], 1)
        self.marker.unlink()
        self.marker.write_bytes(raw)
        self.marker.chmod(0o600)
        (self.marker.parent / '.widevine-setup.lock').symlink_to(target)
        self.assertEqual(self.invoke()[0], 1)
        self.child.assert_not_called()

    def test_chromium_consent_dispatch_is_separate_from_brave(self):
        self.seed()
        brave_marker = self.marker
        self.marker = self.marker.with_name('widevine-Chromium.json')
        self.seed(profile='Chromium')
        self.assertEqual(self.invoke('Chromium'), (0, ''))
        self.child.assert_called_once_with(
            [b.sys.executable, str(b.SETUP), '--profile', 'Chromium', '--initialize', '--timeout', '15'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        self.assertFalse(self.marker.exists())
        self.assertTrue(brave_marker.exists())

    def test_profile_markers_are_independent_and_root_refuses(self):
        self.seed()
        self.assertEqual(self.invoke('Brave-Browser-M4'), (0, ''))
        self.child.assert_not_called()
        with patch.object(b.os, 'getuid', return_value=0):
            self.assertEqual(self.invoke()[0], 1)
        self.assertTrue(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
