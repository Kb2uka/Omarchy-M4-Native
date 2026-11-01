#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Evidence-gated J715 owner; recovery never unbinds or unloads a live display."""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from j715_display_profile import parse, qualified, sha

# Reuse the unchanged owner's durable locking and key-file preservation logic in
# an isolated module instance. Neither the J713 module nor its files are mutated.
SPEC = importlib.util.spec_from_file_location(
    "_j715_owner_primitives", Path(__file__).with_name("m4-backlight.py"))
OWNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OWNER)
BOARD = b"apple,j715"
COMPATIBLE = OWNER.COMPATIBLE
NAME = "j715_dcp_backlight"
MODULE = "usr/lib/omarchy-m4/backlight/j715/j715_dcp_backlight.ko"
PINS = "usr/lib/omarchy-m4/backlight/j715/module.json"
PROFILE = "usr/lib/omarchy-m4/backlight/j715/profile.json"
STATE = "var/lib/omarchy-m4/j715-backlight"
DEBUG = "sys/kernel/debug/j715_dcp_backlight"
BACKLIGHT = "sys/class/backlight/j715-dcp"
OWNER.BOARD, OWNER.STATE = BOARD, STATE
OWNER.MARKER = OWNER.MARKER.replace("J713", "J715")
OWNER.BINDS = OWNER.BINDS.replace("J713", "J715").replace("m4-backlight brightness", "m4-j715-backlight brightness")
for key, step in (("F1", "5%-"), ("F2", "+5%")):
    OWNER.BINDS += (
        f'hl.unbind("{key}")\n'
        f'o.bind("{key}", "Panel brightness", "m4-j715-backlight brightness {step}", '
        '{ locked = true, repeating = true })\n'
    )
MARKER, BINDS = OWNER.MARKER, OWNER.BINDS
Refused, require = OWNER.Refused, OWNER.require
locked_state, write_state, remove_state = OWNER.locked_state, OWNER.write_state, OWNER.remove_state
add_keys, remove_keys, keys_hook = OWNER.add_keys, OWNER.remove_keys, OWNER.keys_hook
text = OWNER.text


def is_j715(root: Path) -> bool:
    try:
        compatible = (root / COMPATIBLE).read_bytes().split(b"\0")
        return BOARD in compatible and b"apple,t8132" in compatible and b"apple,j713" not in compatible
    except OSError:
        return False


def status(root: Path):
    result = {"j715": is_j715(root), "loaded": (root / "sys/module" / NAME).exists(),
              "held": (root / STATE / "hold").exists(),
              "failed_attempt": (root / STATE / "attempt").exists(),
              "backlight": (root / BACKLIGHT).exists()}
    if result["backlight"]:
        result["nits"] = int(text(root / BACKLIGHT / "actual_brightness"))
        result["maximum_nits"] = int(text(root / BACKLIGHT / "max_brightness"))
    return result


def start(root: Path, run=subprocess.run):
    require(is_j715(root), "This is not a J715 MacBook Air")
    with locked_state(root) as state:
        require(not os.path.lexists(state / "hold"), "Native backlight is disabled until --enable")
        require(not os.path.lexists(state / "attempt"), "Earlier attempt failed; inspect before owner recovery")
        profile = qualified(root / PROFILE)
        node = root / "sys/firmware/devicetree/base" / profile.resources[0].path.lstrip("/")
        require((node / "status").is_file() and text(node / "status").rstrip("\0") == "okay" and
                (node / "omarchy,display-handoff-verified").is_file() and
                (node / "memory-region").is_file(), "Verified display handoff is not booted")
        require((node / "omarchy,display-profile").read_bytes() == profile.digest.encode() + b"\0" and
                (node / "omarchy,display-firmware").read_bytes() == profile.firmware_identity.encode() + b"\0",
                "Handoff profile identity differs")
        pin_path, module = root / PINS, root / MODULE
        require(pin_path.is_file() and not pin_path.is_symlink(), "Module pins are missing")
        pins = json.loads(pin_path.read_bytes())
        require(pins["profile_sha256"] == profile.digest, "Module profile identity differs")
        require(text(root / "proc/sys/kernel/osrelease") == pins["kernel_release"], "Wrong qualified kernel")
        require(module.is_file() and not module.is_symlink(), "Pinned module is missing")
        data = module.read_bytes()
        require(len(data) == pins["module_bytes"] and sha(data) == pins["module_sha256"], "Module pin mismatch")
        if (root / "sys/module" / NAME).exists():
            require((root / BACKLIGHT).exists(), "Loaded backlight is incomplete; reboot")
            return status(root)
        write_state(state, "attempt", "starting native J715 backlight\n")
        environment = os.environ.copy()
        environment.setdefault("TMPDIR", "/tmp")
        for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
            environment.setdefault(key, environment["TMPDIR"])
        environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
        run(["insmod", str(module)], check=True, env=environment)
        (root / DEBUG / "start").write_text("1\n")
        (root / DEBUG / "brightness_nits").write_text(f"{profile.initial_nits}\n")
        fields = dict(field.split("=", 1) for field in text(root / DEBUG / "status").split())
        require(fields.get("online") == "1" and fields.get("failed") == "0" and
                int(fields.get("applied_nits", "-1")) == profile.initial_nits and
                (root / BACKLIGHT).exists(), "Startup did not complete; no retry this boot")
        result = status(root)
        require(result["maximum_nits"] == profile.max_nits and result["nits"] == profile.initial_nits,
                "Backlight completion differs from profile")
        remove_state(state, "attempt")
        result["failed_attempt"] = False
        return result


def brightness(step: str, no_osd=False, run=subprocess.run, root=Path("/")):
    require(is_j715(root), "This is not a J715 MacBook Air")
    environment = os.environ.copy()
    environment.setdefault("TMPDIR", "/tmp")
    for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
        environment.setdefault(key, environment["TMPDIR"])
    environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
    result = run(["hyprctl", "monitors", "-j"], check=True, capture_output=True, text=True, env=environment)
    focused = [m for m in json.loads(result.stdout) if m.get("focused")]
    require(len(focused) == 1, "No unique focused monitor")
    if focused[0]["name"] != "Unknown-1":
        return run(["omarchy", "brightness", "display"] + (["--no-osd"] if no_osd else []) +
                   [step], env=environment, check=False).returncode
    require((root / BACKLIGHT).exists(), "Native panel backlight is not started")
    profile = parse(root / PROFILE)
    fd = os.open(Path(os.environ["XDG_RUNTIME_DIR"]) / "omarchy-brightness-display.lock",
                 os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            OWNER.fcntl.flock(fd, OWNER.fcntl.LOCK_EX | OWNER.fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        result = run(["brightnessctl", "-d", "j715-dcp", "-m"], check=True,
                     capture_output=True, text=True, env=environment)
        current = int(result.stdout.strip().split(",")[3].rstrip("%"))
        minimum = max(1, (100 * profile.min_nits + profile.max_nits - 1) // profile.max_nits)
        actions = {"+5%": min(100, current + (1 if current < 5 else 5)),
                   "5%-": max(minimum, current - (1 if current <= 5 else 5)),
                   "+1%": min(100, current + 1), "1%-": max(minimum, current - 1),
                   "100%": 100, "1%": minimum}
        require(step in actions, "Unsupported brightness key action")
        run(["brightnessctl", "-d", "j715-dcp", "set", f"{actions[step]}%"],
            check=True, capture_output=True, text=True, env=environment)
        if not no_osd:
            result = run(["brightnessctl", "-d", "j715-dcp", "-m"], check=True,
                         capture_output=True, text=True, env=environment)
            percent = result.stdout.strip().split(",")[3].rstrip("%")
            run(["omarchy-osd", "-i", "brightness", "-p", percent], check=True, env=environment)
        return 0
    finally:
        os.close(fd)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    for name in ("boot", "enable", "disable", "keys-hook", "keys-remove-hook", "supported"):
        action.add_argument("--" + name, action="store_true")
    parser.add_argument("--root", type=Path, default=Path("/"), help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?", choices=["brightness"])
    parser.add_argument("step", nargs="?")
    parser.add_argument("--no-osd", action="store_true")
    args = parser.parse_args(argv)
    if args.supported:
        return 0 if not args.command and is_j715(args.root) else 1
    try:
        actions = (args.boot, args.enable, args.disable, args.keys_hook, args.keys_remove_hook)
        if args.command:
            require(args.step and not any(actions), "Use brightness STEP without startup actions")
            return brightness(args.step, args.no_osd, root=args.root)
        if any(actions):
            require(os.geteuid() == 0, "This action requires root")
            require(is_j715(args.root), "This is not a J715 MacBook Air")
        if args.boot:
            print(json.dumps(start(args.root)))
        elif args.keys_hook or args.keys_remove_hook:
            print(json.dumps(keys_hook(args.root, remove=args.keys_remove_hook)))
        elif args.enable or args.disable:
            with locked_state(args.root) as state:
                if args.disable:
                    write_state(state, "hold", "disabled until --enable\n")
                else:
                    remove_state(state, "hold")
                    remove_state(state, "attempt")
            print("Owner recovery recorded for next boot; no module unloaded")
        else:
            print(json.dumps(status(args.root)))
    except (OSError, ValueError, KeyError, Refused, subprocess.CalledProcessError) as error:
        print("m4-j715-backlight: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
