# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Publication races, startup transitions and the staged service/session boundary."""
import configparser
import io
import json
import os
import shlex
import subprocess
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from test_m4_j715_audio_ready import FIXTURE, ROOT, Fixture, r, s


class StartupTests(Fixture):
    def test_readiness_observer_drops_realtime_policy(self):
        protection = configparser.ConfigParser(interpolation=None)
        protection.read(ROOT / "packaging/j715-audio/speakersafetyd-j715.service")
        service = protection["Service"]
        observer = shlex.split(service["ExecStartPost"])
        self.assertEqual(observer[:3], ["/usr/bin/chrt", "--other", "0"])
        self.assertEqual(service["CPUSchedulingPolicy"], "fifo")
        self.assertEqual(service["CPUSchedulingPriority"], "20")
        result = subprocess.run(
            [*observer[:4], "-c",
             "import os; print(os.sched_getscheduler(0), os.sched_getparam(0).sched_priority)"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), "0 0")

    def test_slow_payload_check_resamples_current_lease(self):
        original = r.s.selected
        def disk_read(root, manifest):
            original(root, manifest)
            self.advance(.1)
        with patch.object(r.s, "selected", side_effect=disk_read):
            rc, _, err = self.cli(publish=True)
        self.assertEqual(rc, 0, err)

    def test_low_initialization_then_full_observation(self):
        self.group.update(phase="0", generation="0", public_generation="0", owner="0",
            control_owner="0", cancelled="1", gpio_low_verified="1",
            gpio_hw="00070a02", gpio_cache="00070a02", deadline_ns="0")
        self.write(r.PCM + "c/sub0/status", "closed")
        self.write(r.GROUP, " ".join(k + "=" + v for k, v in self.group.items()))
        began = self.now
        def transition():
            if self.now - began < 200_000_000:
                self.group.update(phase="1", generation="7", owner="1", control_owner="1", cancelled="0")
            else:
                self.group.update(phase="2", generation="7", public_generation="7", sensing="1")
                self.write(r.PCM + "c/sub0/status", FIXTURE["rx_status"])
            self.refresh()
        self.changer = transition
        rc, out, err = self.cli(publish=True)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["identity"]["generation"], 7)
        self.assertGreaterEqual(self.now - began, 15_200_000_000)

    def test_prelease_then_pending_root_record(self):
        self.assertEqual(self.cli(publish=True)[0], 0)
        record = s.at(self.root, s.READY).read_bytes()
        s.at(self.root, s.READY).unlink()
        self.svc.update(ActiveState="activating", SubState="start-post")
        self.group.update(sequence="0", sensing="0", public_generation="0",
                          mute_ns="0", deadline_ns=str(self.now + 2_000_000_000))
        self.write(r.GROUP, " ".join(k + "=" + v for k, v in self.group.items()))
        self.write(r.PCM + "c/sub0/status", "closed")
        began = self.now
        def progress():
            self.group.update(sensing="1", public_generation="1", mute_ns="1")
            self.write(r.PCM + "c/sub0/status", FIXTURE["rx_status"])
            self.refresh()
            if self.now - began >= 300_000_000:
                self.svc.update(ActiveState="active", SubState="running")
            if self.now - began >= 500_000_000:
                self.write(s.READY, record)
        self.changer = progress
        rc, _, err = self.cli("gate")
        self.assertEqual(rc, 0, err)
        self.assertGreaterEqual(self.now - began, 1_500_000_000)

    def test_pending_timeout_and_bad_low_hardware(self):
        self.group.update(phase="1")
        self.refresh()
        began = self.now
        self.assertEqual(self.cli()[0], 1)
        self.assertEqual(self.now - began, 30_000_000_000)
        self.group.update(phase="0", owner="0", public_generation="0", cancelled="1",
                          gpio_low_verified="1", gpio_hw="00070a03", gpio_cache="00070a02")
        self.refresh()
        self.assertEqual(self.cli()[0], 1)

    def test_publication_is_atomic_root_only_and_invalidated_on_stop(self):
        replace = os.replace
        published = []
        def observe(source, destination):
            if Path(destination).name == "ready.json":
                candidate = json.loads(Path(source).read_bytes())
                self.assertGreaterEqual(candidate["observed_seconds"], 15)
                self.assertFalse(Path(destination).exists())
                published.append(candidate)
            return replace(source, destination)
        with patch.object(os, "replace", side_effect=observe):
            self.assertEqual(self.cli(publish=True)[0], 0)
        self.assertEqual(len(published), 1)
        self.assertEqual(s.at(self.root, s.READY).stat().st_mode & 0o777, 0o644)
        with patch.object(os, "geteuid", return_value=1000):
            self.assertEqual(self.cli("gate")[0], 0)
            self.assertEqual(self.cli(publish=True)[0], 1)
            self.assertEqual(self.cli("invalidate")[0], 1)
        self.assertEqual(self.cli("invalidate")[0], 0)
        self.assertFalse(s.at(self.root, s.READY).exists())

    def test_publication_failure_removes_epoch_and_partial_record(self):
        replace = os.replace
        def fail(source, destination):
            if Path(destination).name == "ready.json":
                raise OSError("fixture replace failure")
            return replace(source, destination)
        with patch.object(os, "replace", side_effect=fail):
            self.assertEqual(self.cli(publish=True)[0], 1)
        parent = s.at(self.root, s.READY).parent
        self.assertFalse((parent / "ready.json").exists())
        self.assertFalse((parent / ".epoch").exists())
        self.assertFalse(list(parent.glob("*.tmp")))

    def test_stop_wins_over_publication_and_gate_in_flight(self):
        self.changer = lambda: r.invalidate(self.root)
        self.assertEqual(self.cli(publish=True)[0], 1)
        self.assertFalse(s.at(self.root, s.READY).exists())
        self.changer = None
        self.assertEqual(self.cli(publish=True)[0], 0)
        self.changer = lambda: r.invalidate(self.root)
        self.assertEqual(self.cli("gate")[0], 1)

    def test_superseded_attempt_cannot_publish_or_delete_new_record(self):
        rc, out, err = self.cli()
        self.assertEqual(rc, 0, err)
        result = json.loads(out)
        old, new = r.begin_publication(self.root), r.begin_publication(self.root)
        with self.assertRaises(ValueError):
            r.publish(self.root, self.manifest, result, old)
        current = r.publish(self.root, self.manifest, result, new)
        self.assertEqual(r.invalidate(self.root, old)["status"], "superseded")
        self.assertEqual(json.loads(s.at(self.root, s.READY).read_bytes()), current)

    def test_record_symlink_hardlink_and_parent_attacks_refused(self):
        directory = r.runtime_directory(self.root, True)
        victim = self.write("/victim", "unchanged")
        path = directory / "ready.json"
        path.symlink_to(victim)
        self.assertEqual(self.cli("invalidate")[0], 1)
        self.assertEqual(victim.read_text(), "unchanged")
        path.unlink()
        os.link(victim, path)
        self.assertEqual(self.cli("invalidate")[0], 1)
        self.assertEqual(victim.read_text(), "unchanged")
        path.unlink()
        lock = directory / ".lock"
        lock.unlink()
        lock.symlink_to(victim)
        self.assertEqual(self.cli("invalidate")[0], 1)

    def test_gate_rejects_hardlinked_startup_record(self):
        self.assertEqual(self.cli(publish=True)[0], 0)
        os.link(s.at(self.root, s.READY), self.root / "record-alias")
        self.assertEqual(self.cli("gate")[0], 1)

    def test_profile_links_and_runtime_parent_attacks_refused(self):
        path = s.at(self.root, s.PROFILE)
        victim = self.write("/victim", path.read_bytes())
        path.unlink()
        path.symlink_to(victim)
        self.assertEqual(self.cli(publish=True)[0], 1)
        path.unlink()
        os.link(victim, path)
        self.assertEqual(self.cli(publish=True)[0], 1)
        path.unlink()
        self.write(s.PROFILE, victim.read_bytes())
        import shutil
        shutil.rmtree(s.at(self.root, s.READY).parent)
        directory = self.root / "elsewhere"
        directory.mkdir()
        s.at(self.root, s.READY).parent.symlink_to(directory)
        self.assertEqual(self.cli(publish=True)[0], 1)
        self.assertEqual(list(directory.iterdir()), [])

    def test_gate_record_permissions_and_extra_feedback_owner_refused(self):
        self.assertEqual(self.cli(publish=True)[0], 0)
        s.at(self.root, s.READY).chmod(0o666)
        self.assertEqual(self.cli("gate")[0], 1)
        s.at(self.root, s.READY).chmod(0o644)
        self.write(r.PCM + "c/sub1/status", "state: RUNNING\nowner_pid: 4343")
        self.assertEqual(self.cli()[0], 1)

    def test_current_boot_and_registration_record_required(self):
        self.manifest["boot"] = "previous-boot"
        self.write(s.SELECTED, json.dumps(self.manifest))
        self.assertEqual(self.cli()[0], 1)
        self.manifest["boot"] = "fixture-boot"
        self.write(s.SELECTED, json.dumps(self.manifest))
        s.at(self.root, "/run/omarchy/j715-audio-registration/registered.json").unlink()
        self.assertEqual(self.cli()[0], 1)

    def test_pinned_wrong_id_qualification_and_build_note_never_load(self):
        import shutil
        registration = s.load("m4-j715-audio-register.py")
        shutil.rmtree(self.root / "sys/module")
        shutil.rmtree(self.root / "proc/asound")
        qualification = json.loads(s.at(self.root, self.artifact["qualification"]).read_bytes())
        commands = []
        def command(argv):
            commands.append(argv)
            return self.report.decode() if argv[-1] == s.HELPER else "inactive"
        for case in ("id", "note"):
            qualification["speaker_id"] = 0 if case == "id" else 3
            if case == "note":
                name = s.MODULES[0]
                self.artifact["modules"][name] = "aa" * 36
                self.artifact["module_artifacts"][name]["note"] = "aa" * 36
                qualification["payload_sha256"] = s.payload_identity(self.artifact)
            raw = json.dumps(qualification).encode()
            self.write(self.artifact["qualification"], raw)
            self.artifact["files"][self.artifact["qualification"]] = self.sha(raw)
            self.write(s.MANIFEST, json.dumps(self.artifact))
            with self.subTest(case=case):
                self.assertEqual(registration.main(self.root, command), 1)
                self.assertFalse(any(c[0] in ("insmod", "modprobe") for c in commands))
                self.assertFalse(s.at(self.root, "/run/omarchy/j715-audio-registration/attempt.json").exists())

    def test_missing_or_held_native_preserves_external_session(self):
        import shutil
        shutil.rmtree(self.root / "proc/asound/AppleJ715")
        self.write(s.STATE + "/hold", "held")
        self.assertEqual(s.main([], self.root), 0)
        self.assertEqual(self.service.call_count, 0)
        self.write("/proc/asound/AppleJ715/pcm0p/sub0/status", "closed")
        self.assertEqual(s.main([], self.root), 1)

    def test_units_drive_real_frontend_contracts(self):
        package = ROOT / "packaging/j715-audio"
        registration, protection, gate = (configparser.ConfigParser(interpolation=None) for _ in range(3))
        registration.read(package / "m4-j715-audio-register.service")
        protection.read(package / "speakersafetyd-j715.service")
        gate.read(package / "80-j715-audio-ready.conf")
        after = set(registration["Unit"]["After"].split())
        self.assertIn("systemd-udev-trigger.service", after)
        self.assertNotIn("systemd-udev-settle.service", after)
        self.assertEqual(protection["Unit"]["Requires"], "m4-j715-audio-register.service")
        self.assertIn("m4-j715-audio-register.service", protection["Unit"]["After"].split())
        for unit in (registration, protection):
            self.assertEqual(unit["Service"]["Restart"], "no")
        self.assertEqual(shlex.split(protection["Service"]["ExecStart"]),
                         [s.DAEMON, "--config-path", s.BASE + "/config"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(s.main(shlex.split(protection["Service"]["ExecCondition"])[3:], self.root), 0)
            for stage in ("ExecStartPre", "ExecStartPost"):
                command = shlex.split(protection["Service"][stage])
                if stage == "ExecStartPost":
                    self.assertEqual(command[:3], ["/usr/bin/chrt", "--other", "0"])
                    command = command[3:]
                self.assertEqual(r.main(command[3:], self.root), 0)
            self.assertEqual(s.main(shlex.split(gate["Service"]["ExecStartPre"])[3:], self.root), 0)
            self.assertEqual(r.main(shlex.split(protection["Service"]["ExecStopPost"])[3:], self.root), 0)
        self.assertFalse(s.at(self.root, s.READY).exists())
        self.write(s.STATE + "/hold", "held")
        self.assertEqual(s.main(["--require-registration"], self.root), 1)


class IdZeroTests(Fixture):
    speaker_id = 0

    def test_separately_qualified_zero_uses_zero_profile(self):
        self.assertEqual(self.manifest["speaker_id"], 0)
        rc, _, err = self.cli(publish=True)
        self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main()
