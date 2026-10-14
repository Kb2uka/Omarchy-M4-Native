# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Luthfi Hizb
"""J713 headphone jack sources: register plan contract, default-output policy, names."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'audio/j713/headphones'
POLICY = SOURCE / 'policy/usr/share'
CONF = POLICY / 'wireplumber/wireplumber.conf.d/83-j713-headphones.conf'
HOOK = POLICY / 'wireplumber/scripts/default-nodes/j713-headphones.lua'
UCM = POLICY / 'alsa/ucm2/conf.d/J713Headphones'
SPEAKER_HOOK = ROOT / 'audio/j713/policy/usr/share/wireplumber/scripts/default-nodes/j713-protected.lua'


class HeadphoneSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'no host C compiler')
    def test_register_plan_contract(self):
        # Every register value and the shared-register contract with the
        # speaker driver live in j713_hp_plan.h; plan_test.c enforces them.
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'plan_test'
            subprocess.run(['cc', '-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(binary),
                            str(SOURCE / 'kernel/test/plan_test.c')], check=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
        self.assertIn('all checks passed', result.stdout)

    def test_default_output_policy(self):
        subprocess.run([os.environ.get('LUA', 'lua'), str(ROOT / 'tests/j713_headphones_policy.lua'),
                        str(ROOT), str(SPEAKER_HOOK), str(HOOK)], check=True)

    def test_hook_can_never_stop_wireplumber(self):
        config = CONF.read_text()
        self.assertIn('name = default-nodes/j713-headphones.lua', config)
        self.assertIn('provides = hooks.default-nodes.j713-headphones', config)
        self.assertIn('hooks.default-nodes.j713-headphones = optional', config)
        self.assertIn('wants = [ hooks.default-nodes.j713-headphones ]', config)
        self.assertNotIn('requires', re.sub(r'#.*', '', config))
        self.assertNotIn('= required', config)

    def test_card_jack_and_node_names_agree(self):
        driver = (SOURCE / 'kernel/j713_headphones.c').read_text()
        self.assertIn('h->card.name = "J713Headphones";', driver)
        self.assertIn('snd_soc_card_jack_new_pins(rtd->card, "Headphone Jack",', driver)
        device = (UCM / 'HiFi.conf').read_text()
        self.assertIn('JackControl "Headphone Jack"', device)
        self.assertIn('PlaybackPCM "hw:${CardId},0"', device)
        self.assertIn('File "HiFi.conf"', (UCM / 'J713Headphones.conf').read_text())
        config = CONF.read_text()
        self.assertIn('api.alsa.path = "hw:J713Headphones,0"', config)
        node = re.search(r'node\.name = "([^"]+)"', config).group(1)
        self.assertEqual(node, 'alsa_output.j713.Headphones')
        self.assertIn(f'props ["node.name"] == "{node}"', HOOK.read_text())


if __name__ == '__main__':
    unittest.main()
