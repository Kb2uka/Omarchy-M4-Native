#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Reversible, owner-only placement for the experimental J713 desktop."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess


DROPIN_NAME = "systemd/user/omarchy-browser-.service.d/70-m4-placement.conf"


def command(*args):
    return subprocess.check_output(args, text=True, timeout=10)


def cpu_list(value):
    result = set()
    for item in value.strip().split(","):
        ends = item.split("-")
        if len(ends) not in (1, 2) or not all(x.isdecimal() for x in ends):
            raise ValueError("Invalid online CPU list")
        first, last = int(ends[0]), int(ends[-1])
        if not 0 <= first <= last < 4096:
            raise ValueError("Invalid online CPU range")
        result.update(range(first, last + 1))
    return result


def performance_cpus(root):
    compatible = (root / "sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
    if b"apple,j713" not in compatible or b"apple,t8132" not in compatible:
        raise ValueError("This workaround is qualified only for J713/T8132")
    directory = root / "sys/devices/system/cpu"
    online = cpu_list((directory / "online").read_text())
    roles = {b"apple,donan-p": set(), b"apple,donan-e": set()}
    registers = set()
    capacities = set()
    for cpu in online:
        path = directory / f"cpu{cpu}"
        tags = (path / "of_node/compatible").read_bytes().rstrip(b"\0")
        if tags not in roles:
            raise ValueError("Unqualified CPU type")
        roles[tags].add(cpu)
        reg = (path / "of_node/reg").read_bytes()
        if len(reg) != 8 or reg in registers:
            raise ValueError("Invalid or duplicate CPU identity")
        registers.add(reg)
        capacities.add(int((path / "cpu_capacity").read_text()))
    if len(roles[b"apple,donan-p"]) != 4 or len(roles[b"apple,donan-e"]) != 6:
        raise ValueError("Expected all four performance and six efficiency CPUs online")
    if capacities != {1024}:
        raise ValueError("Scheduler capacity changed; review this workaround")
    return online, roles[b"apple,donan-p"]


def identity(root, pid, expected=None):
    process = root / "proc" / str(pid)
    status = dict(line.split(":", 1) for line in (process / "status").read_text().splitlines())
    if status["Uid"].split() != [str(os.getuid())] * 4 or int(status["Tgid"]) != pid:
        raise ValueError("Compositor must be this owner's main thread")
    fields = (process / "stat").read_text().rsplit(")", 1)[1].split()
    current = {"pid": pid, "start": int(fields[19]), "uid": os.getuid(),
               "boot": (root / "proc/sys/kernel/random/boot_id").read_text().strip()}
    if expected is not None and any(current[key] != expected[key] for key in current):
        raise ValueError("Saved compositor process changed")
    executable = os.readlink(process / "exe").removesuffix(" (deleted)")
    if Path(executable).name != "Hyprland" or (process / "comm").read_text().strip() != "Hyprland":
        raise ValueError("Session PID is not the compositor")
    return {**current, "executable": executable}


def saved_process(root, saved):
    try:
        return identity(root, saved["pid"], expected=saved)
    except (FileNotFoundError, ProcessLookupError, ValueError):
        return None


def session(root, environment):
    instance, display = environment.get("HYPRLAND_INSTANCE_SIGNATURE"), environment.get("WAYLAND_DISPLAY")
    if not instance or not display:
        raise ValueError("Run inside the owner's graphical session")
    matches = [item for item in json.loads(command("hyprctl", "instances", "-j"))
               if item.get("instance") == instance and item.get("wl_socket") == display]
    if len(matches) != 1:
        raise ValueError("Cannot identify one current compositor")
    return identity(root, int(matches[0]["pid"]))


def dropin_content(cpus):
    return ("# J713 desktop placement; managed by m4-session-placement.py\n"
            "[Service]\nCPUAffinity=\nCPUAffinity=" + " ".join(map(str, sorted(cpus))) + "\n").encode()


def check_browser_affinity(root, managed):
    reply = json.loads(command("busctl", "--user", "--json=short", "get-property",
                               "org.freedesktop.systemd1", "/org/freedesktop/systemd1",
                               "org.freedesktop.systemd1.Manager", "UnitPath"))
    if reply.get("type") != "as" or not reply.get("data"):
        raise ValueError("Cannot discover the user manager's unit paths")
    for unit_path in reply["data"]:
        if not isinstance(unit_path, str) or not unit_path.startswith("/"):
            raise ValueError("Unexpected user unit path")
        for prefix in ("service.d", "omarchy-.service.d", "omarchy-browser-.service.d"):
            directory = root / unit_path.lstrip("/") / prefix
            try:
                files = list(directory.iterdir())
            except FileNotFoundError:
                continue
            for path in files:
                if path == managed or path.suffix != ".conf":
                    continue
                if path.name == managed.name:
                    raise ValueError("Existing browser affinity configuration: " + str(path))
                for line in path.read_text().splitlines():
                    text = line.strip()
                    if not text.startswith(("#", ";")) and text.partition("=")[0].strip() == "CPUAffinity":
                        raise ValueError("Existing browser affinity configuration: " + str(path))


def paths(root, environment, main_only=False):
    runtime = environment.get("XDG_RUNTIME_DIR")
    if os.getuid() == 0 or runtime != f"/run/user/{os.getuid()}":
        raise ValueError("Run as the desktop owner, without sudo")
    directory = root / runtime.lstrip("/")
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise ValueError("Unexpected owner runtime directory")
    basename = "m4-hyprland-main-placement" if main_only else "m4-session-placement"
    relatives = (basename + ".json", basename + ".lock")
    for relative in relatives if main_only else (*relatives, DROPIN_NAME):
        path = directory
        for part in Path(relative).parts:
            path /= part
            if path.is_symlink():
                raise ValueError("Symlink in placement state or drop-in path")
    return directory / (basename + ".json"), directory / DROPIN_NAME


def restore(root, state, dropin):
    if not state.exists():
        if dropin.exists():
            raise ValueError("Drop-in has no saved state; leaving it unchanged")
        return {"status": "inactive"}
    saved = json.loads(state.read_text())
    expected = dropin_content(saved["performance"])
    current = saved_process(root, saved["identity"])
    if current == saved["identity"]:
        pid = current["pid"]
        mask = os.sched_getaffinity(pid)
        if mask == set(saved["performance"]):
            os.sched_setaffinity(pid, saved["original"])
        elif mask != set(saved["original"]):
            raise ValueError("Compositor affinity was changed elsewhere; leaving it unchanged")
    if dropin.exists() and (dropin.is_symlink() or dropin.read_bytes() != expected):
        raise ValueError("Drop-in was customized; compositor restored, file left unchanged")
    if dropin.exists():
        dropin.unlink()
        if not any(dropin.parent.iterdir()):
            dropin.parent.rmdir()
    command("systemctl", "--user", "daemon-reload")
    state.unlink()
    return {"status": "restored", "identity_still_current": current == saved["identity"]}


def apply(root, environment, state, dropin):
    online, performance = performance_cpus(root)
    target = session(root, environment)
    check_browser_affinity(root, dropin)
    if state.exists():
        saved = json.loads(state.read_text())
        if saved["identity"] == target and saved["performance"] == sorted(performance):
            if dropin.read_bytes() != dropin_content(performance) or os.sched_getaffinity(target["pid"]) != performance:
                raise ValueError("Saved placement was changed; review before reapplying")
            return {"status": "active", "pid": target["pid"], "performance": sorted(performance)}
        if saved["identity"] != target and saved_process(root, saved["identity"]) == saved["identity"]:
            raise ValueError("Another live compositor owns placement; restore it explicitly first")
        restore(root, state, dropin)
    if dropin.exists() or dropin.is_symlink():
        raise ValueError("Existing placement drop-in; leaving it unchanged")
    original = os.sched_getaffinity(target["pid"])
    if original != online:
        raise ValueError("Existing compositor affinity is customized; leaving it unchanged")
    saved = {"identity": target, "original": sorted(original), "performance": sorted(performance)}
    with state.open("x") as stream:
        json.dump(saved, stream)
    try:
        dropin.parent.mkdir(parents=True, exist_ok=True)
        with dropin.open("xb") as stream:
            stream.write(dropin_content(performance))
        command("systemctl", "--user", "daemon-reload")
        if identity(root, target["pid"]) != target:
            raise ValueError("Compositor changed during configuration")
        os.sched_setaffinity(target["pid"], performance)
    except Exception:
        restore(root, state, dropin)
        raise
    return {"status": "active", "pid": target["pid"], "performance": sorted(performance)}


def main_state(state):
    saved = json.loads(state.read_text())
    if saved.get("policy") != "hyprland-main-only-v1":
        raise ValueError("Unrecognized main-only state; leaving it unchanged")
    return saved


def restore_main(root, state):
    if not state.exists():
        return {"status": "inactive"}
    saved = main_state(state)
    current = saved_process(root, saved["identity"])
    if current == saved["identity"]:
        pid = current["pid"]
        mask = os.sched_getaffinity(pid)
        if mask == set(saved["performance"]):
            os.sched_setaffinity(pid, saved["original"])
            if os.sched_getaffinity(pid) != set(saved["original"]):
                raise ValueError("Main-thread restore did not round-trip")
        elif mask != set(saved["original"]):
            raise ValueError("Compositor affinity was changed elsewhere; leaving it unchanged")
    state.unlink()
    return {"status": "restored", "identity_still_current": current == saved["identity"]}


def apply_main(root, environment, state):
    online, performance = performance_cpus(root)
    target = session(root, environment)
    if state.exists():
        saved = main_state(state)
        if saved["identity"] == target:
            if (saved["performance"] != sorted(performance) or
                    saved["original"] != sorted(online) or
                    os.sched_getaffinity(target["pid"]) != performance):
                raise ValueError("Saved main-only placement was changed; leaving it unchanged")
            return {"status": "active", "pid": target["pid"], "performance": sorted(performance)}
        if saved_process(root, saved["identity"]) == saved["identity"]:
            raise ValueError("Another live compositor owns main-only placement")
        restore_main(root, state)
    original = os.sched_getaffinity(target["pid"])
    if original != online:
        raise ValueError("Existing compositor affinity is customized; leaving it unchanged")
    saved = {"policy": "hyprland-main-only-v1", "identity": target,
             "original": sorted(original), "performance": sorted(performance)}
    fd = os.open(state, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(saved, stream)
    try:
        if identity(root, target["pid"]) != target:
            raise ValueError("Compositor changed during configuration")
        os.sched_setaffinity(target["pid"], performance)
        if os.sched_getaffinity(target["pid"]) != performance:
            raise ValueError("Main-thread affinity did not round-trip")
    except Exception:
        restore_main(root, state)
        raise
    return {"status": "active", "pid": target["pid"], "performance": sorted(performance)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("apply", "restore", "status"))
    parser.add_argument("--main-only", action="store_true",
                        help="Change only the current compositor main thread; no browser policy")
    args = parser.parse_args()
    root = Path("/")
    state, dropin = paths(root, os.environ, main_only=True) if args.main_only else paths(root, os.environ)
    fd = os.open(state.with_suffix(".lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.main_only:
            if args.action == "apply":
                result = apply_main(root, os.environ, state)
            elif args.action == "restore":
                result = restore_main(root, state)
            else:
                result = {"saved": main_state(state) if state.exists() else None}
        elif args.action == "apply":
            result = apply(root, os.environ, state, dropin)
        elif args.action == "restore":
            result = restore(root, state, dropin)
        else:
            result = {"saved": json.loads(state.read_text()) if state.exists() else None,
                      "dropin": dropin.read_text() if dropin.exists() else None}
        print(json.dumps(result))


if __name__ == "__main__":
    main()
