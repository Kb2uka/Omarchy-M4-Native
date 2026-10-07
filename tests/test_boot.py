#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Exercise the host launcher with a harmless loader, never a hardware proxy."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / "tools" / "boot.sh"


class BootLauncherTests(unittest.TestCase):
    def setUp(self):
        self.bash = shutil.which("bash")
        self.tmp = tempfile.TemporaryDirectory(prefix="boot '\n $ bench ")
        self.addCleanup(self.tmp.cleanup)
        self.kit = Path(self.tmp.name)
        proxy = self.kit / "m1n1" / "proxyclient" / "tools"
        proxy.mkdir(parents=True)
        (proxy / "linux.py").write_text(
            "import json, os, sys\n"
            "print('LOADER=' + json.dumps({'args':sys.argv[1:], "
            "'device':os.environ.get('M1N1DEVICE'), 'tty':os.isatty(0)}), flush=True)\n"
            "sys.exit(int(os.environ.get('FAKE_LOADER_EXIT', '0')))\n"
        )
        for name in ("Image-m4hacks-smc-nvme.gz", "initramfs.cpio.gz", "control ' $.dtb"):
            (self.kit / name).write_bytes(name.encode())
        self.dtb = self.kit / "control ' $.dtb"
        self.env = dict(os.environ, BOOT_KIT=str(self.kit),
                        M1N1DEVICE="/dev/null", PYTHON=sys.executable)

    def run_boot(self, *args):
        return subprocess.run([self.bash, str(LAUNCHER), *args], env=self.env,
                              cwd=self.kit, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=20)

    def test_paths_device_pty_and_log(self):
        result = self.run_boot(str(self.dtb), "control")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        logs = list((self.kit / "logs").glob("boot-control-*.log.*"))
        self.assertEqual(len(logs), 1)
        log = logs[0].read_text()
        self.assertIn("LOADER=", log, repr(log))
        # BSD script may echo an EOF marker before the first child output.
        data = json.loads(log.split("LOADER=", 1)[1].splitlines()[0])
        self.assertTrue(data["tty"])
        self.assertEqual(data["device"], "/dev/null")
        self.assertEqual(data["args"], ["-b", "nosmp idle=nop panic=0 console=tty0 fbcon=nodefer loglevel=7",
            str(self.kit / "Image-m4hacks-smc-nvme.gz"), str(self.dtb),
            str(self.kit / "initramfs.cpio.gz")])
        self.assertIn(hashlib.sha256(self.dtb.read_bytes()).hexdigest(), log)
        self.assertIn("loader_exit=0", log)
        self.assertIn("not a Linux boot verdict", log)

    def test_relative_device_resolved_before_chdir(self):
        device = self.kit / "proxy-device"
        device.symlink_to("/dev/null")
        self.env["M1N1DEVICE"] = "proxy-device"
        result = self.run_boot(str(self.dtb), "relative-device")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        log = next((self.kit / "logs").glob("*.log.*")).read_text()
        data = json.loads(log.split("LOADER=", 1)[1].splitlines()[0])
        self.assertTrue(Path(data["device"]).is_absolute())
        self.assertTrue(os.path.samefile(data["device"], device))

    def test_loader_failure_preserved(self):
        self.env["FAKE_LOADER_EXIT"] = "9"
        result = self.run_boot(str(self.dtb), "failure")
        self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
        self.assertIn("loader_exit=9", next((self.kit / "logs").glob("*.log.*")).read_text())

    def test_explicit_device_required(self):
        self.env.pop("M1N1DEVICE")
        result = self.run_boot(str(self.dtb), "missing-device")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("M1N1DEVICE", result.stderr)
        self.assertNotIn("LOADER=", result.stdout)

    def test_missing_initramfs_rejected_before_loader(self):
        (self.kit / "initramfs.cpio.gz").unlink()
        result = self.run_boot(str(self.dtb), "missing-initrd")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("initramfs", result.stderr)
        self.assertNotIn("LOADER=", result.stdout)

    def test_tag_cannot_escape_log_directory(self):
        result = self.run_boot(str(self.dtb), "../escape")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tag", result.stderr)
        self.assertNotIn("LOADER=", result.stdout)

    def test_custom_kernel_and_arguments(self):
        kernel = self.kit / "fallback ' $.gz"
        kernel.write_bytes(b"fallback")
        self.env["BOOT_ARGS"] = "nosmp panic=0 initcall_debug"
        result = self.run_boot(str(self.dtb), "fallback", str(kernel))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout.split("LOADER=", 1)[1].splitlines()[0])
        self.assertEqual(data["args"][2], str(kernel))
        self.assertEqual(data["args"][1], self.env["BOOT_ARGS"])

    def test_same_second_logs_are_unique(self):
        commands = self.kit / "commands"
        commands.mkdir()
        date = commands / "date"
        date.write_text("#!/bin/sh\nprintf '%s\\n' 20261006T230000Z\n")
        date.chmod(0o755)
        self.env["PATH"] = str(commands) + os.pathsep + self.env["PATH"]
        for _ in range(2):
            result = self.run_boot(str(self.dtb), "same-second")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list((self.kit / "logs").glob("boot-same-second-*"))), 2)

    def test_regular_file_is_not_a_proxy(self):
        self.env["M1N1DEVICE"] = str(self.dtb)
        result = self.run_boot(str(self.dtb), "regular-device")
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("LOADER=", result.stdout)

    @unittest.skipUnless(sys.platform.startswith("linux"), "util-linux shell selection")
    def test_invalid_parent_shell_does_not_break_loader(self):
        self.env["SHELL"] = "/bin/false"
        result = self.run_boot(str(self.dtb), "shell")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LOADER=", result.stdout)

    def test_cdpath_does_not_contaminate_resolved_paths(self):
        (self.kit / "kit-link").symlink_to(self.kit, target_is_directory=True)
        self.env["BOOT_KIT"] = "kit-link"
        self.env["CDPATH"] = "."
        result = self.run_boot(str(self.dtb), "cdpath")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("LOADER=", result.stdout)

    def test_missing_script_has_diagnostic(self):
        commands = self.kit / "empty-path"
        commands.mkdir()
        self.env["PATH"] = str(commands)
        result = self.run_boot(str(self.dtb), "missing-script")
        self.assertEqual(result.returncode, 2)
        self.assertIn("required command not found: script", result.stderr)

    def test_leading_dash_input_filename(self):
        target = self.kit / "-dtb"
        target.write_bytes(self.dtb.read_bytes())
        result = self.run_boot("-dtb", "leading-dash")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        data = json.loads(result.stdout.split("LOADER=", 1)[1].splitlines()[0])
        self.assertTrue(Path(data["args"][3]).is_absolute())
        self.assertTrue(os.path.samefile(data["args"][3], target))

    @unittest.skipUnless(sys.platform.startswith("linux"), "util-linux shell selection")
    def test_recorder_uses_the_invoked_bash(self):
        alternate = self.kit / "alternate-bash"
        alternate.symlink_to(self.bash)
        self.bash = str(alternate)
        commands = self.kit / "commands"
        commands.mkdir()
        recorder = commands / "script"
        real_script = shutil.which("script")
        recorder.write_text(
            "#!" + sys.executable + "\n"
            "import json, os, sys\n"
            "print('RECORDER_SHELL=' + json.dumps(os.environ.get('SHELL')), flush=True)\n"
            "os.execv(" + repr(real_script) + ", [" + repr(real_script) + "] + sys.argv[1:])\n"
        )
        recorder.chmod(0o755)
        self.env["PATH"] = str(commands) + os.pathsep + self.env["PATH"]
        result = self.run_boot(str(self.dtb), "alternate-shell")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        selected = json.loads(result.stdout.split("RECORDER_SHELL=", 1)[1].splitlines()[0])
        self.assertEqual(selected, str(alternate))

    def test_missing_interpreter_has_diagnostic(self):
        self.env["PYTHON"] = str(self.kit / "missing-python")
        result = self.run_boot(str(self.dtb), "missing-python")
        self.assertEqual(result.returncode, 2)
        self.assertIn("missing-python", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
