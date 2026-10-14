#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Prepare desktop defaults in an offline J713 image, retaining packaged Omarchy defaults."""
import argparse
import json
import os
from pathlib import Path
import re
import stat


MAIN_PLACEMENT_HOOK = "etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-main-placement"
MAIN_PLACEMENT_CONTENT = ('#!/bin/bash\n'
                          'exec /usr/bin/python3 "$HOME/.local/lib/omarchy/m4-session-placement.py" --main-only apply\n')

M4_OVERRIDES = '''
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
-- J713 lid (issue #84): lock on close, light the lock screen on open.
-- The Air's lid is "Apple SMC power/lid events", not the "Lid Switch" Omarchy
-- 4.0.4 binds, and its lid check reads ACPI, which arm64 lacks. Lock on close
-- and light the lock screen on open. Omarchy's clamshell check is skipped:
-- omarchy-hw-external-monitors counts the simpledrm panel (Unknown-1) as
-- external, so it would never lock. logind still ignores the lid: J713
-- suspend is unqualified.
o.bind("switch:on:Apple SMC power/lid events", nil, "omarchy-system-lock", { locked = true })
o.bind("switch:off:Apple SMC power/lid events", nil, "omarchy-brightness-display on", { locked = true })
'''


APPLY_LOCK = "usr/bin/omarchy-apply-lock"
LOCK_PAM = "etc/pam.d/omarchy-lock-password"


def lock_pam(root):
    """Omarchy's installer runs omarchy-apply-lock to write the lock screen's PAM
    stack. An offline image never runs it, and without the file the lock screen
    refuses to lock ("missing-pam"). Take the stack from the packaged helper."""
    text = image_path(root, APPLY_LOCK).read_text(encoding="utf-8")
    stacks = re.findall(r"\btee /etc/pam\.d/omarchy-lock-password >/dev/null <<'EOF'\n(.*?\n)EOF\n", text, re.S)
    if len(stacks) != 1 or not stacks[0].startswith("#%PAM-1.0\n"):
        raise ValueError("Unexpected packaged lock screen PAM helper; review omarchy-apply-lock")
    return stacks[0]


def image_path(root, relative):
    """Require real paths: this image operation must not follow host symlinks."""
    path = root
    for part in Path(relative).parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"Symlink in image configuration path: {relative}")
    return path


def package_names(root):
    names = set()
    for desc in image_path(root, "var/lib/pacman/local").glob("*/desc"):
        fields = image_path(root, desc.relative_to(root)).read_text(encoding="utf-8").splitlines()
        if "%NAME%" in fields:
            index = fields.index("%NAME%") + 1
            if index < len(fields):
                names.add(fields[index])
        if "%PROVIDES%" in fields:
            for value in fields[fields.index("%PROVIDES%") + 1:]:
                if not value:
                    break
                names.add(value.partition("=")[0])
    return names


def agent_widget_files(root):
    """Clone the packaged widget into owner defaults without changing packaged files."""
    source = "usr/share/omarchy/shell/plugins/agents"
    target = "etc/skel/.config/omarchy/plugins/m4.agents"
    files = {}
    for path in sorted(image_path(root, source).rglob("*")):
        path = image_path(root, path.relative_to(root))
        public_document = path.name.upper() in ("README.MD", "LICENSE.MD", "COPYING.MD", "NOTICE.MD", "CHANGELOG.MD")
        if path.is_file() and (path.suffix.lower() != ".md" or public_document):
            relative = path.relative_to(root / source).as_posix()
            files[f"{target}/{relative}"] = path.read_bytes()
    for name in ("manifest.json", "Panel.qml"):
        if f"{target}/{name}" not in files:
            raise ValueError("Missing packaged widget file: " + name)
    stale = []
    for path in sorted(image_path(root, target).rglob("*")):
        path = image_path(root, path.relative_to(root))
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative not in files:
            stale.append(relative)
    if stale:
        raise ValueError("Unexpected existing widget files: " + ", ".join(stale))
    manifest = json.loads(files[f"{target}/manifest.json"])
    if manifest.get("id") != "omarchy.agents" or manifest.get("entryPoints", {}).get("barWidget") != "Panel.qml":
        raise ValueError("Unexpected packaged agent widget manifest")
    manifest["id"] = "m4.agents"
    manifest.setdefault("omarchy", {})["clonedFrom"] = "omarchy.agents"
    files[f"{target}/manifest.json"] = json.dumps(manifest, indent=2) + "\n"
    panel = files[f"{target}/Panel.qml"].decode("utf-8")
    visibility = "  visible: providers.length > 0"
    if panel.count(visibility) != 1:
        raise ValueError("Unexpected packaged agent widget visibility")
    panel = panel.replace(visibility, "  visible: true")
    panel = panel.replace(
        "  // Nothing to report, nothing in the bar: Bar.qml collapses a slot whose item\n"
        "  // is invisible, so the icon appears the moment the first scan finds usage and\n"
        "  // stays away entirely on a machine that has never run either CLI.\n",
        "  // Remain visible before the first account has usage data.\n")
    files[f"{target}/Panel.qml"] = panel
    shell = json.loads(image_path(root, "usr/share/omarchy/config/omarchy/shell.json").read_bytes())
    entries = [entry for section in shell["bar"]["layout"].values() for entry in section
               if entry.get("id") == "omarchy.agents"]
    if len(entries) != 1:
        raise ValueError("Packaged shell must contain one agent widget")
    entries[0]["id"] = "m4.agents"
    files["etc/skel/.config/omarchy/shell.json"] = json.dumps(shell, indent=2) + "\n"
    return files


def session_logging_files(root):
    files = {}
    for unit in ("dbus", "pipewire", "wireplumber", "wayland-wm-app-daemon",
                 "wayland-wm-env@", "wayland-wm@"):
        name = f"etc/systemd/user/{unit}.service.d/90-m4-ram.conf"
        path = image_path(root, name)
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()
            if (len(lines) != 3 or lines[0] != "[Service]" or
                not re.fullmatch(r"StandardOutput=(journal|append:/tmp/desktop-[^\r\n]+\.log)", lines[1]) or
                not re.fullmatch(r"StandardError=(journal|append:/tmp/desktop-[^\r\n]+\.log)", lines[2])):
                raise ValueError(f"Unexpected RAM logging settings: {name}")
        files[name] = "[Service]\nStandardOutput=journal\nStandardError=journal\n"
    return files


def check_chromium_line_lengths(lines):
    """Fit each UTF-8 line and LF in one read of the launcher's 2048-byte buffer."""
    if any(len(line.encode("utf-8")) + 1 > 2047 for line in lines):
        raise ValueError("Unexpected Chromium option syntax; review packaged defaults")


def chromium_flags(root):
    """Retain packaged options and disable the measured M4 video throttle."""
    source = image_path(root, "usr/share/omarchy/config/chromium-flags.conf")
    text = source.read_bytes().decode("utf-8")
    if "\x00" in text or "\r" in text:
        raise ValueError("Unexpected Chromium option syntax; review packaged defaults")
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    check_chromium_line_lengths(lines)
    feature = "OnBeginFrameThrottleVideo"
    switches = {}
    for index, line in enumerate(lines):
        value = line.strip(" ")
        if not value or value.startswith("#"):
            continue
        if not re.fullmatch(r"--?[A-Za-z0-9][A-Za-z0-9_-]*(?:=[^\s'\"\\]*)?", value):
            raise ValueError("Unexpected Chromium option syntax; review packaged defaults")
        if not re.match(r"--?(enable|disable)-features", value):
            continue
        match = re.fullmatch(r"--(enable|disable)-features=([A-Za-z0-9_,]+)", value)
        if not match or match[1] in switches:
            raise ValueError("Unexpected Chromium feature switch; review packaged defaults")
        names = match[2].split(",")
        if not all(names):
            raise ValueError("Unexpected Chromium feature list; review packaged defaults")
        switches[match[1]] = (index, names)
        if match[1] == "enable" and feature in names:
            raise ValueError("Conflicting Chromium feature enable; review packaged defaults")
    if "disable" in switches:
        index, names = switches["disable"]
        if feature not in names:
            lines[index] = "--disable-features=" + ",".join([*names, feature])
    else:
        lines.append("--disable-features=" + feature)
    check_chromium_line_lengths(lines)
    return "\n".join(lines) + "\n"


def planned_files(root):
    if root == Path(root.anchor):
        raise ValueError("An offline image directory is required; refusing a filesystem root")
    for marker in ("etc/m4-root-id", "var/lib/omarchy/provisioning/pending"):
        if not image_path(root, marker).is_file():
            raise ValueError(f"Not a pending M4 owner image: missing {marker}")
    if image_path(root, "etc/skel/.config/omarchy/hooks/post-boot.d/50-m4-placement").exists():
        raise ValueError("Existing automatic placement hook; review and remove it from the offline candidate before configuration")
    hook = image_path(root, MAIN_PLACEMENT_HOOK)
    if hook.exists() and (not hook.is_file() or hook.read_bytes() != MAIN_PLACEMENT_CONTENT.encode()):
        raise ValueError("Existing main-only placement hook differs; leaving it unchanged")
    packages = Path(__file__).with_name("m4-desktop.packages").read_text(encoding="utf-8").splitlines()
    required = {p for p in packages if p and not p.startswith("#")}
    required.update(("omarchy", "omarchy-settings", "hyprland"))
    base = image_path(root, "usr/share/omarchy/install/omarchy-base.packages").read_text(encoding="utf-8")
    core = {line.strip() for line in base.splitlines()
            if line.strip() and not line.lstrip().startswith("#")}
    if not core or any(not re.fullmatch(r"[a-zA-Z0-9@_+][a-zA-Z0-9@_.+-]*", name) for name in core):
        raise ValueError("Unexpected core package manifest; expected unversioned package names")
    required.update(core)
    missing = sorted(required - package_names(root))
    if missing:
        raise ValueError("Missing image packages: " + ", ".join(missing))

    stock = image_path(root, "usr/share/omarchy/config/hypr/hyprland.lua").read_text(encoding="utf-8")
    if 'require("default.hypr.omarchy")' not in stock:
        raise ValueError("Packaged Hyprland entrypoint does not load Omarchy defaults")
    locale = image_path(root, "etc/locale.gen").read_text(encoding="utf-8").splitlines()
    matches = [i for i, line in enumerate(locale)
               if re.fullmatch(r"#?en_US\.UTF-8\s+UTF-8\s*", line)]
    if len(matches) != 1:
        raise ValueError("Image locale.gen must contain exactly one en_US.UTF-8 entry")
    for index in matches:
        locale[index] = "en_US.UTF-8 UTF-8"
    zram = image_path(root, "usr/share/omarchy/default/systemd/zram-generator.conf.d/90-omarchy.conf").read_text(encoding="utf-8")
    files = {
        MAIN_PLACEMENT_HOOK: MAIN_PLACEMENT_CONTENT,
        "etc/skel/.config/hypr/hyprland.lua": stock.rstrip() + "\n" + M4_OVERRIDES,
        "etc/skel/.config/chromium-flags.conf": chromium_flags(root),
        "etc/locale.gen": "\n".join(locale) + "\n",
        "etc/locale.conf": "LANG=en_US.UTF-8\n",
        "etc/xdg/uwsm/env.d/90-m4-shader-cache": "export MESA_SHADER_CACHE_DISABLE=false\n",
        "etc/skel/.local/lib/omarchy/m4-session-placement.py": Path(__file__).with_name("m4-session-placement.py").read_bytes(),
        "etc/systemd/zram-generator.conf.d/90-omarchy.conf": zram,
        LOCK_PAM: lock_pam(root),
    }
    files.update(session_logging_files(root))
    files.update(agent_widget_files(root))
    return {image_path(root, name): content.encode("utf-8") if isinstance(content, str) else content
            for name, content in files.items()}


def configure(root, apply=False):
    root = root.resolve(strict=True)
    files = planned_files(root)
    cache = image_path(root, "etc/xdg/uwsm/env.d/90-m4-shader-cache")
    new_cache_dirs = []
    for parent in cache.parents:
        if parent == root:
            break
        if not parent.exists():
            new_cache_dirs.append(parent)
        elif not parent.is_dir() or (os.name != "nt" and
                                    parent.stat().st_mode & 0o005 != 0o005):
            raise ValueError("Image path is not a publicly readable directory: " +
                             parent.relative_to(root).as_posix())
    owned_modes = {cache: 0o644, image_path(root, MAIN_PLACEMENT_HOOK): 0o755,
                   image_path(root, "etc/skel/.local/lib/omarchy/m4-session-placement.py"): 0o644,
                   image_path(root, LOCK_PAM): 0o644}
    different = [path.relative_to(root).as_posix() for path, content in files.items()
                 if not path.is_file() or path.read_bytes() != content or
                 (path in owned_modes and os.name != "nt" and
                  stat.S_IMODE(path.stat().st_mode) != owned_modes[path])]
    if apply:
        for path, content in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if path == cache and os.name != "nt":
                for parent in new_cache_dirs:
                    parent.chmod(0o755)
            path.write_bytes(content)
            if path in owned_modes and os.name != "nt":
                path.chmod(owned_modes[path])
    elif different:
        raise ValueError("Desktop image configuration differs: " + ", ".join(different))
    return {"configured_files": [p.relative_to(root).as_posix() for p in files],
            "changed_files": different if apply else [],
            "scope": "Offline defaults only; run locale-gen and qualify a fresh owner session separately"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    print(json.dumps(configure(args.root, args.apply), indent=2))
