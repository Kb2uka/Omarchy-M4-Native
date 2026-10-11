# SPDX-License-Identifier: MIT
"""Invoke actual Lua policy and retained upstream hooks without a live session."""
import hashlib
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
POLICY_ROOT = ROOT / 'audio/j713/policy'
SCRIPT = POLICY_ROOT / 'usr/share/wireplumber/scripts/default-nodes/j713-protected.lua'
CONF = POLICY_ROOT / 'etc/wireplumber/wireplumber.conf.d/82-j713-protected-default.conf'
FIXTURE = ROOT / 'tests/fixtures/wireplumber-0.5.18'


class DefaultPolicyTests(unittest.TestCase):
    def test_component_required_and_loads_shipped_hook(self):
        # This guards the source contract only. The separate integration check
        # loads this fragment with WirePlumber's real component resolver.
        config = CONF.read_text()
        self.assertIn('name = default-nodes/j713-protected.lua', config)
        self.assertIn('type = script/lua', config)
        self.assertIn('provides = hooks.default-nodes.j713-protected', config)
        self.assertIn('requires = [ policy.default-nodes ]', config)
        self.assertIn('hooks.default-nodes.j713-protected = required', config)
        self.assertNotIn('"policy.default-nodes"', config)
        self.assertTrue(SCRIPT.is_file())

    def test_upstream_sources_unchanged(self):
        for line in (FIXTURE / 'SHA256SUMS').read_text().splitlines():
            expected, name = line.split('  ', 1)
            self.assertEqual(hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest(), expected, name)

    def test_actual_default_node_pipeline(self):
        subprocess.run([os.environ.get('LUA', 'lua'),
                        str(ROOT / 'tests/j713_audio_default_policy.lua'),
                        str(ROOT), str(SCRIPT)], check=True)


if __name__ == '__main__':
    unittest.main()
