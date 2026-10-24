#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Observe J715 protection using the retained owner's publication/stop lifecycle."""
import importlib.util
import json
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location('_j715_session_ready', Path(__file__).with_name('m4-j715-audio-session.py'))
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
owner = s.load('m4-j713-audio-ready.py')
SERVICE, DAEMON, PROFILE, RECORD = s.SERVICE, s.DAEMON, s.PROFILE, s.READY
PCM = '/proc/asound/AppleJ715/pcm0'
GROUP = owner.GROUP
MODULES = set(s.MODULES)
require, trusted_json, trusted = s.require, s.trusted_json, s.trusted
read, pairs, Pending = owner.read, owner.pairs, owner.Pending
initial_state, loaded_modules = owner.initial_state, owner.loaded_modules
_identity = owner.identity
os, subprocess = owner.os, owner.subprocess


def service():
    properties = "ActiveState,SubState,Result,MainPID,InvocationID,ControlGroup"
    return dict(line.split("=", 1) for line in subprocess.check_output(
        ["systemctl", "--system", "show", SERVICE, "--property=" + properties],
        text=True, timeout=2, env=s.command_environment()).splitlines())


def identity(root, manifest, startup, svc=None, allow_starting=False):
    observed = _identity(root, manifest, startup, svc, allow_starting)
    if startup:
        command = s.at(root, "/proc/" + str(observed["pid"]) + "/cmdline").read_bytes()
        require(command.split(b"\0") == [
            DAEMON.encode(), b"--config-path", (s.BASE + "/config").encode(), b""],
            "Daemon config invocation differs")
    return observed


def payload(root, manifest):
    s.selected(root, manifest)
    registered = trusted_json(root, '/run/omarchy/j715-audio-registration/registered.json')[0]
    require(registered['boot'] == manifest['boot'] and registered['selection_sha256'] ==
            s.hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            'Registration generation differs')


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
                    "faulted": "0", "removing": "0", "error": "0", "gpio_error": "0", "mute_mask": "3f"}
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
        require(fields["mute_mask"] == "3f" and int(fields["mute_ns"]) > 0 and tx == "closed", "MUTE requires closed TX")
    else:
        require(pairs(tx).get("state") == "RUNNING", "ACTIVE requires RUNNING TX")
    rx = pairs(read(root, PCM + "c/sub0/status"))
    require(rx.get("state") == "RUNNING" and rx.get("owner_pid") == str(pid), "Feedback not RUNNING under daemon")
    for other in s.at(root, PCM + "c").glob("sub*/status"):
        require(other.parent.name == "sub0" or other.read_text().strip() == "closed",
                "Feedback PCM has another owner")
    hw = pairs(read(root, PCM + "c/sub0/hw_params"))
    geometry = {"access": "RW_INTERLEAVED", "format": "S16_LE", "channels": "12",
                "period_size": "1024", "buffer_size": "2048"}
    require(all(hw.get(k) == v for k, v in geometry.items()) and
            hw.get("rate", "").split()[:1] == ["48000"], "Feedback geometry mismatch")
    now = time.monotonic_ns()
    deadline = int(fields["deadline_ns"])
    require(now < deadline <= now + 50_000_000, "Lease expired or outside qualified 50 ms horizon")
    require(int(fields["sequence"]) > 0, "No protection lease")
    return {"generation": generation, "sequence": int(fields["sequence"]),
            "deadline_ns": deadline, "observed_monotonic_ns": now, "phase": phase}


def check(root, manifest, digest, startup, ready_path):
    first, previous, epoch = initial_state(root, manifest, digest, startup, ready_path)
    attestation = None if startup else trusted_json(root, ready_path)[1]
    started = time.monotonic_ns()
    duration = 15_000_000_000 if startup else 1_000_000_000
    while True:
        time.sleep(.25)
        if not startup:
            require(trusted_json(root, ready_path)[1] == attestation, "Startup attestation invalidated/replaced")
        current_identity = identity(root, manifest, startup)
        require(current_identity == first, "Daemon/boot identity changed during observation")
        current = sample(root, first["pid"], startup)
        require(current["generation"] == epoch["generation"], "Protection epoch changed")
        require(current["sequence"] > previous["sequence"], "Protection lease did not advance")
        previous = current
        if time.monotonic_ns() - started >= duration:
            break
    s.selected(root, manifest)
    require(trusted_json(root, s.SELECTED)[1] == digest, "Selected manifest replaced")
    if not startup:
        require(trusted_json(root, ready_path)[1] == attestation, "Final startup attestation changed")
    final = sample(root, first["pid"], startup)
    require(final["generation"] == epoch["generation"] and final["sequence"] >= current["sequence"],
            "Final protection epoch/sequence changed")
    current = final
    require(identity(root, manifest, startup) == first, "Final daemon identity changed")
    require(time.monotonic_ns() < current["deadline_ns"], "Lease expired during final identity check")
    return {"schema": 1, "status": "ready", "mode": "startup" if startup else "gate", "identity": epoch, "sample": current,
            "observed_seconds": (time.monotonic_ns() - started) / 1e9}



_original_publish = owner.publish


def publish(root, manifest, result, token):
    payload(root, manifest)
    require(trusted_json(root, s.SELECTED)[1] == result['identity']['manifest_sha256'],
            'Selection changed before publication')
    return _original_publish(root, manifest, result, token)


# Each owner module is loaded in a private namespace. No J713 source, installed
# manifest or J713 process is changed. Reuse the proven lock/epoch/race machinery.
for name in ('SERVICE', 'DAEMON', 'PROFILE', 'RECORD', 'PCM', 'MODULES', 'require',
             'trusted_json', 'trusted', 'payload', 'sample', 'check', 'publish', 'identity', 'service'):
    setattr(owner, name, globals()[name])
main = owner.main
invalidate, begin_publication = owner.invalidate, owner.begin_publication
runtime_directory, atomic_record = owner.runtime_directory, owner.atomic_record


if __name__ == '__main__':
    raise SystemExit(main())
