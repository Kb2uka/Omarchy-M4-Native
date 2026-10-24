# SPDX-License-Identifier: MIT
"""Run the shipped J715 default hook with retained upstream selection hooks."""
import hashlib
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from j715_sources import SourceLock, local_sources

DONOR = Path(os.environ.get("J715_DONOR_SOURCE", ROOT.parent / "donor-source"))
POLICY = DONOR / "audio/j715/policy"


class DefaultPolicyTests(unittest.TestCase):
    def test_actual_default_pipeline_when_shipped_j715_hook_is_loaded(self):
        # Given: original donor bytes and the retained upstream hooks.
        local_sources(DONOR, SourceLock.read(ROOT / "audio/j715/source-lock.json"))
        fixture = ROOT / "tests/fixtures/wireplumber-0.5.18"
        for line in (fixture / "SHA256SUMS").read_text().splitlines():
            expected, name = line.split("  ", 1)
            self.assertEqual(hashlib.sha256((fixture / name).read_bytes()).hexdigest(), expected)
        environment = os.environ.copy()
        for key in ("TMPDIR", "NODE_COMPILE_CACHE", "BUN_RUNTIME_TRANSPILER_CACHE_PATH"):
            self.assertTrue(environment.get(key), key)
        environment.update(PYTHONDONTWRITEBYTECODE="1", GIT_MASTER="1")
        script = POLICY / "usr/share/wireplumber/scripts/default-nodes/j715-protected.lua"
        # When: execute actual shipped policy through real upstream hook ordering.
        result = subprocess.run(
            [environment.get("LUA", "lua"), str(ROOT / "tests/j715_audio_default_policy.lua"),
             str(ROOT), str(script)], env=environment, text=True, capture_output=True, check=False)
        # Then: selection scenarios, including J713 noninterference, must all pass.
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feedback_not_exposed_when_shipped_ucm_defines_devices(self):
        # Given: the actual playback-only UCM device configuration.
        hifi = (POLICY / "usr/share/alsa/ucm2/conf.d/macaudio/J715-HiFi.conf").read_text()
        # When: parse machine-consumed PCM entries, not comments or descriptions.
        entries = re.findall(r'^\s*(PlaybackPCM|CapturePCM)\s+"([^"]+)"', hifi, re.MULTILINE)
        # Then: no feedback capture PCM is exposed by this UCM candidate.
        self.assertEqual(entries, [("PlaybackPCM", "hw:AppleJ715,0")])


if __name__ == "__main__":
    unittest.main()
