# SPDX-License-Identifier: MIT
"""Fake-root owner tests: no module, service, firmware or real panel access."""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_j715_display_profile import FIXTURE, PROFILE, load

BACKLIGHT = load("j715_owner_test", "tools/m4-j715-backlight.py")
ORIGINAL = load("unchanged_owner_keys", "tests/test_m4_backlight.py")
ORIGINAL.BACKLIGHT = BACKLIGHT


class OwnerKeys(ORIGINAL.OwnerKeys):
    def test_plain_function_keys_use_native_brightness(self):
        for key, step in (("F1", "5%-"), ("F2", "+5%")):
            with self.subTest(key=key):
                self.assertIn(f'hl.unbind("{key}")', BACKLIGHT.BINDS)
                self.assertIn(f'"m4-j715-backlight brightness {step}"', BACKLIGHT.BINDS)
        self.assertNotIn("F1", {entry[0] for entry in BACKLIGHT.OWNER.KEYS})

    def test_removal_preserves_an_edited_managed_block(self):
        BACKLIGHT.add_keys(self.config, True)
        source = self.config.read_text().replace("m4-j715-backlight brightness +5%", "custom +5%")
        self.config.write_text(source)
        with self.assertRaises(BACKLIGHT.Refused):
            BACKLIGHT.remove_keys(self.config)
        self.assertEqual(self.config.read_text(), source)


class Startup(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = PROFILE.parse(FIXTURE)
        self.client = "sys/firmware/devicetree/base" + self.profile.resources[0].path
        self.put(BACKLIGHT.COMPATIBLE, b"apple,j715\0apple,t8132\0")
        self.put("proc/sys/kernel/osrelease", b"test-kernel")
        self.put(self.client + "/status", b"okay\0")
        self.put(self.client + "/memory-region", b"\0\0\0\4")
        self.put(self.client + "/omarchy,display-handoff-verified", b"")
        self.put(self.client + "/omarchy,display-profile", self.profile.digest.encode() + b"\0")
        self.put(self.client + "/omarchy,display-firmware", self.profile.firmware_identity.encode() + b"\0")
        self.module = b"synthetic-module"
        self.put(BACKLIGHT.MODULE, self.module)
        self.put(BACKLIGHT.PINS, json.dumps({"module_bytes": len(self.module),
                 "module_sha256": PROFILE.sha(self.module), "kernel_release": "test-kernel",
                 "profile_sha256": self.profile.digest}).encode())
        for name, value in (("status", b"attempted=1 online=1 failed=0 applied_nits=17"),
                            ("start", b""), ("brightness_nits", b"")):
            self.put(BACKLIGHT.DEBUG + "/" + name, value)
        self.put(BACKLIGHT.BACKLIGHT + "/actual_brightness", b"17")
        self.put(BACKLIGHT.BACKLIGHT + "/max_brightness", b"123")
        self.calls = []

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
        return path

    def load_module(self, command, **options):
        self.assertTrue((self.root / BACKLIGHT.STATE / "attempt").exists())
        self.calls.append(command)

    def start(self, run=None):
        with patch.object(BACKLIGHT, "qualified", return_value=self.profile):
            return BACKLIGHT.start(self.root, run or self.load_module)

    def test_wrong_board_refuses_before_state(self):
        self.put(BACKLIGHT.COMPATIBLE, b"apple,j713\0apple,t8132\0")
        with self.assertRaises(BACKLIGHT.Refused):
            self.start()
        self.assertFalse((self.root / BACKLIGHT.STATE).exists())
        self.assertFalse(self.calls)

    def test_hash_size_kernel_profile_and_handoff_guard_loading(self):
        failures = [(BACKLIGHT.MODULE, b"X" + self.module[1:]),
                    (BACKLIGHT.MODULE, b"short"),
                    ("proc/sys/kernel/osrelease", b"other"),
                    (self.client + "/status", b"disabled\0"),
                    (self.client + "/omarchy,display-profile", b"other\0"),
                    (self.client + "/omarchy,display-firmware", b"other\0")]
        for name, bad in failures:
            with self.subTest(name=name, value=bad):
                path = self.root / name
                saved = path.read_bytes()
                self.put(name, bad)
                with self.assertRaises(BACKLIGHT.Refused):
                    self.start()
                self.put(name, saved)
        for name in ("memory-region", "omarchy,display-handoff-verified", "status"):
            path = self.root / self.client / name
            saved = path.read_bytes()
            path.unlink()
            with self.assertRaises(BACKLIGHT.Refused):
                self.start()
            path.write_bytes(saved)
        self.assertFalse(self.calls)
        self.assertFalse((self.root / BACKLIGHT.STATE / "attempt").exists())

    def test_interrupted_or_partial_start_latches_and_refuses_retry(self):
        def interrupted(command, **options):
            self.load_module(command, **options)
            raise subprocess.CalledProcessError(1, command)
        with self.assertRaises(subprocess.CalledProcessError):
            self.start(interrupted)
        with self.assertRaises(BACKLIGHT.Refused):
            self.start()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue((self.root / BACKLIGHT.STATE / "attempt").exists())

    def test_completion_is_required_before_latch_removal(self):
        for state in (b"online=0 failed=0 applied_nits=17", b"online=1 failed=1 applied_nits=17",
                      b"online=1 failed=0 applied_nits=124", b"online=1 failed=0 applied_nits=16"):
            with self.subTest(state=state):
                self.put(BACKLIGHT.DEBUG + "/status", state)
                with self.assertRaises(BACKLIGHT.Refused):
                    self.start()
                marker = self.root / BACKLIGHT.STATE / "attempt"
                self.assertTrue(marker.exists())
                marker.unlink()  # Test-only reset, not production recovery.

    def test_success_writes_profile_initial_and_clears_latch_last(self):
        result = self.start()
        self.assertFalse(result["failed_attempt"])
        self.assertEqual(result["nits"], self.profile.initial_nits)
        self.assertEqual((self.root / BACKLIGHT.DEBUG / "brightness_nits").read_text(), "17\n")
        self.assertEqual((self.root / BACKLIGHT.DEBUG / "start").read_text(), "1\n")

    def test_loaded_incomplete_is_not_reloaded(self):
        (self.root / "sys/module" / BACKLIGHT.NAME).mkdir(parents=True)
        self.assertEqual(self.start()["nits"], 17)
        for path in (self.root / BACKLIGHT.BACKLIGHT).iterdir():
            path.unlink()
        (self.root / BACKLIGHT.BACKLIGHT).rmdir()
        with self.assertRaises(BACKLIGHT.Refused):
            self.start()
        self.assertFalse(self.calls)

    def test_hold_recovery_privileges_and_inflight_lock(self):
        with patch.object(BACKLIGHT.os, "geteuid", return_value=0), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(BACKLIGHT.main(["--disable", "--root", str(self.root)]), 0)
            with self.assertRaises(BACKLIGHT.Refused):
                self.start()
            self.put(BACKLIGHT.STATE + "/attempt", b"interrupted")
            self.assertEqual(BACKLIGHT.main(["--enable", "--root", str(self.root)]), 0)
        self.assertFalse((self.root / BACKLIGHT.STATE / "attempt").exists())
        with BACKLIGHT.locked_state(self.root) as state:
            BACKLIGHT.write_state(state, "attempt", "inflight")
            with self.assertRaises(BACKLIGHT.Refused), BACKLIGHT.locked_state(self.root):
                self.fail("acquired an inflight state lock")
        with patch.object(BACKLIGHT.os, "geteuid", return_value=1000), contextlib.redirect_stderr(io.StringIO()):
            for action in ("--boot", "--enable", "--disable", "--keys-hook", "--keys-remove-hook"):
                self.assertEqual(BACKLIGHT.main([action, "--root", str(self.root)]), 1)


class Routing(unittest.TestCase):
    setUp = Startup.setUp
    put = Startup.put

    def run_route(self, command, **options):
        self.calls.append(command)
        value = ('[{"name":"Unknown-1","focused":true}]' if command[0] == "hyprctl" else
                 f"j715-dcp,backlight,17,{self.current}%,123")
        return SimpleNamespace(stdout=value, returncode=0)
    def test_external_display_bypasses_profile_and_native_actuator(self):
        calls = []
        def run(command, **options):
            calls.append(command)
            return SimpleNamespace(stdout='[{"name":"DP-1","focused":true}]', returncode=0)
        with patch.object(BACKLIGHT, "parse", side_effect=AssertionError("external profile lookup")), \
             patch.object(BACKLIGHT, "qualified", side_effect=AssertionError("external profile lookup")):
            BACKLIGHT.brightness("1%-", True, run, self.root)
        self.assertEqual(calls, [["hyprctl", "monitors", "-j"],
                                ["omarchy", "brightness", "display", "--no-osd", "1%-"]])

    def test_native_keys_obey_profile_minimum_and_preserve_steps(self):
        profile_path = self.root / BACKLIGHT.PROFILE
        profile_path.parent.mkdir(parents=True, exist_ok=True)
        profile_path.write_bytes(FIXTURE.read_bytes())
        for current, step, expected in ((4, "+5%", "5%"), (6, "5%-", "2%"), (2, "1%-", "2%"),
                                         (100, "+1%", "100%"), (5, "5%-", "4%"), (20, "1%", "2%")):
            with self.subTest(step=step, current=current):
                self.calls = []
                self.current = current
                with patch.dict(os.environ, XDG_RUNTIME_DIR=str(self.root)):
                    BACKLIGHT.brightness(step, False, self.run_route, self.root)
                self.assertIn(["brightnessctl", "-d", "j715-dcp", "set", expected], self.calls)
                self.assertTrue(any(command[0] == "omarchy-osd" for command in self.calls))


if __name__ == "__main__":
    unittest.main()
