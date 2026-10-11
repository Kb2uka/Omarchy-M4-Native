#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Observe J713 protection; root can publish or invalidate its startup record."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import time

SERVICE = "speakersafetyd-j713.service"
DAEMON = "/usr/lib/speakersafetyd-j713"
PROFILE = "/usr/share/speakersafetyd/apple/j713.conf"
GROUP = "/sys/bus/platform/devices/speaker-group/status"
PCM = "/proc/asound/AppleJ713/pcm0"
RECORD = "/run/omarchy/j713-audio-readiness/ready.json"
MODULES = {"apple_admac", "snd_soc_apple_mca", "snd_soc_macaudio",
           "snd_soc_tas2764", "snd_soc_j713_group", "j713_audio_bootstrap", "j713_audio_dt3"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def at(root, absolute):
    path = Path(absolute)
    require(path.is_absolute() and ".." not in path.parts, "Expected absolute path")
    return root / str(path).lstrip("/")


def read(root, name):
    return at(root, name).read_text().strip()


def trusted(path, root, directory=False):
    """Package/record paths must not be replaceable by a desktop user."""
    for item in (path, *path.parents):
        info = item.lstat()
        require(not stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and
                not info.st_mode & 0o022, "Untrusted file or parent: " + str(item))
        if item == root:
            break
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    require(expected(path.stat().st_mode), "Unexpected file type: " + str(path))


def trusted_json(root, name):
    path = at(root, name)
    trusted(path, root)
    with path.open("rb") as stream:
        raw = stream.read(262145)
    require(len(raw) <= 262144, "JSON input exceeds 256 KiB")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def payload(root, manifest):
    require(manifest["schema"] == 1 and manifest["service"] == SERVICE, "Manifest schema/service")
    tags = at(root, "/sys/firmware/devicetree/base/compatible").read_bytes().split(b"\0")
    require(b"apple,j713" in tags and b"apple,t8132" in tags, "Expected J713/T8132")
    require(read(root, "/proc/sys/kernel/osrelease") == manifest["kernel_release"], "Kernel mismatch")
    require(DAEMON in manifest["files"] and PROFILE in manifest["files"], "Missing daemon/profile identity")
    for name, expected in manifest["files"].items():
        path = at(root, name)
        trusted(path, root)
        require(re.fullmatch(r"[0-9a-f]{64}", expected), "Invalid payload digest")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        require(actual == expected, "Payload mismatch: " + name)


def loaded_modules(root, manifest):
    require(set(manifest["modules"]) == MODULES, "Incomplete loaded-module identity")
    for module, note in manifest["modules"].items():
        base = "/sys/module/" + module
        require(read(root, base + "/initstate") == "live", "Module not live: " + module)
        require(note and at(root, base + "/notes/.note.gnu.build-id").read_bytes().hex() == note,
                "Module identity mismatch: " + module)


def service():
    properties = "ActiveState,SubState,Result,MainPID,InvocationID,ControlGroup"
    return dict(line.split("=", 1) for line in subprocess.check_output(
        ["systemctl", "--system", "show", SERVICE, "--property=" + properties],
        text=True, timeout=2).splitlines())


class Pending(Exception):
    def __init__(self, message, generation=None):
        super().__init__(message)
        self.generation = generation


def identity(root, manifest, startup, svc=None, allow_starting=False):
    svc = service() if svc is None else svc
    state = (svc.get("ActiveState"), svc.get("SubState"))
    allowed = {("active", "running")}
    if startup or allow_starting:
        allowed.add(("activating", "start-post"))
    require(state in allowed and svc.get("Result") == "success", "Protection service not ready: " + repr(state))
    invocation = svc["InvocationID"]
    require(re.fullmatch(r"[0-9a-f]{32}", invocation) and invocation != "0" * 32, "Invalid invocation")
    pid = int(svc["MainPID"])
    require(pid > 0, "Missing main PID")
    proc = "/proc/" + str(pid)
    fields = read(root, proc + "/stat").rsplit(")", 1)[1].split()
    require(fields[0] not in ("Z", "X"), "Daemon exited")
    status = pairs(read(root, proc + "/status"))
    require(status["Uid"].split() == ["0"] * 4 and int(status["Tgid"]) == pid, "Daemon identity/UID")
    if startup:
        require(os.readlink(at(root, proc + "/exe")) == DAEMON, "Daemon executable changed")
        with at(root, proc + "/exe").open("rb") as stream:
            require(hashlib.file_digest(stream, "sha256").hexdigest() == manifest["files"][DAEMON], "Running daemon payload mismatch")
    require(svc["ControlGroup"].startswith("/") and
            "0::" + svc["ControlGroup"] in read(root, proc + "/cgroup").splitlines(), "Daemon service cgroup")
    return {"boot": read(root, "/proc/sys/kernel/random/boot_id"), "invocation": invocation,
            "pid": pid, "start": int(fields[19])}


def pairs(text):
    return {key.strip(): value.strip() for key, value in
            (line.split(":", 1) for line in text.splitlines() if ":" in line)}


def sample(root, pid, startup, initial=False):
    for attempt in range(6):
        fields = dict(token.split("=", 1) for token in read(root, GROUP).split())
        if fields.get("stable") == "1":
            break
        require(attempt < 5, "Group status remained unstable")
        time.sleep(.01)
    if initial and fields.get("phase") in ("0", "1"):
        require(all(fields.get(k) == "0" for k in ("faulted", "removing", "error", "gpio_error"))
                and fields.get("profile") == "1", "Fault during initialization")
        if fields["phase"] == "0":
            low = {"owner": "0", "public_generation": "0", "cancelled": "1", "gpio_low_verified": "1",
                   "gpio_hw": "00070a02", "gpio_cache": "00070a02", "deadline_ns": "0"}
            require(all(fields.get(k) == v for k, v in low.items()) and int(fields["generation"]) >= 0 and
                    fields.get("control_owner") in ("0", "1"),
                    "Unqualified ownerless LOW initialization")
            require(read(root, PCM + "p/sub0/status") == "closed", "Ownerless LOW requires closed TX")
            rx = read(root, PCM + "c/sub0/status")
            preparing_rx = pairs(rx)
            # PCM can advance after the LOW snapshot; these are pending states,
            # never readiness. Subsequent samples must prove the owned lease.
            require(rx == "closed" or (preparing_rx.get("state") in ("OPEN", "SETUP", "PREPARED", "RUNNING") and
                    preparing_rx.get("owner_pid") == str(pid)),
                    "Ownerless LOW has unexpected RX state/owner")
            raise Pending("Waiting for protection owner acquisition")
        require(fields.get("cancelled") == "0" and fields.get("owner") == "1" and
                int(fields["generation"]) > 0, "Cancelled initialization")
        raise Pending("Waiting for protection MUTE/RX initialization", int(fields["generation"]))
    if initial and fields.get("phase") == "2" and fields.get("sequence") == "0":
        expected = {"owner": "1", "control_owner": "1", "profile": "1", "cancelled": "0",
                    "faulted": "0", "removing": "0", "error": "0", "gpio_error": "0", "mute_mask": "f"}
        require(all(fields.get(k) == v for k, v in expected.items()), "Unhealthy first-lease initialization")
        generation = int(fields["generation"])
        require(generation > 0 and fields.get("sensing") in ("0", "1") and
                fields.get("public_generation") == (str(generation) if fields["sensing"] == "1" else "0"),
                "Invalid first-lease epoch")
        require(read(root, PCM + "p/sub0/status") == "closed", "First lease requires closed TX")
        now = time.monotonic_ns()
        require(now < int(fields["deadline_ns"]) <= now + 2_000_000_000,
                "Expired or invalid configure deadline before first lease: " + json.dumps(fields))
        raise Pending("Waiting for first protection lease", generation)
    expected = {"owner": "1", "control_owner": "1", "sensing": "1", "profile": "1",
                "cancelled": "0", "faulted": "0", "removing": "0", "error": "0", "gpio_error": "0"}
    require(all(fields.get(k) == v for k, v in expected.items()), "Unhealthy protection group")
    generation = int(fields["generation"])
    require(generation > 0 and fields["public_generation"] == str(generation), "Invalid group epoch")
    phase = fields["phase"]
    require(phase == "2" if startup else phase in ("2", "3"), "Unqualified protection phase")
    tx = read(root, PCM + "p/sub0/status")
    if phase == "2":
        require(fields["mute_mask"] == "f" and int(fields["mute_ns"]) > 0 and tx == "closed", "MUTE requires closed TX")
    else:
        require(pairs(tx).get("state") == "RUNNING", "ACTIVE requires RUNNING TX")
    rx = pairs(read(root, PCM + "c/sub0/status"))
    require(rx.get("state") == "RUNNING" and rx.get("owner_pid") == str(pid), "Feedback not RUNNING under daemon")
    hw = pairs(read(root, PCM + "c/sub0/hw_params"))
    geometry = {"access": "RW_INTERLEAVED", "format": "S16_LE", "channels": "8",
                "period_size": "1024", "buffer_size": "2048"}
    require(all(hw.get(k) == v for k, v in geometry.items()) and
            hw.get("rate", "").split()[:1] == ["48000"], "Feedback geometry mismatch")
    now = time.monotonic_ns()
    deadline = int(fields["deadline_ns"])
    require(now < deadline <= now + 50_000_000, "Lease expired or outside qualified 50 ms horizon")
    require(int(fields["sequence"]) > 0, "No protection lease")
    return {"generation": generation, "sequence": int(fields["sequence"]),
            "deadline_ns": deadline, "observed_monotonic_ns": now, "phase": phase}


def initial_state(root, manifest, digest, startup, ready_path):
    boot = read(root, "/proc/sys/kernel/random/boot_id")
    until = time.monotonic_ns() + 30_000_000_000
    first = None
    first_generation = None
    while True:
        try:
            require(read(root, "/proc/sys/kernel/random/boot_id") == boot, "Boot changed while waiting")
            svc = service()
            state = (svc.get("ActiveState"), svc.get("SubState"))
            require(svc.get("Result") == "success" and svc.get("ActiveState") != "failed",
                    "Protection service failed during initialization")
            if not startup and first is None and svc.get("MainPID") == "0" and state in (
                    ("inactive", "dead"), ("activating", "start-pre"), ("activating", "start")):
                raise Pending("Waiting for system protection startup")
            current = identity(root, manifest, startup, svc, allow_starting=True)
            loaded_modules(root, manifest)
            if first is None:
                first = current
            require(current == first, "Daemon identity changed while waiting")
            previous = sample(root, first["pid"], startup, initial=True)
            require(first_generation is None or previous["generation"] == first_generation,
                    "Protection epoch changed before first lease")
            epoch = {**first, "generation": previous["generation"], "manifest_sha256": digest}
            if not startup:
                if state != ("active", "running"):
                    raise Pending("Waiting for completed system protection startup", previous["generation"])
                try:
                    expected = trusted_json(root, ready_path)[0]
                except FileNotFoundError:
                    raise Pending("Waiting for root startup attestation", previous["generation"]) from None
                require(expected.get("schema") == 1 and expected.get("status") == "ready" and expected.get("mode") == "startup" and
                        expected.get("identity") == epoch, "Stale readiness record")
            return first, previous, epoch
        except Pending as exc:
            require(first_generation is None or exc.generation is not None,
                    "Protection returned LOW after initialization began")
            if exc.generation is not None:
                require(first_generation is None or exc.generation == first_generation,
                        "Protection epoch changed while awaiting first lease")
                first_generation = exc.generation
            require(time.monotonic_ns() < until, "Readiness initialization timeout: " + str(exc))
            time.sleep(.1)


def check(root, manifest, digest, startup, ready_path):
    first, previous, epoch = initial_state(root, manifest, digest, startup, ready_path)
    started = time.monotonic_ns()
    duration = 15_000_000_000 if startup else 1_000_000_000
    while True:
        time.sleep(.25)
        current_identity = identity(root, manifest, startup)
        require(current_identity == first, "Daemon/boot identity changed during observation")
        current = sample(root, first["pid"], startup)
        require(current["generation"] == epoch["generation"], "Protection epoch changed")
        require(current["sequence"] > previous["sequence"], "Protection lease did not advance")
        previous = current
        if time.monotonic_ns() - started >= duration:
            break
    require(identity(root, manifest, startup) == first, "Final daemon identity changed")
    require(time.monotonic_ns() < current["deadline_ns"], "Lease expired during final identity check")
    return {"schema": 1, "status": "ready", "mode": "startup" if startup else "gate", "identity": epoch, "sample": current,
            "observed_seconds": (time.monotonic_ns() - started) / 1e9}


def runtime_directory(root, create):
    directory = at(root, RECORD).parent
    current = root
    trusted(current, root, directory=True)
    for part in directory.relative_to(root).parts:
        current /= part
        try:
            trusted(current, root, directory=True)
        except FileNotFoundError:
            if not create:
                return None
            current.mkdir(mode=0o755)
            current.chmod(0o755)
            trusted(current, root, directory=True)
    return directory


@contextmanager
def record_lock(root, directory):
    path = directory / ".lock"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o077, "Untrusted readiness lock")
        trusted(path, root)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def remove_record_file(root, path):
    try:
        trusted(path, root)
    except FileNotFoundError:
        return
    require(path.stat().st_nlink == 1, "Unexpected readiness hard link")
    path.unlink()


def atomic_record(root, path, data, mode):
    if path.exists() or path.is_symlink():
        trusted(path, root)
        require(path.stat().st_nlink == 1, "Unexpected readiness hard link")
    temporary = path.with_name("." + path.name + "." + secrets.token_hex(12) + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), mode)
            json.dump(data, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def invalidate(root, token=None):
    require(os.geteuid() == 0, "Invalidation requires root")
    directory = runtime_directory(root, create=False)
    if directory is None:
        return {"status": "invalidated", "record_present": False}
    with record_lock(root, directory):
        if token is not None:
            try:
                current = trusted_json(root, "/" + str((directory / ".epoch").relative_to(root)))[0]
            except FileNotFoundError:
                return {"status": "superseded"}
            if current.get("token") != token:
                return {"status": "superseded"}
        present = (directory / "ready.json").exists()
        remove_record_file(root, directory / "ready.json")
        remove_record_file(root, directory / ".epoch")
    return {"status": "invalidated", "record_present": present}


def begin_publication(root):
    require(os.geteuid() == 0, "Publication requires root")
    directory = runtime_directory(root, create=True)
    token = secrets.token_hex(32)
    with record_lock(root, directory):
        remove_record_file(root, directory / "ready.json")
        atomic_record(root, directory / ".epoch", {"token": token}, 0o600)
    return token


def publish(root, manifest, result, token):
    directory = runtime_directory(root, create=False)
    require(directory is not None, "Readiness attempt invalidated")
    with record_lock(root, directory):
        epoch = trusted_json(root, "/" + str((directory / ".epoch").relative_to(root)))[0]
        require(epoch.get("token") == token, "Readiness attempt superseded")
        expected = {key: result["identity"][key] for key in ("boot", "pid", "start", "invocation")}
        require(identity(root, manifest, True) == expected, "Daemon changed before publication")
        loaded_modules(root, manifest)
        current = sample(root, expected["pid"], True)
        require(current["generation"] == result["identity"]["generation"] and
                current["sequence"] >= result["sample"]["sequence"], "Epoch changed before publication")
        record = {**result, "sample": current, "publication_token": token}
        atomic_record(root, directory / "ready.json", record, 0o644)
    return record


def main(argv=None, root=Path("/")):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("startup", "gate", "invalidate"))
    parser.add_argument("--manifest")
    parser.add_argument("--ready", help="Root-owned startup JSON; required for the user gate")
    parser.add_argument("--publish", action="store_true", help="Atomically publish the startup record at " + RECORD)
    args = parser.parse_args(argv)
    if (args.mode == "gate") != bool(args.ready):
        parser.error("--ready is required only for gate")
    if (args.mode != "invalidate") != bool(args.manifest):
        parser.error("--manifest is required for startup/gate, not invalidate")
    if args.publish and args.mode != "startup":
        parser.error("--publish is available only with startup")
    token = None
    published = False
    try:
        require(args.mode == "gate" or os.geteuid() == 0, "Startup/invalidation requires root")
        if args.mode == "invalidate":
            result = invalidate(root)
        else:
            if args.publish:
                token = begin_publication(root)
            manifest, digest = trusted_json(root, args.manifest)
            payload(root, manifest)
            result = check(root, manifest, digest, args.mode == "startup", args.ready)
            if token is not None:
                result = publish(root, manifest, result, token)
                published = True
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "not-ready", "error": str(exc)}), file=sys.stderr)
        return 1
    finally:
        if token is not None and not published:
            try:
                invalidate(root, token)
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                print(json.dumps({"status": "cleanup-failed", "error": str(exc)}), file=sys.stderr)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
