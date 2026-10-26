# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Synthetic lifecycle fixtures, never hardware qualification receipts."""
import hashlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
spec = importlib.util.spec_from_file_location("j715_session_tests", ROOT / "tools/m4-j715-audio-session.py")
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)
r = s.load("m4-j715-audio-ready.py")
FIXTURE = json.loads((ROOT / "tests/fixtures/j715-readiness-pcm.json").read_bytes())
NOTE = struct.pack("<III", 4, 20, 3) + b"GNU\0" + bytes(range(20))


def module_bytes():
    data = bytearray(324)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", data, 16, 1, 183)
    struct.pack_into("<Q", data, 40, 64)
    struct.pack_into("<HHH", data, 58, 64, 3, 1)
    names = b"\0.shstrtab\0.note.gnu.build-id\0"
    struct.pack_into("<IIQQQQIIQQ", data, 128, 1, 3, 0, 0, 256, len(names), 0, 0, 1, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 192, 11, 7, 0, 0, 288, len(NOTE), 0, 0, 4, 0)
    data[256:256 + len(names)] = names
    data[288:324] = NOTE
    return bytes(data)


class Fixture(unittest.TestCase):
    """Real N3 staged profiles, fake hardware files, fake clock/service boundary."""
    speaker_id = 3
    @classmethod
    def setUpClass(cls):
        sources = s.load("j715_sources.py")
        cls.lock = sources.SourceLock.read(ROOT / "audio/j715/source-lock.json")
        donor = Path(os.environ.get("J715_DONOR_SOURCE", ROOT.parent / "donor-source"))
        cls.sources = sources.local_sources(donor, cls.lock)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.now, self.sequence, self.uid, self.changer = 100_000_000_000, 1, 0, None
        self.group = dict(x.split("=") for x in FIXTURE["status"].split())
        self.write("/sys/firmware/devicetree/base/compatible", b"apple,j715\0apple,t8132\0")
        self.write("/proc/sys/kernel/osrelease", "fixture-kernel")
        self.write("/proc/sys/kernel/random/boot_id", "fixture-boot")
        self.write("/run/lock/reserved", "")
        actual_lstat, actual_fstat = Path.lstat, os.fstat
        def owned(info):
            fields = list(info)
            fields[4] = self.uid
            return os.stat_result(fields)
        self.addCleanup(patch.stopall)
        patch.object(Path, "lstat", lambda p, *a, **kw: owned(actual_lstat(p, *a, **kw))).start()
        patch.object(os, "fstat", lambda fd: owned(actual_fstat(fd))).start()
        patch.object(os, "geteuid", return_value=0).start()
        bit = self.speaker_id // 3
        self.report = json.dumps({"board": True, "chip": "gpiochip0", "pins": {"4": bit, "13": bit},
                                  "speaker_id": self.speaker_id}).encode()
        renderer = s.load("m4-j715-audio-profile.py")
        candidate = renderer.candidate(self.sources, renderer.IdReport.parse(self.report), self.lock)
        files = {s.DAEMON: b"synthetic daemon", s.PROFILE: next(
            f.data for f in self.sources if f.pin.path == "audio/j715/j715.conf")}
        contract = json.loads((ROOT / "audio/j715/runtime-contract.json").read_bytes())
        files.update({s.BASE + "/" + Path(n).name: (ROOT / n).read_bytes()
                      for n in contract["runtime_python_sources"]})
        base = s.BASE + "/profiles/" + str(self.speaker_id)
        files.update({base + "/" + n: data for n, data in candidate.files})
        files[base + "/profile-receipt.json"] = candidate.receipt
        artifacts = {}
        for module in s.MODULES:
            path = s.BASE + "/modules/" + module + ".ko"
            files[path] = module_bytes()
            artifacts[module] = {"module": module, "path": path, "sha256": self.sha(files[path]),
                                     "note": NOTE.hex(), "vermagic": "fixture-kernel SMP preempt mod_unload aarch64"}
            self.write("/sys/module/" + module + "/initstate", "live")
            self.write("/sys/module/" + module + "/notes/.note.gnu.build-id", NOTE)
        source_path = s.BASE + "/source-receipt.json"
        files[source_path] = json.dumps({"schema": 1, "board": "apple,j715",
            "commit": self.lock.commit, "lock_sha256": self.sha(self.lock.raw)}).encode()
        build = {"schema": 1, "board": "apple,j715", "profile": "conservative-v1", "kernel_release": "fixture-kernel",
                     "source_receipt_sha256": self.sha(files[source_path]),
                     "external_inputs": {"sysroot": {"usr/lib/fixture-abi": self.sha(b"synthetic ABI")}},
                     "runtime_profile_sha256": self.sha(files[s.PROFILE]), "outputs": {
                         Path(n).name: {"size": len(data), "sha256": self.sha(data)}
                         for n, data in files.items() if n == s.DAEMON or n.endswith(".ko")}}
        build_path, qpath = s.BASE + "/build-receipt.json", s.BASE + "/qualification.json"
        files[build_path] = json.dumps(build).encode()
        self.artifact = {"schema": 1, "board": "apple,j715", "soc": "apple,t8132", "service": s.SERVICE,
            "profile": "conservative-v1", "kernel_release": "fixture-kernel", "source_commit": self.lock.commit,
            "source_lock_sha256": self.sha(self.lock.raw), "files": {n: self.sha(d) for n, d in files.items()},
            "modules": {n: e["note"] for n, e in artifacts.items()}, "module_artifacts": artifacts,
            "abi_files": {"/usr/lib/fixture-abi": {"sha256": self.sha(b"synthetic ABI"), "size": 13}},
            "profiles": {str(self.speaker_id): {"root": base}}, "build_receipt": build_path,
            "source_receipt": source_path, "qualification": qpath}
        qualification = {"schema": 1, "status": "qualified", "hardware_qualified": True, "board": "apple,j715",
            "soc": "apple,t8132", "speaker_id": self.speaker_id, "profile_receipt_sha256": self.sha(candidate.receipt),
            "payload_sha256": s.payload_identity(self.artifact)}
        files[qpath] = json.dumps(qualification).encode()
        self.artifact["files"][qpath] = self.sha(files[qpath])
        for name, data in files.items():
            self.write(name, data)
        self.write("/usr/lib/fixture-abi", "synthetic ABI")
        self.write(s.MANIFEST, json.dumps(self.artifact))
        self.manifest = s.selection(self.root, self.report)
        self.write(s.SELECTED, json.dumps(self.manifest))
        self.write("/run/omarchy/j715-audio-registration/registered.json", json.dumps({
            "boot": "fixture-boot", "selection_sha256": self.sha(json.dumps(self.manifest, sort_keys=True).encode())}))
        fields = ["0"] * 40
        fields[0], fields[19] = "S", "12345"
        self.write("/proc/4242/stat", "4242 (speaker daemon) " + " ".join(fields))
        self.write("/proc/4242/status", "Uid:\t0\t0\t0\t0\nTgid:\t4242\n")
        self.write("/proc/4242/exe", files[s.DAEMON])
        self.write("/proc/4242/cmdline", (s.DAEMON + "\0--config-path\0" + s.BASE + "/config\0").encode())
        self.write("/proc/4242/cgroup", "0::/system.slice/" + s.SERVICE)
        self.write(r.PCM + "c/sub0/status", FIXTURE["rx_status"])
        self.write(r.PCM + "c/sub0/hw_params", FIXTURE["rx_hw"])
        self.write(r.PCM + "p/sub0/status", "closed")
        self.svc = {"ActiveState": "active", "SubState": "running", "Result": "success",
            "MainPID": "4242", "InvocationID": "a" * 32, "ControlGroup": "/system.slice/" + s.SERVICE}
        actual_readlink = os.readlink
        patch.object(os, "readlink", side_effect=lambda path, *a, **kw:
            s.DAEMON if str(path).endswith("/proc/4242/exe") else actual_readlink(path, *a, **kw)).start()
        patch.object(r.time, "monotonic_ns", side_effect=lambda: self.now).start()
        patch.object(r.time, "sleep", side_effect=self.advance).start()
        self.service = patch.object(r.subprocess, "check_output", side_effect=lambda *a, **kw:
            "\n".join(k + "=" + v for k, v in self.svc.items())).start()
        self.refresh()

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def write(self, name, data):
        path = s.at(self.root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        path.chmod(0o644)
        return path

    def refresh(self):
        self.group.update(sequence=str(self.sequence), deadline_ns=str(self.now + 50_000_000))
        self.write(r.GROUP, " ".join(k + "=" + v for k, v in self.group.items()))

    def advance(self, seconds):
        self.now += int(seconds * 1e9)
        self.sequence += 1
        self.refresh()
        if self.changer:
            self.changer()

    def cli(self, mode="startup", publish=False):
        args = [mode, "--manifest", s.SELECTED] if mode != "invalidate" else [mode]
        if mode == "gate":
            args += ["--ready", s.READY]
        if publish:
            args += ["--publish"]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = r.main(args, root=self.root)
        return rc, out.getvalue(), err.getvalue()


class ReadinessTests(Fixture):
    def test_startup_and_gate_observe_fake_clock(self):
        self.svc.update(ActiveState="activating", SubState="start-post")
        rc, out, err = self.cli(publish=True)
        self.assertEqual(rc, 0, err)
        self.assertGreaterEqual(json.loads(out)["observed_seconds"], 15)
        self.svc.update(ActiveState="active", SubState="running")
        began = self.now
        self.assertEqual(self.cli("gate")[0], 0)
        self.assertGreaterEqual(self.now - began, 1_000_000_000)

    def test_geometry_owner_mask_and_horizon_refused(self):
        for channels in (4, 8):
            with self.subTest(channels=channels):
                self.write(r.PCM + "c/sub0/hw_params", FIXTURE["rx_hw"].replace("channels: 12", f"channels: {channels}"))
                self.assertEqual(self.cli()[0], 1)
        self.write(r.PCM + "c/sub0/hw_params", FIXTURE["rx_hw"])
        for key, value in (("mute_mask", "f"), ("deadline_ns", str(self.now)),
                           ("deadline_ns", str(self.now + 50_000_001)), ("faulted", "1")):
            with self.subTest(key=key, value=value):
                saved = self.group[key]
                self.group[key] = value
                self.write(r.GROUP, " ".join(k + "=" + v for k, v in self.group.items()))
                self.assertEqual(self.cli()[0], 1)
                self.group[key] = saved
        self.refresh()
        self.write(r.PCM + "c/sub0/status", FIXTURE["rx_status"].replace("4242", "4343"))
        self.assertEqual(self.cli()[0], 1)

    def test_sequence_and_identity_changes_refused(self):
        mutations = [
            lambda: self.write("/proc/sys/kernel/random/boot_id", "other"),
            lambda: self.svc.update(MainPID="4343"),
            lambda: self.svc.update(InvocationID="b" * 32),
            lambda: self.group.update(generation="8", public_generation="8"),
            lambda: setattr(self, "sequence", 1)]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.write("/proc/sys/kernel/random/boot_id", "fixture-boot")
                self.svc.update(MainPID="4242", InvocationID="a" * 32)
                self.group.update(generation="1", public_generation="1")
                self.sequence = 1
                self.refresh()
                def change(mutate=mutate):
                    mutate()
                    self.refresh()
                self.changer = change
                self.assertEqual(self.cli()[0], 1)

    def test_gate_rejects_stale_attestation_and_bad_invocation(self):
        self.assertEqual(self.cli(publish=True)[0], 0)
        path = s.at(self.root, s.READY)
        original = json.loads(path.read_bytes())
        for key, value in (("boot", "old"), ("pid", 12), ("start", 44),
                           ("invocation", "b" * 32), ("generation", 99), ("manifest_sha256", "0" * 64)):
            record = json.loads(json.dumps(original))
            record["identity"][key] = value
            self.write(s.READY, json.dumps(record))
            with self.subTest(key=key):
                self.assertEqual(self.cli("gate")[0], 1)
        self.write("/proc/4242/cmdline", "wrong config")
        self.assertEqual(self.cli()[0], 1)

    def test_payload_owner_mode_links_and_manifest_change_refused(self):
        path = s.at(self.root, s.PROFILE)
        old = path.read_bytes()
        self.write(s.PROFILE, b"corrupt")
        self.assertEqual(self.cli()[0], 1)
        self.write(s.PROFILE, old)
        path.chmod(0o666)
        self.assertEqual(self.cli()[0], 1)
        path.chmod(0o644)
        self.uid = 1000
        self.assertEqual(self.cli()[0], 1)
        self.uid = 0
        self.changer = lambda: self.write(s.SELECTED, "{}")
        self.assertEqual(self.cli()[0], 1)


if __name__ == "__main__":
    unittest.main()
