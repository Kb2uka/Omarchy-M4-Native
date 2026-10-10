# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Main-only startup entry point with isolated proc/sys and syscall fixtures."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_m4_session_placement as fixture

placement = fixture.placement


class MainPlacementTests(unittest.TestCase):
    setUp = fixture.PlacementTests.setUp
    write = fixture.PlacementTests.write
    process = fixture.PlacementTests.process
    command = fixture.PlacementTests.command
    set_affinity = fixture.PlacementTests.set_affinity

    def cli(self, action="apply"):
        output = io.StringIO()
        real_path = Path
        with patch.object(sys, "argv", ["placement", "--main-only", action]), \
             patch.dict(os.environ, self.environment, clear=True), \
             patch.object(placement, "Path", side_effect=lambda value: self.root if value == "/" else real_path(value)), \
             redirect_stdout(output):
            placement.main()
        return json.loads(output.getvalue())

    @property
    def main_state(self):
        return self.state.with_name("m4-hyprland-main-placement.json")

    def test_cli_apply_repeat_restore_changes_only_main_and_no_browser_files(self):
        self.masks.update({790: {2, 4}, 900: set(range(10))})
        for tid in (789, 790):
            self.write(f"proc/789/task/{tid}/comm", "Hyprland\n")
        self.write(self.dropin.relative_to(self.root), "# Existing owner policy\nCPUAffinity=2 4\n")
        before_dropin = self.dropin.read_bytes()
        other = {pid: value.copy() for pid, value in self.masks.items() if pid != 789}
        self.assertEqual(self.cli()["performance"], sorted(self.performance))
        saved = self.main_state.read_bytes()
        self.assertEqual(self.cli()["status"], "active")
        self.assertEqual(self.main_state.read_bytes(), saved)
        self.assertEqual(self.set_calls, [(789, self.performance)])
        self.assertEqual({pid: self.masks[pid] for pid in other}, other)
        self.assertFalse(self.state.exists())
        self.assertEqual(self.dropin.read_bytes(), before_dropin)
        self.assertTrue(all(command[0] == "hyprctl" for command in self.commands))
        self.assertEqual(self.main_state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cli("restore")["status"], "restored")
        self.assertEqual(self.masks[789], set(range(10)))
        self.assertEqual({pid: self.masks[pid] for pid in other}, other)
        self.assertEqual(self.dropin.read_bytes(), before_dropin)
        self.assertFalse(self.main_state.exists())

    def test_custom_main_and_modified_repeat_preserved(self):
        self.masks[789] = {2, 4}
        with self.assertRaisesRegex(ValueError, "customized"):
            self.cli()
        self.assertEqual(self.set_calls, [])
        self.assertFalse(self.main_state.exists())
        self.masks[789] = set(range(10)); self.cli()
        saved = self.main_state.read_bytes()
        self.masks[789] = {2, 4}
        for action in ("apply", "restore"):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.cli(action)
            self.assertEqual(self.main_state.read_bytes(), saved)
            self.assertEqual(self.masks[789], {2, 4})

    def test_reused_pid_restore_never_changes_new_process(self):
        self.cli(); self.process(789, 9999); self.masks[789] = {2}
        self.assertFalse(self.cli("restore")["identity_still_current"])
        self.assertEqual(self.masks[789], {2})
        self.assertEqual(len(self.set_calls), 1)

    def test_live_nested_session_refused(self):
        self.cli(); saved = self.main_state.read_bytes()
        self.process(888, 9000); self.masks[888] = set(range(10)); self.instances[0]["pid"] = 888
        with self.assertRaisesRegex(ValueError, "Another live compositor"):
            self.cli()
        self.assertEqual(self.main_state.read_bytes(), saved)
        self.assertEqual(self.masks[888], set(range(10)))
        self.assertEqual(len(self.set_calls), 1)

    def test_wrong_session_owner_board_capacity_refused_before_affinity(self):
        cases = [
            ("proc/789/status", "Uid:\t0\t0\t0\t0\nTgid:\t789\n"),
            ("proc/789/status", "Uid:\t1000\t1000\t1000\t1000\nTgid:\t700\n"),
            ("sys/firmware/devicetree/base/compatible", b"apple,j713\0"),
            ("sys/devices/system/cpu/cpu0/cpu_capacity", "512\n"),
        ]
        for name, value in cases:
            path = self.root / name; old = path.read_bytes()
            with self.subTest(name=name, value=value):
                self.write(name, value)
                with self.assertRaises(ValueError): self.cli()
                self.assertEqual(self.set_calls, [])
                self.assertFalse(self.main_state.exists())
            path.write_bytes(old)
        self.instances[0]["wl_socket"] = "greeter-socket"
        with self.assertRaisesRegex(ValueError, "one current compositor"): self.cli()
        self.assertEqual(self.set_calls, [])

    def test_main_state_symlink_and_other_owner_runtime_refused(self):
        target = self.write("unrelated", "preserve")
        self.main_state.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "Symlink"): self.cli()
        self.assertEqual(target.read_text(), "preserve")
        self.main_state.unlink(); self.runtime_uid = 2000
        with self.assertRaisesRegex(ValueError, "runtime directory"): self.cli()
        self.assertEqual(self.set_calls, [])

    def test_apply_identity_rechecked_before_write(self):
        original = placement.identity
        calls = 0
        def changing(root, pid, expected=None):
            nonlocal calls
            calls += 1
            if calls == 2: self.process(pid, 9999)
            return original(root, pid, expected)
        with patch.object(placement, "identity", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "changed during"): self.cli()
        self.assertEqual(self.set_calls, [])
        self.assertFalse(self.main_state.exists())

    def test_cli_main_lock_held_during_affinity(self):
        real = self.set_affinity
        def checked(pid, mask):
            with self.main_state.with_suffix(".lock").open("r") as other:
                with self.assertRaises(BlockingIOError):
                    placement.fcntl.flock(other, placement.fcntl.LOCK_EX | placement.fcntl.LOCK_NB)
            real(pid, mask)
        with patch.object(placement.os, "sched_setaffinity", side_effect=checked): self.cli()

    def test_failed_set_rolls_back_state_without_browser_operation(self):
        with patch.object(placement.os, "sched_setaffinity", side_effect=OSError("refused")):
            with self.assertRaisesRegex(OSError, "refused"): self.cli()
        self.assertFalse(self.main_state.exists())
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.masks[789], set(range(10)))

    def test_successful_set_with_restricted_readback_never_reports_active(self):
        narrowed = {min(self.performance)}
        def restricted(pid, mask):
            self.set_calls.append((pid, set(mask)))
            self.masks[pid] = set(mask) & narrowed
        with patch.object(placement.os, "sched_setaffinity", side_effect=restricted):
            with self.assertRaisesRegex(ValueError, "changed elsewhere"):
                self.cli()
        self.assertEqual(self.set_calls, [(789, self.performance)])
        self.assertEqual(self.masks[789], narrowed)
        self.assertTrue(self.main_state.exists())
        saved = json.loads(self.main_state.read_text())
        self.assertEqual(saved["original"], list(range(10)))
        self.assertFalse(self.dropin.exists())

    def test_stale_main_state_replaced_without_touching_reused_pid(self):
        self.cli()
        self.process(789, 9999)
        self.masks[789] = {2, 4}
        self.process(888, 9000)
        self.masks[888] = set(range(10))
        self.instances[0]["pid"] = 888
        self.assertEqual(self.cli()["pid"], 888)
        saved = json.loads(self.main_state.read_text())
        self.assertEqual(saved["identity"]["pid"], 888)
        self.assertEqual(saved["identity"]["start"], 9000)
        self.assertEqual(saved["original"], list(range(10)))
        self.assertEqual(self.masks[789], {2, 4})
        self.assertEqual(self.set_calls, [(789, self.performance), (888, self.performance)])

    def test_modified_saved_masks_refused_without_affinity_write(self):
        self.cli()
        original = self.main_state.read_text()
        for key in ("performance", "original"):
            with self.subTest(key=key):
                saved = json.loads(original)
                saved[key] = [2, 4]
                self.main_state.write_text(json.dumps(saved))
                before = self.main_state.read_bytes()
                with self.assertRaisesRegex(ValueError, "Saved main-only placement was changed"):
                    self.cli()
                self.assertEqual(self.main_state.read_bytes(), before)
                self.assertEqual(self.set_calls, [(789, self.performance)])
                self.assertEqual(self.masks[789], self.performance)
        self.main_state.write_text(original)

    def test_restore_successful_set_with_restricted_readback_keeps_state(self):
        self.cli()
        before = self.main_state.read_bytes()
        def restricted(pid, mask):
            self.set_calls.append((pid, set(mask)))
            self.masks[pid] = set(mask) & {2, 4}
        with patch.object(placement.os, "sched_setaffinity", side_effect=restricted):
            with self.assertRaisesRegex(ValueError, "restore did not round-trip"):
                self.cli("restore")
        self.assertEqual(self.main_state.read_bytes(), before)
        self.assertEqual(self.set_calls, [(789, self.performance), (789, set(range(10)))])
        self.assertEqual(self.masks[789], {2, 4})

    def test_unknown_saved_policy_refused(self):
        self.main_state.write_text('{"policy":"other"}')
        for action in ("apply", "restore", "status"):
            with self.assertRaisesRegex(ValueError, "Unrecognized"): self.cli(action)
        self.assertEqual(self.set_calls, [])
