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


class BoostPlacementTests(MainPlacementTests):
    """Measured capacities (cpufreq DTB): a leak-free clamp replaces the affinity mask."""

    def setUp(self):
        fixture.PlacementTests.setUp(self)
        for cpu in set(range(10)) - self.performance:
            self.write(f"sys/devices/system/cpu/cpu{cpu}/cpu_capacity", "331\n")
        self.clamps = {789: 0}
        self.clamp_calls = []
        patch.object(placement, "util_min", side_effect=lambda pid: self.clamps[pid]).start()
        patch.object(placement, "set_util_min", side_effect=self.set_clamp).start()

    def set_clamp(self, pid, value):
        self.clamp_calls.append((pid, value))
        self.clamps[pid] = value

    def test_apply_repeat_restore_clamps_only_main_and_keeps_every_cpu(self):
        self.write(self.dropin.relative_to(self.root), "# Existing owner policy\nCPUAffinity=2 4\n")
        before_dropin = self.dropin.read_bytes()
        self.assertEqual(self.cli(), {"status": "active", "pid": 789, "boost": 1024})
        saved = self.main_state.read_bytes()
        self.assertEqual(json.loads(saved)["policy"], "hyprland-main-uclamp-v1")
        self.assertEqual(self.cli()["status"], "active")
        self.assertEqual(self.main_state.read_bytes(), saved)
        self.assertEqual(self.clamp_calls, [(789, 1024)])
        self.assertEqual(self.set_calls, [])
        self.assertEqual(self.masks[789], set(range(10)))
        self.assertEqual(self.main_state.stat().st_mode & 0o777, 0o600)
        self.assertTrue(all(command[0] == "hyprctl" for command in self.commands))
        self.assertEqual(self.cli("restore")["status"], "restored")
        self.assertEqual(self.clamp_calls[-1], (789, 0))
        self.assertFalse(self.main_state.exists())
        self.assertEqual(self.dropin.read_bytes(), before_dropin)

    def test_customized_clamp_refused_without_state(self):
        self.clamps[789] = 512
        with self.assertRaisesRegex(ValueError, "clamp is customized"):
            self.cli()
        self.assertEqual(self.clamp_calls, [])
        self.assertFalse(self.main_state.exists())

    def test_clamp_changed_elsewhere_is_left_alone(self):
        self.cli(); saved = self.main_state.read_bytes()
        self.clamps[789] = 300
        for action in ("apply", "restore"):
            with self.assertRaisesRegex(ValueError, "changed"):
                self.cli(action)
            self.assertEqual(self.main_state.read_bytes(), saved)
        self.assertEqual(self.clamps[789], 300)
        self.assertEqual(self.clamp_calls, [(789, 1024)])

    def test_reused_pid_restore_never_changes_new_process(self):
        self.cli(); self.process(789, 9999); self.clamps[789] = 1024
        self.assertFalse(self.cli("restore")["identity_still_current"])
        self.assertEqual(self.clamp_calls, [(789, 1024)])
        self.assertFalse(self.main_state.exists())

    def test_failed_clamp_rolls_back_state(self):
        with patch.object(placement, "set_util_min", side_effect=OSError("refused")):
            with self.assertRaisesRegex(OSError, "refused"): self.cli()
        self.assertFalse(self.main_state.exists())
        self.assertEqual(self.clamps[789], 0)

    def test_clamp_without_readback_never_reports_active(self):
        with patch.object(placement, "set_util_min", side_effect=lambda pid, value: self.clamps.__setitem__(pid, 512)):
            with self.assertRaisesRegex(ValueError, "changed elsewhere"): self.cli()
        self.assertTrue(self.main_state.exists())

    def test_live_nested_session_refused(self):
        self.cli(); saved = self.main_state.read_bytes()
        self.process(888, 9000); self.clamps[888] = 0; self.instances[0]["pid"] = 888
        with self.assertRaisesRegex(ValueError, "Another live compositor"):
            self.cli()
        self.assertEqual(self.main_state.read_bytes(), saved)
        self.assertEqual(self.clamp_calls, [(789, 1024)])

    def test_stale_affinity_state_replaced_by_clamp(self):
        stale = {"policy": "hyprland-main-only-v1", "identity": {"pid": 700, "start": 1, "uid": 1000,
                 "boot": "old-boot", "executable": "/usr/bin/Hyprland"},
                 "original": list(range(10)), "performance": sorted(self.performance)}
        self.main_state.write_text(json.dumps(stale))
        self.assertEqual(self.cli()["boost"], 1024)
        self.assertEqual(json.loads(self.main_state.read_text())["policy"], "hyprland-main-uclamp-v1")
        self.assertEqual(self.set_calls, [])

    def test_unexpected_capacities_refused_before_clamp(self):
        cases = (("cpu1", "1024\n", "cpu0", "512\n"),   # a P core below full capacity
                 ("cpu0", "331\n", "cpu2", "400\n"))    # E cores that disagree
        for p_cpu, p_value, e_cpu, e_value in cases:
            with self.subTest(p=p_value, e=e_value):
                old = {c: (self.root / f"sys/devices/system/cpu/{c}/cpu_capacity").read_text() for c in (p_cpu, e_cpu)}
                self.write(f"sys/devices/system/cpu/{p_cpu}/cpu_capacity", p_value)
                self.write(f"sys/devices/system/cpu/{e_cpu}/cpu_capacity", e_value)
                with self.assertRaisesRegex(ValueError, "Unexpected scheduler capacities"):
                    self.cli()
                for c, value in old.items():
                    self.write(f"sys/devices/system/cpu/{c}/cpu_capacity", value)
        self.assertEqual(self.clamp_calls, [])

    # Affinity-only behaviors from the parent class do not apply to the clamp.
    test_cli_apply_repeat_restore_changes_only_main_and_no_browser_files = None
    test_custom_main_and_modified_repeat_preserved = None
    test_cli_main_lock_held_during_affinity = None
    test_failed_set_rolls_back_state_without_browser_operation = None
    test_successful_set_with_restricted_readback_never_reports_active = None
    test_stale_main_state_replaced_without_touching_reused_pid = None
    test_modified_saved_masks_refused_without_affinity_write = None
    test_restore_successful_set_with_restricted_readback_keeps_state = None


class SchedAttrEncoding(unittest.TestCase):
    def test_clamp_request_resets_on_fork_and_keeps_nice(self):
        current = placement.SCHED_ATTR.pack(56, 0, 0, -3, 0, 0, 0, 0, 0, 1024)
        requests = []
        def call(index, pid, buffer, *rest):
            if index == 1:
                buffer.raw = current
            else:
                requests.append(placement.SCHED_ATTR.unpack(buffer.raw))
        with patch.object(placement, "sched_call", side_effect=call):
            placement.set_util_min(789, 1024)
        size, policy, flags, nice, priority, *_, low, high = requests[0]
        self.assertEqual((size, policy, nice, low), (56, 0, -3, 1024))
        self.assertEqual(flags, placement.SCHED_FLAG_RESET_ON_FORK | placement.SCHED_FLAG_UTIL_CLAMP_MIN)

    def test_realtime_compositor_refused(self):
        def call(index, pid, buffer, *rest):
            buffer.raw = placement.SCHED_ATTR.pack(56, 1, 0, 0, 10, 0, 0, 0, 0, 1024)
        with patch.object(placement, "sched_call", side_effect=call):
            with self.assertRaisesRegex(ValueError, "normal-priority"):
                placement.util_min(789)
