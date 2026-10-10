# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
from contextlib import redirect_stdout
import importlib.util
import io
import os
import json
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


if sys.platform != "linux":
    raise unittest.SkipTest("Native Linux session placement tests")


SOURCE = Path(__file__).resolve().parents[1] / "tools/m4-session-placement.py"
spec = importlib.util.spec_from_file_location("m4_placement", SOURCE)
placement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(placement)


class PlacementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.performance = {1, 3, 5, 9}  # Different logical numbering from the development Air.
        self.write("sys/firmware/devicetree/base/compatible", b"apple,j713\0apple,t8132\0apple,arm-platform\0")
        self.write("sys/devices/system/cpu/online", "0-9\n")
        for cpu in range(10):
            base = f"sys/devices/system/cpu/cpu{cpu}"
            self.write(base + "/of_node/compatible", b"apple,donan-p\0" if cpu in self.performance else b"apple,donan-e\0")
            self.write(base + "/of_node/reg", cpu.to_bytes(8, "big"))
            self.write(base + "/cpu_capacity", "1024\n")
        self.write("proc/sys/kernel/random/boot_id", "current-boot\n")
        self.process(789, 1343)
        self.environment = {"XDG_RUNTIME_DIR": "/run/user/1000", "HYPRLAND_INSTANCE_SIGNATURE": "instance", "WAYLAND_DISPLAY": "wayland-1"}
        self.state = self.root / "run/user/1000/m4-session-placement.json"
        self.state.parent.mkdir(parents=True)
        self.dropin = self.state.parent / placement.DROPIN_NAME
        self.masks = {789: set(range(10))}
        self.set_calls = []
        self.instances = [{"instance": "instance", "wl_socket": "wayland-1", "pid": 789}]
        self.commands = []
        self.addCleanup(patch.stopall)
        patch.object(placement.os, "getuid", return_value=1000).start()
        patch.object(placement.os, "sched_getaffinity", side_effect=lambda pid: self.masks[pid].copy()).start()
        patch.object(placement.os, "sched_setaffinity", side_effect=self.set_affinity).start()
        patch.object(placement, "command", side_effect=self.command).start()
        self.runtime_uid = 1000
        real_stat = Path.stat
        def owned_runtime(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path.is_relative_to(self.root / "run/user"):
                fields = list(info)
                fields[4] = self.runtime_uid
                return os.stat_result(fields)
            return info
        patch.object(Path, "stat", owned_runtime).start()

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else value.encode())
        return path

    def process(self, pid, start):
        self.write(f"proc/{pid}/status", f"Uid:\t1000\t1000\t1000\t1000\nTgid:\t{pid}\n")
        self.write(f"proc/{pid}/comm", "Hyprland\n")
        fields = ["0"] * 40
        fields[0], fields[19] = "S", str(start)
        self.write(f"proc/{pid}/stat", f"{pid} (Hyprland) " + " ".join(fields))
        path = self.root / f"proc/{pid}/exe"
        if not path.is_symlink():
            path.symlink_to("/opt/m4-hypr/bin/Hyprland")

    def command(self, *args):
        self.commands.append(args)
        if args[0] == "hyprctl":
            return json.dumps(self.instances)
        if args[0] == "busctl":
            return json.dumps({"type": "as", "data": ["/etc/systemd/user", "/run/user/1000/systemd/user", "/home/kb2uka/.config/systemd/user"]})
        return ""

    def set_affinity(self, pid, mask):
        self.set_calls.append((pid, set(mask)))
        self.masks[pid] = set(mask)

    def apply(self):
        return placement.apply(self.root, self.environment, self.state, self.dropin)

    def test_dynamic_cpu_mapping_main_only_dropin_and_restore(self):
        result = self.apply()
        self.assertEqual(result["performance"], [1, 3, 5, 9])
        self.assertEqual(self.set_calls, [(789, self.performance)])
        self.assertIn(b"CPUAffinity=\nCPUAffinity=1 3 5 9\n", self.dropin.read_bytes())
        self.assertEqual(json.loads(self.state.read_text())["original"], list(range(10)))
        reloads = self.commands.count(("systemctl", "--user", "daemon-reload"))
        placement.restore(self.root, self.state, self.dropin)
        self.assertEqual(self.commands.count(("systemctl", "--user", "daemon-reload")), reloads + 1)
        self.assertEqual(self.set_calls[-1], (789, set(range(10))))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.dropin.exists())

    def test_repeat_preserves_original_mask_and_makes_no_second_change(self):
        self.apply()
        before = self.state.read_bytes()
        self.apply()
        self.assertEqual(len(self.set_calls), 1)
        self.assertEqual(self.state.read_bytes(), before)
        placement.restore(self.root, self.state, self.dropin)
        self.assertEqual(self.masks[789], set(range(10)))

    def test_hardware_mismatch_precedes_all_writes(self):
        cases = [("sys/firmware/devicetree/base/compatible", b"apple,j714\0apple,t8132\0"),
                 ("sys/firmware/devicetree/base/compatible", b"apple,j713\0apple,t8103\0"),
                 ("sys/devices/system/cpu/online", b"0-8"),
                 ("sys/devices/system/cpu/cpu1/cpu_capacity", b"2048"),
                 ("sys/devices/system/cpu/cpu1/of_node/compatible", b"unknown\0"),
                 ("sys/devices/system/cpu/cpu1/of_node/reg", bytes(8))]
        for name, value in cases:
            with self.subTest(name=name):
                path = self.root / name
                before = path.read_bytes()
                path.write_bytes(value)
                with self.assertRaises(ValueError):
                    self.apply()
                path.write_bytes(before)
                self.assertEqual(self.set_calls, [])
                self.assertFalse(self.state.exists())
                self.assertFalse(self.dropin.exists())

    def test_wrong_session_and_launcher_are_rejected(self):
        for instances in ([], self.instances * 2, [{**self.instances[0], "instance": "other"}],
                          [{**self.instances[0], "wl_socket": "wayland-other"}]):
            def query(*args):
                return json.dumps(instances) if args[0] == "hyprctl" else self.command(*args)
            with patch.object(placement, "command", side_effect=query):
                with self.assertRaises(ValueError):
                    self.apply()
        self.write("proc/789/comm", "start-hyprland\n")
        with self.assertRaises(ValueError):
            self.apply()
        self.write("proc/789/comm", "Hyprland\n")
        exe = self.root / "proc/789/exe"
        exe.unlink()
        exe.symlink_to("/usr/bin/bash")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.set_calls, [])
        self.assertFalse(self.state.exists())

    def test_other_user_and_non_main_tid_are_rejected(self):
        for status in ("Uid:\t1001\t1001\t1001\t1001\nTgid:\t789\n", "Uid:\t1000\t1000\t1000\t1000\nTgid:\t784\n"):
            self.write("proc/789/status", status)
            with self.assertRaises(ValueError):
                self.apply()
        self.assertEqual(self.set_calls, [])

    def test_preserves_existing_user_configuration(self):
        self.dropin.parent.mkdir(parents=True)
        self.dropin.write_bytes(b"custom\n")
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.dropin.read_bytes(), b"custom\n")
        self.dropin.unlink()
        self.masks[789] = {2, 4}
        with self.assertRaises(ValueError):
            self.apply()
        self.assertEqual(self.set_calls, [])

    def test_other_applicable_affinity_dropins_are_preserved(self):
        cases = ["etc/systemd/user/service.d/20-custom.conf",
                 "etc/systemd/user/omarchy-.service.d/20-custom.conf",
                 "home/kb2uka/.config/systemd/user/omarchy-browser-.service.d/10-custom.conf",
                 "home/kb2uka/.config/systemd/user/omarchy-browser-.service.d/70-m4-placement.conf"]
        for name in cases:
            with self.subTest(name=name):
                path = self.write(name, "[Service]\n CPUAffinity = 2 4\n")
                try:
                    with self.assertRaisesRegex(ValueError, "affinity configuration"):
                        self.apply()
                    self.assertFalse(self.state.exists())
                    self.assertEqual(path.read_text(), "[Service]\n CPUAffinity = 2 4\n")
                    self.assertEqual(self.masks[789], set(range(10)))
                finally:
                    if self.state.exists():
                        placement.restore(self.root, self.state, self.dropin)
                    path.unlink()

    def test_reload_failure_removes_candidate_and_keeps_original_mask(self):
        def command(*args):
            if args[0] == "hyprctl":
                return json.dumps(self.instances)
            if args[0] == "busctl":
                return json.dumps({"type": "as", "data": ["/etc/systemd/user"]})
            if not self.commands:
                self.commands.append(args)
                raise RuntimeError("reload failed")
            return ""
        with patch.object(placement, "command", side_effect=command):
            with self.assertRaisesRegex(RuntimeError, "reload failed"):
                self.apply()
        self.assertEqual(self.masks[789], set(range(10)))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.dropin.exists())

    def test_same_name_dropin_cannot_shadow_runtime_placement(self):
        name = "home/kb2uka/.config/systemd/user/omarchy-browser-.service.d/70-m4-placement.conf"
        path = self.root / name
        path.parent.mkdir(parents=True)
        for content in ("[Service]\nNice=5\n", "", None):
            with self.subTest(content=content):
                if content is None:
                    path.symlink_to("/dev/null")
                else:
                    path.write_text(content)
                try:
                    with self.assertRaisesRegex(ValueError, "affinity configuration"):
                        self.apply()
                    self.assertFalse(self.state.exists())
                    self.assertFalse(self.dropin.exists())
                    self.assertEqual(self.set_calls, [])
                    if content is None:
                        self.assertEqual(path.readlink(), Path("/dev/null"))
                    else:
                        self.assertEqual(path.read_text(), content)
                finally:
                    path.unlink()

    def test_affinity_scan_ignores_comments_but_rejects_unreadable_config(self):
        path = self.write("etc/systemd/user/service.d/20-custom.conf", "# CPUAffinity=2\n; CPUAffinity=4\n[Service]\nNice=5\n")
        self.apply()
        placement.restore(self.root, self.state, self.dropin)
        read_text = Path.read_text
        def read_file(candidate, *args, **kwargs):
            if candidate == path:
                raise PermissionError("cannot inspect custom config")
            return read_text(candidate, *args, **kwargs)
        with patch.object(Path, "read_text", read_file):
            with self.assertRaises(PermissionError):
                self.apply()
        self.assertFalse(self.state.exists())

    def test_reused_pid_is_never_restored(self):
        self.apply()
        self.process(789, 9999)
        calls = self.set_calls.copy()
        result = placement.restore(self.root, self.state, self.dropin)
        self.assertFalse(result["identity_still_current"])
        self.assertEqual(self.set_calls, calls)
        self.assertFalse(self.dropin.exists())

    def test_external_change_is_not_silently_overwritten(self):
        self.apply()
        self.masks[789] = {0, 2}
        with self.assertRaises(ValueError):
            self.apply()
        with self.assertRaises(ValueError):
            placement.restore(self.root, self.state, self.dropin)
        self.assertEqual(self.masks[789], {0, 2})
        self.assertTrue(self.state.exists())

    def test_restore_keeps_custom_dropin_but_restores_own_main_mask(self):
        self.apply()
        self.dropin.write_bytes(b"custom\n")
        with self.assertRaisesRegex(ValueError, "Saved placement was changed"):
            self.apply()
        with self.assertRaisesRegex(ValueError, "file left unchanged"):
            placement.restore(self.root, self.state, self.dropin)
        self.assertEqual(self.masks[789], set(range(10)))
        self.assertEqual(self.dropin.read_bytes(), b"custom\n")
        self.assertTrue(self.state.exists())
        self.dropin.unlink()
        placement.restore(self.root, self.state, self.dropin)
        self.assertFalse(self.state.exists())

    def test_next_session_clears_stale_state_without_touching_reused_pid(self):
        self.apply()
        self.process(789, 9999)
        self.process(901, 15000)
        self.masks[901] = set(range(10))
        self.instances[0]["pid"] = 901
        self.apply()
        self.assertEqual(self.set_calls, [(789, self.performance), (901, self.performance)])
        self.assertEqual(json.loads(self.state.read_text())["identity"]["pid"], 901)

    def test_runtime_symlinks_and_root_invocation_are_rejected(self):
        self.assertEqual(placement.paths(self.root, self.environment), (self.state, self.dropin))
        other = self.root / "run/user/1001"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "without sudo"):
            placement.paths(self.root, {**self.environment, "XDG_RUNTIME_DIR": "/run/user/1001"})
        for path in (self.state, self.state.with_suffix(".lock"), self.dropin.parent):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.symlink_to(self.root / "elsewhere")
            with self.assertRaisesRegex(ValueError, "Symlink"):
                placement.paths(self.root, self.environment)
            path.unlink()
        with patch.object(placement.os, "getuid", return_value=0):
            with self.assertRaises(ValueError):
                placement.paths(self.root, {**self.environment, "XDG_RUNTIME_DIR": "/run/user/0"})

    def test_runtime_owned_by_another_user_is_rejected(self):
        self.runtime_uid = 1001
        with self.assertRaisesRegex(ValueError, "Unexpected owner runtime directory"):
            placement.paths(self.root, self.environment)

    def test_live_nested_session_refusal_preserves_outer(self):
        self.apply()
        saved, dropin = self.state.read_bytes(), self.dropin.read_bytes()
        calls = self.set_calls.copy()
        self.process(901, 15000)
        self.masks[901] = {2, 4}
        self.instances[0] = {"instance": "nested", "wl_socket": "wayland-2", "pid": 901}
        self.environment.update(HYPRLAND_INSTANCE_SIGNATURE="nested", WAYLAND_DISPLAY="wayland-2")
        with self.assertRaisesRegex(ValueError, "Another live compositor"):
            self.apply()
        self.assertEqual(self.masks[789], self.performance)
        self.assertEqual(self.masks[901], {2, 4})
        self.assertEqual(self.set_calls, calls)
        self.assertEqual(self.state.read_bytes(), saved)
        self.assertEqual(self.dropin.read_bytes(), dropin)

    def test_deleted_executable_same_process_restores_original(self):
        self.apply()
        exe = self.root / "proc/789/exe"
        exe.unlink()
        exe.symlink_to("/opt/m4-hypr/bin/Hyprland (deleted)")
        result = placement.restore(self.root, self.state, self.dropin)
        self.assertTrue(result["identity_still_current"])
        self.assertEqual(self.masks[789], set(range(10)))
        self.assertFalse(self.state.exists())
        self.assertFalse(self.dropin.exists())

    def test_changed_executable_same_pid_is_not_restored(self):
        self.apply()
        calls = self.set_calls.copy()
        exe = self.root / "proc/789/exe"
        exe.unlink()
        exe.symlink_to("/different-build/Hyprland")
        result = placement.restore(self.root, self.state, self.dropin)
        self.assertFalse(result["identity_still_current"])
        self.assertEqual(self.set_calls, calls)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.dropin.exists())

    def test_unreadable_exe_reused_pid_cleans_up_but_current_pid_preserves_state(self):
        self.apply()
        saved, dropin = self.state.read_bytes(), self.dropin.read_bytes()
        calls = self.set_calls.copy()
        readlink = placement.os.readlink
        def denied(path, *args, **kwargs):
            if Path(path) == self.root / "proc/789/exe":
                raise PermissionError("nondumpable process")
            return readlink(path, *args, **kwargs)
        with patch.object(placement.os, "readlink", side_effect=denied):
            with self.assertRaises(PermissionError):
                placement.restore(self.root, self.state, self.dropin)
            self.assertEqual(self.state.read_bytes(), saved)
            self.assertEqual(self.dropin.read_bytes(), dropin)
            self.process(789, 9999)
            result = placement.restore(self.root, self.state, self.dropin)
        self.assertFalse(result["identity_still_current"])
        self.assertEqual(self.set_calls, calls)
        self.assertFalse(self.state.exists())
        self.assertFalse(self.dropin.exists())

    def test_changed_process_after_reload_is_never_placed(self):
        reload_dropins = []
        def changed(*args):
            result = self.command(*args)
            if args == ("systemctl", "--user", "daemon-reload"):
                reload_dropins.append(self.dropin.exists())
                self.process(789, 9999)
            return result
        with patch.object(placement, "command", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Compositor changed"):
                self.apply()
        self.assertEqual(self.set_calls, [])
        self.assertFalse(self.state.exists())
        self.assertFalse(self.dropin.exists())
        self.assertEqual(self.commands.count(("systemctl", "--user", "daemon-reload")), 2)
        self.assertEqual(reload_dropins, [True, False])

    def test_concurrent_state_creation_is_not_overwritten(self):
        original_open = Path.open
        def raced(path, mode="r", *args, **kwargs):
            if path == self.state:
                with original_open(path, "w") as stream:
                    stream.write("created elsewhere")
            return original_open(path, mode, *args, **kwargs)
        with patch.object(Path, "open", raced):
            with self.assertRaises(FileExistsError):
                self.apply()
        self.assertEqual(self.state.read_text(), "created elsewhere")
        self.assertEqual(self.set_calls, [])
        self.assertFalse(self.dropin.exists())

    def test_cli_dispatches_each_action_under_exclusive_lock(self):
        self.state.write_text("{}")
        self.state.with_suffix(".lock").touch()
        real_read = Path.read_text
        dispatched = []
        def locked(action):
            with self.state.with_suffix(".lock").open("r") as contender:
                with self.assertRaises(BlockingIOError, msg="Action dispatched without an exclusive lock"):
                    placement.fcntl.flock(contender, placement.fcntl.LOCK_SH | placement.fcntl.LOCK_NB)
            dispatched.append(action)
            return {}
        def read(path, *args, **kwargs):
            if path == self.state:
                locked("status")
            return real_read(path, *args, **kwargs)
        for action in ("apply", "restore", "status"):
            with self.subTest(action=action), \
                 patch.object(sys, "argv", ["placement", action]), \
                 patch.object(placement, "paths", return_value=(self.state, self.dropin)), \
                 patch.object(placement, "apply", side_effect=lambda *args: locked("apply")), \
                 patch.object(placement, "restore", side_effect=lambda *args: locked("restore")), \
                 patch.object(Path, "read_text", read), redirect_stdout(io.StringIO()):
                placement.main()
            with self.state.with_suffix(".lock").open("r") as contender:
                placement.fcntl.flock(contender, placement.fcntl.LOCK_EX | placement.fcntl.LOCK_NB)
        self.assertEqual(dispatched, ["apply", "restore", "status"])


class CommandTests(unittest.TestCase):
    def test_stalled_command_is_bounded_and_timeout_propagates(self):
        def stalled(args, *, text, timeout=None):
            self.assertTrue(text)
            self.assertIsNotNone(timeout, "External command has no deadline")
            self.assertGreater(timeout, 0)
            self.assertLessEqual(timeout, 10)
            raise placement.subprocess.TimeoutExpired(args, timeout)
        with patch.object(placement.subprocess, "check_output", side_effect=stalled):
            with self.assertRaises(placement.subprocess.TimeoutExpired):
                placement.command("busctl", "--user")


if __name__ == "__main__":
    unittest.main()
