# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "tools/configure-m4-desktop.py"
spec = importlib.util.spec_from_file_location("m4_desktop_config", SOURCE)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopImageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="m4 desktop image ")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / "image"
        self.root.mkdir()
        self.stock = ('-- Packaged entrypoint fixture\n'
                      'require("default.hypr.omarchy")\n'
                      'require("hypr.bindings")\n'
                      'require("future.packaged.defaults")\n')
        self.write("etc/m4-root-id", "61924e2a-307d-4f34-9b04-4eef87d46729\n")
        self.write("var/lib/omarchy/provisioning/pending", "")
        self.write("usr/share/omarchy/config/hypr/hyprland.lua", self.stock)
        self.chromium = (
            "--ozone-platform=wayland\n"
            "--ozone-platform-hint=wayland\n"
            "--password-store=gnome-libsecret\n"
            "--enable-features=TouchpadOverscrollHistoryNavigation\n"
            "--load-extension=/usr/share/omarchy/default/chromium/extensions/copy-url,"
            "/usr/share/omarchy/default/chromium/extensions/yt-dlp,"
            "/usr/share/omarchy/default/chromium/extensions/whatsapp-slim\n")
        self.write("usr/share/omarchy/config/chromium-flags.conf", self.chromium)
        self.write("usr/share/omarchy/install/omarchy-base.packages", "# Core desktop\nbtop\nnvim\ndotnet-runtime\n")
        self.write("var/lib/pacman/local/btop-1-1/desc", "%NAME%\nbtop\n")
        self.write("var/lib/pacman/local/neovim-1-1/desc", "%NAME%\nneovim\n\n%PROVIDES%\nnvim\nvim-plugin-runtime\n")
        self.write("var/lib/pacman/local/dotnet-runtime-bin-1-1/desc", "%NAME%\ndotnet-runtime-bin\n\n%PROVIDES%\ndotnet-runtime=10.0.12\n\n%ARCH%\naarch64\n")
        self.write("etc/skel/.config/hypr/hyprland.lua", 'require("default.hypr.bindings.tiling")\n')
        self.write("etc/locale.gen", "#en_US.UTF-8 UTF-8  \n#de_DE.UTF-8 UTF-8\n")
        self.write("etc/locale.conf", "LANG=C\n")
        self.zram = "[zram0]\nzram-size = ram\ncompression-algorithm = zstd\n"
        self.write("usr/share/omarchy/default/systemd/zram-generator.conf.d/90-omarchy.conf", self.zram)
        self.shell = {"version": 1, "idle": {"lock": 300}, "plugins": [],
                      "bar": {"layout": {"left": [{"id": "omarchy.menu"}],
                                         "center": [{"id": "omarchy.clock"}],
                                         "right": [{"id": "omarchy.agents"}, {"id": "omarchy.network"}]}}}
        self.write("usr/share/omarchy/config/omarchy/shell.json", json.dumps(self.shell))
        self.write("usr/share/omarchy/shell/plugins/agents/manifest.json", json.dumps({
            "id": "omarchy.agents", "name": "Agents", "license": "MIT",
            "entryPoints": {"barWidget": "Panel.qml"}}))
        self.write("usr/share/omarchy/shell/plugins/agents/Panel.qml",
                   "Panel {\n  visible: providers.length > 0\n  // Other behavior stays intact.\n}\n")
        self.write("usr/share/omarchy/shell/plugins/agents/Main.qml", "Item {}\n")
        self.write("usr/share/omarchy/shell/plugins/agents/assets/mark.svg", "<svg/>\n")
        self.packages = ("omarchy", "omarchy-settings", "hyprland", "sudo", "base-devel",
                         "autoconf-archive", "gst-plugin-pipewire", "gst-plugins-good", "pipewire-alsa",
                         "pipewire-jack", "pipewire-pulse", "zram-generator", "htop")
        for name in self.packages:
            self.write(f"var/lib/pacman/local/{name}-1-1/desc", f"%NAME%\n{name}\n\n%VERSION%\n1-1\n")
        if os.name != "nt":
            (self.root / "etc").chmod(0o755)

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
        return path

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def symlink(self, path, target, directory=False):
        try:
            path.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows requires permission to create test symlinks")
            raise

    def test_reduced_entrypoint_fails_before_repair_then_stock_defaults_survive(self):
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "configuration differs"):
            desktop.configure(self.root)
        self.assertEqual(self.snapshot(), before)
        desktop.configure(self.root, apply=True)
        config = (self.root / "etc/skel/.config/hypr/hyprland.lua").read_text()
        self.assertTrue(config.startswith(self.stock))
        self.assertIn("uwsm finalize HYPRLAND_INSTANCE_SIGNATURE OMARCHY_PATH", config)
        self.assertIn("no_hardware_cursors = true", config)
        self.assertNotIn("m4-user-session", config)
        self.assertEqual((self.root / "etc/locale.conf").read_text(), "LANG=en_US.UTF-8\n")
        locale = (self.root / "etc/locale.gen").read_text()
        self.assertIn("\nen_US.UTF-8 UTF-8\n", "\n" + locale)
        self.assertIn("#de_DE.UTF-8 UTF-8", locale)
        self.assertEqual((self.root / "etc/systemd/zram-generator.conf.d/90-omarchy.conf").read_text(), self.zram)
        desktop.configure(self.root)

    def test_shader_cache_override_is_installed_and_checked(self):
        desktop.configure(self.root, apply=True)
        override = self.root / "etc/xdg/uwsm/env.d/90-m4-shader-cache"
        self.assertEqual(override.read_bytes(), b"export MESA_SHADER_CACHE_DISABLE=false\n")

        override.write_bytes(b"export MESA_SHADER_CACHE_DISABLE=true\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "etc/xdg/uwsm/env.d/90-m4-shader-cache"):
            desktop.configure(self.root)
        self.assertEqual(self.snapshot(), before)
        report = desktop.configure(self.root, apply=True)
        self.assertEqual(report["changed_files"], ["etc/xdg/uwsm/env.d/90-m4-shader-cache"])
        self.assertEqual(override.read_bytes(), b"export MESA_SHADER_CACHE_DISABLE=false\n")

    def test_chromium_default_preserves_stock_and_existing_owner(self):
        owner = self.write("home/owner/.config/chromium-flags.conf", "--owner-choice\n")
        desktop.configure(self.root, apply=True)
        target = self.root / "etc/skel/.config/chromium-flags.conf"
        expected = self.chromium + "--disable-features=OnBeginFrameThrottleVideo\n"
        self.assertEqual(target.read_text(), expected)
        self.assertEqual(owner.read_text(), "--owner-choice\n")
        self.assertEqual((self.root / "usr/share/omarchy/config/chromium-flags.conf").read_text(), self.chromium)
        desktop.configure(self.root)
        self.assertEqual(desktop.configure(self.root, apply=True)["changed_files"], [])
        target.write_text(self.chromium)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "chromium-flags.conf"):
            desktop.configure(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_chromium_preserves_comments_and_unrelated_feature_text(self):
        for line in ("", "   ", "# --enable-features=Ignored", "  # --enable-features=Ignored", "--user-agent=M4--disable-features=Literal"):
            with self.subTest(line=line):
                stock = self.chromium + line + "\n"
                self.write("usr/share/omarchy/config/chromium-flags.conf", stock)
                desktop.configure(self.root, apply=True)
                actual = (self.root / "etc/skel/.config/chromium-flags.conf").read_text()
                self.assertEqual(actual, stock + "--disable-features=OnBeginFrameThrottleVideo\n")

    def test_chromium_preserves_ascii_option_padding(self):
        stock = self.chromium + "  --user-agent=M4  \n"
        self.write("usr/share/omarchy/config/chromium-flags.conf", stock)
        desktop.configure(self.root, apply=True)
        self.assertEqual((self.root / "etc/skel/.config/chromium-flags.conf").read_bytes(),
                         (stock + "--disable-features=OnBeginFrameThrottleVideo\n").encode())

    def test_chromium_refuses_nul_before_writes(self):
        for extra in ("--user-agent=M4\x00ignored", "# note\x00ignored", "\x00"):
            with self.subTest(extra=extra):
                self.write("usr/share/omarchy/config/chromium-flags.conf", self.chromium + extra + "\n")
                before = self.snapshot()
                for apply in (False, True):
                    with self.subTest(apply=apply):
                        with self.assertRaisesRegex(ValueError, "Chromium option"):
                            desktop.configure(self.root, apply=apply)
                        self.assertEqual(self.snapshot(), before)

    def test_chromium_merges_padded_feature_switch(self):
        self.write("usr/share/omarchy/config/chromium-flags.conf",
                   self.chromium + "  --disable-features=Other  \n")
        desktop.configure(self.root, apply=True)
        self.assertEqual((self.root / "etc/skel/.config/chromium-flags.conf").read_bytes(),
                         (self.chromium + "--disable-features=Other,OnBeginFrameThrottleVideo\n").encode())

    def test_chromium_refuses_padded_feature_conflict_before_writes(self):
        stock = self.chromium.replace(
            "--enable-features=TouchpadOverscrollHistoryNavigation",
            "  --enable-features=OnBeginFrameThrottleVideo  ")
        self.write("usr/share/omarchy/config/chromium-flags.conf", stock)
        before = self.snapshot()
        for apply in (False, True):
            with self.subTest(apply=apply):
                with self.assertRaisesRegex(ValueError, "Conflicting Chromium feature"):
                    desktop.configure(self.root, apply=apply)
                self.assertEqual(self.snapshot(), before)

    def test_chromium_refuses_cr_in_comments_before_writes(self):
        for extra in ("# note\r", "  # note\rmore"):
            with self.subTest(extra=extra):
                self.write("usr/share/omarchy/config/chromium-flags.conf", self.chromium + extra + "\n")
                before = self.snapshot()
                for apply in (False, True):
                    with self.subTest(apply=apply):
                        with self.assertRaisesRegex(ValueError, "Chromium option"):
                            desktop.configure(self.root, apply=apply)
                        self.assertEqual(self.snapshot(), before)

    def test_chromium_line_limit_preserves_boundary(self):
        cases = ("--user-agent=" + "A" * 2033, "#" + "c" * 2045,
                 " " * 2046, "--user-agent=" + "é" * 1016 + "A")
        for index, line in enumerate(cases):
            self.assertEqual(len(line.encode("utf-8")), 2046)
            for ending in ("\n", ""):
                with self.subTest(case=index, ending=ending):
                    stock = self.chromium + line + ending
                    self.write("usr/share/omarchy/config/chromium-flags.conf", stock)
                    desktop.configure(self.root, apply=True)
                    expected = self.chromium + line + "\n--disable-features=OnBeginFrameThrottleVideo\n"
                    self.assertEqual((self.root / "etc/skel/.config/chromium-flags.conf").read_bytes(),
                                     expected.encode("utf-8"))
                    self.assertEqual((self.root / "usr/share/omarchy/config/chromium-flags.conf").read_bytes(),
                                     stock.encode("utf-8"))

    def test_chromium_refuses_oversized_input_before_rewrite(self):
        cases = ("--user-agent=" + "A" * 2034, "#" + "c" * 2046,
                 " " * 2047, "--user-agent=" + "é" * 1017,
                 " " * 2040 + "--disable-features=Other")
        for index, line in enumerate(cases):
            self.assertGreater(len(line.encode("utf-8")), 2046)
            with self.subTest(case=index):
                self.write("usr/share/omarchy/config/chromium-flags.conf", self.chromium + line + "\n")
                before = self.snapshot()
                for apply in (False, True):
                    with self.subTest(apply=apply):
                        with self.assertRaisesRegex(ValueError, "Chromium option"):
                            desktop.configure(self.root, apply=apply)
                        self.assertEqual(self.snapshot(), before)

    def test_chromium_line_limit_checks_merged_output(self):
        prefix = "--disable-features="
        added = ",OnBeginFrameThrottleVideo"
        for output_length in (2046, 2047):
            with self.subTest(output_length=output_length):
                line = prefix + "A" * (output_length - len(prefix) - len(added))
                self.write("usr/share/omarchy/config/chromium-flags.conf", self.chromium + line + "\n")
                before = self.snapshot()
                if output_length == 2046:
                    desktop.configure(self.root, apply=True)
                    expected = self.chromium + line + added + "\n"
                    self.assertEqual((self.root / "etc/skel/.config/chromium-flags.conf").read_bytes(),
                                     expected.encode("utf-8"))
                else:
                    for apply in (False, True):
                        with self.subTest(apply=apply):
                            with self.assertRaisesRegex(ValueError, "Chromium option"):
                                desktop.configure(self.root, apply=apply)
                            self.assertEqual(self.snapshot(), before)

    def test_chromium_rejects_target_anywhere_in_enable_list(self):
        for names in ("Other,OnBeginFrameThrottleVideo", "OnBeginFrameThrottleVideo,Other"):
            with self.subTest(names=names):
                stock = self.chromium.replace("TouchpadOverscrollHistoryNavigation", names)
                self.write("usr/share/omarchy/config/chromium-flags.conf", stock)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Conflicting Chromium feature"):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)

    def test_chromium_refuses_shell_syntax_before_writes(self):
        for extra in ("--ozone-platform=wayland --disable-features=Other",
                      '--user-agent=M4 "--disable-features=Other"',
                      '"--enable-features=OnBeginFrameThrottleVideo"',
                      r"--enable-fea\tures=OnBeginFrameThrottleVideo",
                      "--", "https://example.invalid", "example",
                      '--user-agent="M4"', r"--user-agent=M4\x",
                      "--user-agent=O'Brien", "--user-agent=M4\t--enable-features=OnBeginFrameThrottleVideo",
                      "\t# legacy --enable-features=OnBeginFrameThrottleVideo",
                      "\u00a0# legacy --enable-features=OnBeginFrameThrottleVideo",
                      "--user-agent=M4\r--ozone-platform=wayland",
                      "--user-agent=M4\r", "\r",
                      "--user-agent=M4\v#ignored", "--user-agent=M4\x85#ignored",
                      "--user-agent=M4\u2028#ignored"):
            with self.subTest(extra=extra):
                self.write("usr/share/omarchy/config/chromium-flags.conf", self.chromium + extra + "\n")
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Chromium option"):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)

    def test_chromium_merges_packaged_disabled_features_without_duplicates(self):
        source = "usr/share/omarchy/config/chromium-flags.conf"
        for disabled in ("Other_Feature2", "Other_Feature2,OnBeginFrameThrottleVideo"):
            with self.subTest(disabled=disabled):
                self.write(source, self.chromium + "--disable-features=" + disabled + "\n")
                desktop.configure(self.root, apply=True)
                actual = (self.root / "etc/skel/.config/chromium-flags.conf").read_text()
                self.assertEqual(actual, self.chromium +
                                 "--disable-features=Other_Feature2,OnBeginFrameThrottleVideo\n")

    def test_chromium_ambiguous_feature_contract_refused_before_writes(self):
        for extra in ("--disable-features=\n", "--disable-features=A,,B\n",
                      "--disable-features=A\n--disable-features=B\n",
                      "--enable-features=OtherEnabled\n",
                      "--enable-features=OnBeginFrameThrottleVideo\n",
                      "--enable-features=OnBeginFrameThrottleVideo<Trial\n",
                      "--enable-features=OnBeginFrameThrottleVideo.Group\n",
                      "--enable-features=OnBeginFrameThrottleVideo:x/1\n",
                      "--disable-features=OnBeginFrameThrottleVideo<Study\n",
                      "--disable-features=Other:parameter/value\n",
                      "--disable-features=Other.Group\n",
                      "--disable-features=Other-Feature\n",
                      "--disable-features=Other%20Feature\n",
                      "--disable-features=A B\n",
                      "-disable-features=Other\n",
                      "-enable-features=OnBeginFrameThrottleVideo\n",
                      '--disable-features="A B"\n'):
            with self.subTest(extra=extra):
                stock = self.chromium
                if extra.startswith("--enable-features=OnBeginFrameThrottleVideo"):
                    stock = stock.replace("--enable-features=TouchpadOverscrollHistoryNavigation\n", "")
                self.write("usr/share/omarchy/config/chromium-flags.conf", stock + extra)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Chromium (feature|option)"):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)

    def test_chromium_source_and_destination_symlinks_refused(self):
        for relative in ("usr/share/omarchy/config/chromium-flags.conf",
                         "etc/skel/.config/chromium-flags.conf"):
            with self.subTest(relative=relative):
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    target.unlink()
                outside = self.write("outside-flags", self.chromium)
                self.symlink(target, outside)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Symlink"):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)
                target.unlink()
                self.write(relative, self.chromium)

    def test_session_placement_helper_and_main_only_hook_entrypoint(self):
        desktop.configure(self.root, apply=True)
        hook = self.root / "etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-placement"
        helper = self.root / "etc/skel/.local/lib/omarchy/m4-session-placement.py"
        self.assertFalse(hook.exists())
        self.assertNotIn(hook, desktop.planned_files(self.root))
        main_hook = self.root / "etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-main-placement"
        self.assertEqual(main_hook.read_text(), '#!/bin/bash\nexec /usr/bin/python3 "$HOME/.local/lib/omarchy/m4-session-placement.py" --main-only apply\n')
        if os.name != "nt": self.assertEqual(main_hook.stat().st_mode & 0o777, 0o755)
        self.assertEqual(desktop.configure(self.root, apply=True)["changed_files"], [])
        self.assertEqual(helper.read_bytes(), SOURCE.with_name("m4-session-placement.py").read_bytes())
        helper.write_text("changed\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "m4-session-placement.py"):
            desktop.configure(self.root)
        self.assertEqual(self.snapshot(), before)

    def test_custom_main_hook_refused_and_unrelated_hook_preserved(self):
        unrelated = self.write("etc/skel/.config/omarchy/hooks/post-boot.d/40-owner", "# Custom owner hook\n")
        desktop.configure(self.root, apply=True)
        self.assertEqual(unrelated.read_text(), "# Custom owner hook\n")
        self.write("etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-main-placement", "# Custom replacement\n")
        before = self.snapshot()
        for apply in (False, True):
            with self.assertRaisesRegex(ValueError, "main-only placement hook differs"):
                desktop.configure(self.root, apply=apply)
            self.assertEqual(self.snapshot(), before)

    def test_previous_automatic_placement_hook_is_refused_before_any_write(self):
        desktop.configure(self.root, apply=True)
        for content in ('#!/bin/bash\nexec /usr/bin/python3 "$HOME/.local/lib/omarchy/m4-session-placement.py" apply\n',
                        '# Owner-customized hook\n'):
            self.write("etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-placement", content)
            before = self.snapshot()
            for apply in (False, True):
                with self.subTest(content=content, apply=apply):
                    with self.assertRaisesRegex(ValueError, "Existing automatic placement hook"):
                        desktop.configure(self.root, apply=apply)
                    self.assertEqual(self.snapshot(), before)

    @unittest.skipIf(os.name == "nt", "POSIX image permissions")
    def test_shader_cache_remains_readable_under_restrictive_umask(self):
        previous = os.umask(0o077)
        try:
            desktop.configure(self.root, apply=True)
        finally:
            os.umask(previous)
        override = self.root / "etc/xdg/uwsm/env.d/90-m4-shader-cache"
        self.assertEqual(override.stat().st_mode & 0o777, 0o644)
        for parent in (override.parent, override.parent.parent, override.parent.parent.parent):
            self.assertEqual(parent.stat().st_mode & 0o777, 0o755)
        for mode in (0o600, 0o666):
            with self.subTest(mode=oct(mode)):
                override.chmod(mode)
                with self.assertRaisesRegex(ValueError, "90-m4-shader-cache"):
                    desktop.configure(self.root)
                report = desktop.configure(self.root, apply=True)
                self.assertIn("etc/xdg/uwsm/env.d/90-m4-shader-cache", report["changed_files"])
                self.assertEqual(override.stat().st_mode & 0o777, 0o644)

    @unittest.skipIf(os.name == "nt", "POSIX image permissions")
    def test_shader_cache_rejects_private_existing_directory_before_writes(self):
        desktop.configure(self.root, apply=True)
        for name, mode in (("etc/xdg/uwsm", 0o700), ("etc/xdg/uwsm/env.d", 0o711),
                           ("etc/xdg/uwsm/env.d", 0o744), ("etc/xdg", 0o700),
                           ("etc", 0o750)):
            for apply in (False, True):
                with self.subTest(parent=name, mode=oct(mode), apply=apply):
                    parent = self.root / name
                    parent.chmod(mode)
                    before = self.snapshot()
                    try:
                        with patch.object(Path, "write_bytes") as write:
                            with self.assertRaisesRegex(ValueError, "^" + re.escape(
                                    "Image path is not a publicly readable directory: " + name) + "$"):
                                desktop.configure(self.root, apply=apply)
                            write.assert_not_called()
                        self.assertEqual(self.snapshot(), before)
                        self.assertEqual(parent.stat().st_mode & 0o777, mode)
                    finally:
                        parent.chmod(0o755)

    def test_shader_cache_rejects_non_directory_before_writes(self):
        parent = self.write("etc/xdg/uwsm", "This is a regular file.\n")
        if os.name != "nt":
            parent.parent.chmod(0o755)
            parent.chmod(0o755)
        before = self.snapshot()
        for apply in (False, True):
            with self.subTest(apply=apply):
                with self.assertRaisesRegex(ValueError, "^" + re.escape(
                        "Image path is not a publicly readable directory: etc/xdg/uwsm") + "$"):
                    desktop.configure(self.root, apply=apply)
                self.assertEqual(self.snapshot(), before)
                if os.name != "nt":
                    self.assertEqual(parent.stat().st_mode & 0o777, 0o755)

    def test_missing_packages_fail_before_any_write(self):
        for name in self.packages:
            with self.subTest(name=name):
                desc = self.root / f"var/lib/pacman/local/{name}-1-1/desc"
                prior = desc.read_text()
                desc.unlink()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Missing image packages"):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)
                desc.write_text(prior)

    def test_missing_core_package_or_provider_fails_before_any_write(self):
        for name in ("btop", "neovim", "dotnet-runtime-bin"):
            with self.subTest(name=name):
                desc = self.root / f"var/lib/pacman/local/{name}-1-1/desc"
                prior = desc.read_bytes()
                desc.unlink()
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, "Missing image packages"):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)
                desc.write_bytes(prior)
        desktop.configure(self.root, apply=True)

    def test_empty_or_versioned_core_manifest_fails_before_any_write(self):
        for content in ("# No core packages\n", "btop>=1\n"):
            self.write("usr/share/omarchy/install/omarchy-base.packages", content)
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "core package manifest"):
                desktop.configure(self.root, apply=True)
            self.assertEqual(self.snapshot(), before)

    def test_apply_is_idempotent_and_does_not_touch_owner_or_packaged_files(self):
        owner = self.write("home/example/.config/hypr/hyprland.lua", "owner customization\n")
        desktop.configure(self.root, apply=True)
        before = self.snapshot()
        report = desktop.configure(self.root, apply=True)
        self.assertEqual(report["changed_files"], [])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(owner.read_text(), "owner customization\n")
        self.assertEqual((self.root / "usr/share/omarchy/config/hypr/hyprland.lua").read_text(), self.stock)

    def test_incompatible_stock_or_locale_fails_without_writes(self):
        for path, replacement in (("usr/share/omarchy/config/hypr/hyprland.lua", "old entrypoint\n"),
                                  ("etc/locale.gen", "#de_DE.UTF-8 UTF-8\n")):
            with self.subTest(path=path):
                target = self.root / path
                prior = target.read_text()
                target.write_text(replacement)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)
                target.write_text(prior)

    def test_rejects_live_root_and_non_pending_image(self):
        with self.assertRaisesRegex(ValueError, "refusing a filesystem root"):
            desktop.configure(Path("/"))
        for marker in ("etc/m4-root-id", "var/lib/omarchy/provisioning/pending"):
            with self.subTest(marker=marker):
                path = self.root / marker
                prior = path.read_text()
                path.unlink()
                with self.assertRaisesRegex(ValueError, "pending M4 owner"):
                    desktop.configure(self.root, apply=True)
                path.write_text(prior)

    def test_symlink_target_and_parent_cannot_modify_host(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        victim = outside / "90-omarchy.conf"
        victim.write_text("host configuration\n")
        target = self.root / "etc/systemd/zram-generator.conf.d"
        target.parent.mkdir(parents=True, exist_ok=True)
        self.symlink(target, outside, directory=True)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            desktop.configure(self.root, apply=True)
        self.assertEqual(victim.read_text(), "host configuration\n")
        target.unlink()
        target.mkdir()
        self.symlink(target / "90-omarchy.conf", victim)
        with self.assertRaisesRegex(ValueError, "Symlink"):
            desktop.configure(self.root, apply=True)
        self.assertEqual(victim.read_text(), "host configuration\n")

    def test_user_services_log_to_journal_instead_of_shared_files(self):
        units = ("dbus", "pipewire", "wireplumber", "wayland-wm-app-daemon",
                 "wayland-wm-env@", "wayland-wm@")
        for unit in units:
            self.write(f"etc/systemd/user/{unit}.service.d/90-m4-ram.conf",
                       f"[Service]\nStandardOutput=append:/tmp/desktop-{unit}.log\n"
                       f"StandardError=append:/tmp/desktop-{unit}.log\n")
        desktop.configure(self.root, apply=True)
        for unit in units:
            path = self.root / f"etc/systemd/user/{unit}.service.d/90-m4-ram.conf"
            self.assertEqual(path.read_bytes(),
                             b"[Service]\nStandardOutput=journal\nStandardError=journal\n", unit)
        desktop.configure(self.root)

    def test_agent_widget_visible_before_account_signin_and_stock_layout_preserved(self):
        desktop.configure(self.root, apply=True)
        copied = self.root / "etc/skel/.config/omarchy/plugins/m4.agents"
        panel = (copied / "Panel.qml").read_text(encoding="utf-8")
        self.assertIn("visible: true", panel)
        self.assertIn("// Other behavior stays intact.", panel)
        self.assertEqual((copied / "assets/mark.svg").read_bytes(), b"<svg/>\n")
        self.assertEqual((copied / "Main.qml").read_bytes(), b"Item {}\n")
        manifest = json.loads((copied / "manifest.json").read_bytes())
        self.assertEqual(manifest["id"], "m4.agents")
        self.assertEqual(manifest["omarchy"]["clonedFrom"], "omarchy.agents")
        self.assertEqual(manifest["license"], "MIT")
        actual = json.loads((self.root / "etc/skel/.config/omarchy/shell.json").read_bytes())
        actual["bar"]["layout"]["right"][0]["id"] = "omarchy.agents"
        self.assertEqual(actual, self.shell)
        stock = self.root / "usr/share/omarchy/shell/plugins/agents/Panel.qml"
        self.assertIn(b"visible: providers.length > 0", stock.read_bytes())

    def test_unknown_widget_contract_and_symlink_assets_fail_before_writes(self):
        panel = self.root / "usr/share/omarchy/shell/plugins/agents/Panel.qml"
        original = panel.read_bytes()
        panel.write_text("Panel {}\n", encoding="utf-8")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "widget visibility"):
            desktop.configure(self.root, apply=True)
        self.assertEqual(self.snapshot(), before)
        panel.write_bytes(original)
        victim = self.root.parent / "private-host-file"
        victim.write_bytes(b"private\n")
        asset = panel.parent / "assets/mark.svg"
        asset.unlink()
        self.symlink(asset, victim)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Symlink"):
            desktop.configure(self.root, apply=True)
        self.assertEqual(self.snapshot(), before)

    def test_unexpected_ram_logging_settings_fail_before_writes(self):
        self.write("etc/systemd/user/dbus.service.d/90-m4-ram.conf",
                   "[Service]\nEnvironment=KEEP_THIS=1\n")
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "Unexpected RAM logging"):
            desktop.configure(self.root, apply=True)
        self.assertEqual(self.snapshot(), before)

    def test_locale_example_stays_commented(self):
        example = "# Examples:\n#  en_US.UTF-8 UTF-8\n"
        self.write("etc/locale.gen", example + "#en_US.UTF-8 UTF-8  \n")
        desktop.configure(self.root, apply=True)
        self.assertEqual((self.root / "etc/locale.gen").read_bytes(),
                         (example + "en_US.UTF-8 UTF-8\n").encode())

    def cli(self, mode, root=None, env=None):
        return subprocess.run([sys.executable, str(SOURCE), "--root",
                               str(root or self.root), mode],
                              env=env, capture_output=True)

    def test_cli_check_never_writes_and_reports_actual_changes(self):
        before = self.snapshot()
        check = self.cli("--check")
        self.assertNotEqual(check.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        applied = self.cli("--apply")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        after = self.snapshot()
        changed = sorted(name for name in after if before.get(name) != after[name])
        report = json.loads(applied.stdout)
        self.assertEqual(sorted(report["changed_files"]), changed)
        self.assertTrue(set(changed).issubset(report["configured_files"]))
        self.assertTrue(all("\\" not in name for name in report["configured_files"]))
        self.assertEqual(self.cli("--check").returncode, 0)
        self.assertEqual(self.snapshot(), after)
        self.assertEqual(json.loads(self.cli("--apply").stdout)["changed_files"], [])

    def test_cli_resolves_live_root_alias_before_marker_checks(self):
        alias = self.root.parent / "root-alias"
        self.symlink(alias, Path("/"), directory=True)
        result = self.cli("--check", root=alias)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"refusing a filesystem root", result.stderr)

    def test_utf8_and_linux_newlines_independent_of_host_locale(self):
        stock = self.stock + "-- UTF-8: caf\u00e9\n"
        self.write("usr/share/omarchy/config/hypr/hyprland.lua", stock)
        for name in ("etc/locale.gen", "usr/share/omarchy/install/omarchy-base.packages",
                     "usr/share/omarchy/default/systemd/zram-generator.conf.d/90-omarchy.conf"):
            path = self.root / name
            path.write_bytes(path.read_bytes() + "# UTF-8: caf\u00e9\n".encode())
        path = self.root / "var/lib/pacman/local/omarchy-1-1/desc"
        path.write_bytes(path.read_bytes() + "\n%DESC%\ncaf\u00e9\n".encode())
        self.write("etc/systemd/user/dbus.service.d/90-m4-ram.conf",
                   "[Service]\nStandardOutput=append:/tmp/desktop-caf\u00e9.log\n"
                   "StandardError=append:/tmp/desktop-caf\u00e9.log\n")
        tool = self.root.parent / SOURCE.name
        tool.write_bytes(SOURCE.read_bytes())
        tool.with_name("m4-session-placement.py").write_bytes(
            SOURCE.with_name("m4-session-placement.py").read_bytes())
        tool.with_name("m4-desktop.packages").write_bytes(
            SOURCE.with_name("m4-desktop.packages").read_bytes() + "\n# caf\u00e9\n".encode())
        result = subprocess.run([sys.executable, str(tool), "--root", str(self.root), "--apply"],
                                env={**os.environ, "LC_ALL": "C", "PYTHONUTF8": "0",
                                     "PYTHONCOERCECLOCALE": "0"}, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        config = (self.root / "etc/skel/.config/hypr/hyprland.lua").read_bytes()
        self.assertTrue(config.startswith(stock.encode("utf-8")))
        for name in json.loads(result.stdout)["configured_files"]:
            self.assertNotIn(b"\r", (self.root / name).read_bytes(), name)

    def test_complete_display_and_session_hook_survives(self):
        desktop.configure(self.root, apply=True)
        expected = '''
-- J713 display and session integration.
hl.monitor({ output = "", mode = "preferred", position = "auto", scale = 2 })
hl.config({
  cursor = { no_hardware_cursors = true },
  input = { touchpad = { natural_scroll = true } },
  debug = { disable_logs = false },
})
hl.on("hyprland.start", function()
  hl.exec_cmd("uwsm finalize HYPRLAND_INSTANCE_SIGNATURE OMARCHY_PATH && systemctl --user start pipewire.service wireplumber.service wayland-wm-app-daemon.service")
end)
'''
        actual = (self.root / "etc/skel/.config/hypr/hyprland.lua").read_text(encoding="utf-8")
        self.assertEqual(actual, self.stock.rstrip() + "\n" + expected)

    def test_widget_clone_keeps_public_docs_and_omits_internal_markdown(self):
        source = "usr/share/omarchy/shell/plugins/agents/"
        public = ("Readme.MD", "License.Md", "COPYING.md", "NOTICE.md", "CHANGELOG.md", "NOTICE")
        internal = ("internal/BUILD.md", "internal/Notes.MD")
        for name in public + internal:
            self.write(source + name, name + "\n")
        desktop.configure(self.root, apply=True)
        copied = self.root / "etc/skel/.config/omarchy/plugins/m4.agents"
        for name in public:
            self.assertEqual((copied / name).read_bytes(), (name + "\n").encode())
        for name in internal:
            self.assertFalse((copied / name).exists())
            self.assertTrue((self.root / source / name).is_file())

    def test_stale_widget_file_fails_before_writes_on_apply_and_check(self):
        old_locale = (self.root / "etc/locale.gen").read_bytes()
        desktop.configure(self.root, apply=True)
        (self.root / "etc/locale.gen").write_bytes(old_locale)
        self.write("etc/skel/.config/omarchy/plugins/m4.agents/Removed.qml", "old code\n")
        for apply in (True, False):
            with self.subTest(apply=apply):
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, r"^Unexpected existing widget files: etc/skel/\.config/omarchy/plugins/m4\.agents/Removed\.qml$"):
                    desktop.configure(self.root, apply=apply)
                self.assertEqual(self.snapshot(), before)

    def test_missing_widget_entrypoint_fails_descriptively_without_writes(self):
        for configured in (False, True):
            if configured:
                desktop.configure(self.root, apply=True)
            for name in ("manifest.json", "Panel.qml"):
                path = self.root / "usr/share/omarchy/shell/plugins/agents" / name
                prior = path.read_bytes()
                path.unlink()
                before = self.snapshot()
                try:
                    for apply in (True, False):
                        with self.subTest(configured=configured, name=name, apply=apply):
                            message = "Missing packaged widget file: " + name
                            with self.assertRaisesRegex(ValueError, "^" + re.escape(message) + "$"):
                                desktop.configure(self.root, apply=apply)
                            self.assertEqual(self.snapshot(), before)
                finally:
                    path.write_bytes(prior)

    def test_ambiguous_widget_and_locale_contracts_fail_before_writes(self):
        source = "usr/share/omarchy/shell/plugins/agents/"
        cases = [(source + "manifest.json", '{"id":"changed","entryPoints":{"barWidget":"Panel.qml"}}', "widget manifest"),
                 (source + "manifest.json", '{"id":"omarchy.agents","entryPoints":{"barWidget":"Changed.qml"}}', "widget manifest"),
                 (source + "Panel.qml", "  visible: providers.length > 0\n" * 2, "widget visibility"),
                 ("etc/locale.gen", "#en_US.UTF-8 UTF-8\nen_US.UTF-8 UTF-8\n", "exactly one")]
        for path, content, message in cases:
            with self.subTest(path=path, content=content):
                prior = (self.root / path).read_bytes()
                self.write(path, content)
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, message):
                    desktop.configure(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)
                (self.root / path).write_bytes(prior)
        path = "usr/share/omarchy/config/omarchy/shell.json"
        for entries in ([], [{"id": "omarchy.agents"}] * 2):
            self.shell["bar"]["layout"]["right"] = entries
            self.write(path, json.dumps(self.shell))
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "one agent widget"):
                desktop.configure(self.root, apply=True)
            self.assertEqual(self.snapshot(), before)

    def test_absent_logging_dropins_are_created_for_all_six_services(self):
        desktop.configure(self.root, apply=True)
        units = ("dbus", "pipewire", "wireplumber", "wayland-wm-app-daemon",
                 "wayland-wm-env@", "wayland-wm@")
        for unit in units:
            self.assertEqual((self.root / f"etc/systemd/user/{unit}.service.d/90-m4-ram.conf").read_bytes(),
                             b"[Service]\nStandardOutput=journal\nStandardError=journal\n")

    def test_obsolete_widget_visibility_comment_is_replaced(self):
        self.write("usr/share/omarchy/shell/plugins/agents/Panel.qml",
                   "  // Nothing to report, nothing in the bar: Bar.qml collapses a slot whose item\n"
                   "  // is invisible, so the icon appears the moment the first scan finds usage and\n"
                   "  // stays away entirely on a machine that has never run either CLI.\n"
                   "  visible: providers.length > 0\n")
        desktop.configure(self.root, apply=True)
        panel = (self.root / "etc/skel/.config/omarchy/plugins/m4.agents/Panel.qml").read_text(encoding="utf-8")
        self.assertNotIn("stays away entirely", panel)
        self.assertIn("Remain visible before the first account", panel)


if __name__ == "__main__":
    unittest.main()
