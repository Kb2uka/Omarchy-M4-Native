# SPDX-License-Identifier: MIT
"""Exercise the real package recipe in a scratch root, never the installed host."""
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_j715_display_profile import ROOT, environment, load

BUILD = load("j715_package_build", "tools/build-m4-packages.py")
CHECK = load("j715_package_check", "tools/check-m4-packages.py")
NAME = "omarchy-m4-backlight"
BASE = "usr/lib/omarchy-m4/backlight/j715/"


class BacklightPackage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.scratch = Path(temporary.name)
        cls.stage, cls.root = cls.scratch / "stage", cls.scratch / "root"
        shutil.copytree(ROOT / "packaging/omarchy-m4" / NAME, cls.stage)
        for name, source in BUILD.STAGED[NAME].items():
            shutil.copyfile(ROOT / source, cls.stage / name)
        # Only the existing J713 binary is synthetic here. This test exercises
        # package() copying and policy, not binary publication or makepkg fetches.
        (cls.stage / "j713_dcp_backlight.ko").write_bytes(b"synthetic package-only module")
        cls.env = environment()
        cls.env["pkgdir"] = str(cls.root)
        subprocess.run(["bash", "-c", 'source ./PKGBUILD; package'], cwd=cls.stage,
                       env=cls.env, check=True, capture_output=True, text=True)
        cls.channel = json.loads((ROOT / "packaging/omarchy-m4/channel.json").read_bytes())

    def test_all_local_source_pins_match_real_staged_bytes(self):
        result = subprocess.run(
            ["bash", "-c", 'source ./PKGBUILD; printf "%s\\n" "${source[@]}"; '
             'printf -- "---\\n"; printf "%s\\n" "${sha256sums[@]}"'],
            cwd=self.stage, env=self.env, check=True, capture_output=True, text=True)
        names, hashes = result.stdout.split("---\n")
        names, hashes = names.splitlines(), hashes.splitlines()
        self.assertEqual(len(names), len(hashes))
        for name, digest in zip(names, hashes):
            if "::https://" not in name:
                with self.subTest(name=name):
                    self.assertEqual(hashlib.sha256((self.stage / name).read_bytes()).hexdigest(), digest)

    def test_installed_layout_imports_and_board_gate_work_without_host_writes(self):
        helper = self.root / BASE / "tools/m4-j715-backlight.py"
        compatible = self.root / "sys/firmware/devicetree/base/compatible"
        compatible.parent.mkdir(parents=True)
        for board, code in ((b"apple,j713\0apple,t8132\0", 1),
                            (b"apple,j715\0apple,t8112\0", 1),
                            (b"apple,j715\0apple,j713\0apple,t8132\0", 1),
                            (b"apple,j715\0apple,t8132\0", 0)):
            compatible.write_bytes(board)
            result = subprocess.run([sys.executable, "-B", str(helper), "--supported",
                                     "--root", str(self.root)], env=self.env, capture_output=True,
                                    text=True, check=False)
            self.assertEqual(result.returncode, code, result.stderr)
        self.assertFalse((self.root / "var/lib/omarchy-m4").exists())
        # The installed validator finds its schema under the packaged root.
        sys.path.insert(0, str(helper.parent))
        self.addCleanup(sys.path.remove, str(helper.parent))
        profile = load("j715_installed_profile", str(self.root / BASE / "tools/j715_display_profile.py"))
        self.assertTrue(profile.parse(ROOT / "tests/fixtures/j715-display-synthetic.json").synthetic)

    def test_installed_paths_modes_and_bytes_satisfy_channel_policy(self):
        for path in self.root.rglob("*"):
            if not path.is_file() or not path.relative_to(self.root).as_posix().startswith("usr/"):
                continue
            name = path.relative_to(self.root).as_posix()
            with self.subTest(path=name):
                self.assertTrue(CHECK.allowed(name, self.channel["allowed_paths"][NAME]))
                self.assertTrue(not CHECK.allowed(name, self.channel["protected_paths"]) or
                                name in self.channel["protected_exceptions"])
                self.assertEqual(stat.S_IMODE(path.stat().st_mode) & 0o7022, 0)
        self.assertEqual((self.root / "usr/lib/udev/rules.d/90-j715-backlight.rules").read_bytes(),
                         (ROOT / "packaging/j715-backlight/90-j715-backlight.rules").read_bytes())
        for helper in ("m4-j715-backlight.py", "m4-backlight.py", "j715_sources.py", "j715_display_profile.py"):
            self.assertEqual((self.root / BASE / "tools" / helper).read_bytes(),
                             (ROOT / "tools" / helper).read_bytes())
        self.assertFalse((self.root / BASE / "profile.json").exists())
        self.assertFalse((self.root / BASE / "j715_dcp_backlight.ko").exists())

    def test_service_gates_assets_before_activation_and_keys(self):
        unit = self.root / "usr/lib/systemd/system/m4-j715-backlight.service"
        directives = {}
        for line in unit.read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                directives.setdefault(key, []).append(value)
        self.assertEqual(set(directives["ConditionPathExists"]), {
            "/" + BASE + name for name in ("j715_dcp_backlight.ko", "module.json", "profile.json")})
        self.assertEqual(directives["ExecCondition"], ["/usr/bin/m4-j715-backlight --supported"])
        self.assertEqual(directives["ExecStart"], ["/usr/bin/m4-j715-backlight --boot"])
        self.assertEqual(directives["ExecStartPost"], ["/usr/bin/m4-j715-backlight --keys-hook"])
        self.assertEqual(directives["TimeoutStartSec"], ["30"])

    def test_package_hook_enables_j715_service_only_after_board_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "usr/bin").mkdir(parents=True)
            python = root / "usr/bin/python3"
            python.write_text('#!/bin/sh\nfor arg do\n'
                              '  if [ "$arg" = --supported ]; then exit "$SUPPORTED"; fi\n'
                              'done\nexit 0\n')
            python.chmod(0o755)
            systemctl = root / "usr/bin/systemctl"
            systemctl.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HOOK_LOG"\n')
            systemctl.chmod(0o755)
            log = root / "hook.log"
            env = dict(self.env, PATH=str(root / "usr/bin") + os.pathsep + os.environ["PATH"],
                       HOOK_LOG=str(log))
            hook = ROOT / "packaging/omarchy-m4/omarchy-m4-backlight/omarchy-m4-backlight.install"
            for supported in ("0", "1"):
                env["SUPPORTED"] = supported
                log.write_text("")
                subprocess.run(["bash", "-c", 'source "$1"; post_install', "hook", str(hook)],
                               cwd=root, env=env, capture_output=True, text=True, check=True)
                calls = log.read_text().splitlines()
                self.assertIn("enable m4-backlight.service", calls)
                self.assertEqual("enable m4-j715-backlight.service" in calls, supported == "0")

    def test_channel_checker_rejects_missing_or_substituted_j715_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "package"
            shutil.copytree(self.root / "usr", root / "usr")
            module = root / "usr/lib/omarchy-m4/backlight/j713_dcp_backlight.ko"
            pins = root / "usr/lib/omarchy-m4/backlight/module.json"
            digest = hashlib.sha256(module.read_bytes()).hexdigest()
            channel = json.loads(json.dumps(self.channel))
            channel["backlight_module"] = {module.relative_to(root).as_posix(): digest}
            pins.write_text(json.dumps({"kernel_release": channel["kernel"]["release"],
                                        "module_bytes": module.stat().st_size, "module_sha256": digest}))
            files = {p.relative_to(root).as_posix(): (stat.S_IMODE(p.stat().st_mode),
                     hashlib.sha256(p.read_bytes()).hexdigest()) for p in root.rglob("*") if p.is_file()}
            info = {"pkgname": [NAME], "pkgver": ["1-2"], "arch": ["aarch64"]}
            package = Path(NAME + "-1-2-aarch64.pkg.tar.zst")
            CHECK.check_package(channel, package, info, files, root)
            for path in ("usr/lib/udev/rules.d/90-j715-backlight.rules",
                         "usr/lib/systemd/system/m4-j715-backlight.service",
                         BASE + "tools/m4-j715-backlight.py",
                         BASE + "display/j715/profile.schema.json"):
                for replacement in (None, (0o644, "0" * 64)):
                    with self.subTest(path=path, replacement=replacement):
                        candidate = dict(files)
                        if replacement is None:
                            del candidate[path]
                        else:
                            candidate[path] = replacement
                        with self.assertRaises(ValueError):
                            CHECK.check_package(channel, package, info, candidate, root)

    def test_udev_rule_targets_only_native_panel_brightness(self):
        rule = (self.root / "usr/lib/udev/rules.d/90-j715-backlight.rules").read_text()
        active = [line for line in rule.splitlines() if line and not line.startswith("#")]
        self.assertEqual(len(active), 1)
        fields = [part.strip() for part in active[0].split(", ")]
        self.assertEqual(fields, ['ACTION=="add|change"', 'SUBSYSTEM=="backlight"', 'KERNEL=="j715-dcp"',
                                 'RUN+="/usr/bin/chgrp video /sys%p/brightness"',
                                 'RUN+="/usr/bin/chmod 0664 /sys%p/brightness"'])


if __name__ == "__main__":
    unittest.main()
