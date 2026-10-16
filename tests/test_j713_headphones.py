# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Luthfi Hizb
"""J713 headphone jack sources: register plan contract, default-node policy, names."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'audio/j713/headphones'
KERNEL = SOURCE / 'kernel'
PLAN_HEADER = KERNEL / 'j713_hp_plan.h'
PLAN_TEST = KERNEL / 'test/plan_test.c'
# Read only: the speaker stack's canonical builders, plan_test's oracle.
SPEAKER_KERNEL = ROOT / 'audio/j713/kernel'
POLICY = SOURCE / 'policy/usr/share'
CONF = POLICY / 'wireplumber/wireplumber.conf.d/83-j713-headphones.conf'
HOOK = POLICY / 'wireplumber/scripts/default-nodes/j713-headphones.lua'
UCM = POLICY / 'alsa/ucm2/conf.d/J713Headphones'
SPEAKER_HOOK = ROOT / 'audio/j713/policy/usr/share/wireplumber/scripts/default-nodes/j713-protected.lua'
CFLAGS = ['-std=gnu11', '-O2', '-Wall', '-Wextra', '-Werror']

# Edits of j713_hp_plan.h that plan_test.c must reject: (name, original, mutated).
PLAN_MUTANTS = (
    ('RX SET writes bit 0 (the speaker IV sense)',
     'j713_hp_add(out, d->enable_set, ~0U, J713_HP_BIT);',
     'j713_hp_add(out, d->enable_set, ~0U, dir == J713_HP_RX ? 1 : J713_HP_BIT);'),
    ('RX CLR writes bit 0 (the speaker IV sense)',
     'j713_hp_add(out, d->enable_clr, ~0U, J713_HP_BIT);',
     'j713_hp_add(out, d->enable_clr, ~0U, dir == J713_HP_RX ? 1 : J713_HP_BIT);'),
    ('TEARDOWN writes the RX conf',
     '\t\t/* Reverse of PORT_OPEN, as the speaker\'s j713_port_close(). */\n',
     '\t\t/* Reverse of PORT_OPEN, as the speaker\'s j713_port_close(). */\n'
     '\t\tj713_hp_add(&p, J713_HP_RXS_CONF, ~0U, J713_HP_RX_CONF);\n'),
    ('OMUX clear while the other direction runs',
     '\tif (!other)\n\t\tj713_hp_add(out, J713_HP_SYNC_OMUX_ENABLE_CLR, ~0U, J713_HP_BIT);',
     '\tj713_hp_add(out, J713_HP_SYNC_OMUX_ENABLE_CLR, ~0U, J713_HP_BIT);'),
    ('the stop does not update the active mask',
     'return active & ~(1U << dir);',
     'return active;'),
    ('prepare reprograms while the other direction runs',
     '} else if ((active & other) || (clocked && (prepared & other))) {',
     '} else if (clocked && (prepared & other)) {'),
    ('prepare resets RX while TX runs',
     'a.reset = !(dir == J713_HP_RX && (active & (1U << J713_HP_TX)));',
     'a.reset = true;'),
    ('TEARDOWN leaves the RX FIFO enabled',
     'j713_hp_add(&p, J713_HP_ADP_FIFO_EN, J713_HP_FIFO_EN_BOTH, 0);',
     'j713_hp_add(&p, J713_HP_ADP_FIFO_EN, J713_HP_FIFO_EN_TX, 0);'),
    ('ch5 BUS_WIDTH clobbers bits 31:8',
     'j713_hp_add(&p, J713_HP_DMA_BUS_WIDTH, J713_HP_DMA_BUS_MASK, J713_HP_DMA_BUS_32BIT);',
     'j713_hp_add(&p, J713_HP_DMA_BUS_WIDTH, ~0U, J713_HP_DMA_BUS_32BIT);'),
    ('PROGRAM writes the speaker\'s SYNC_OMUX_SEL(0)',
     'j713_hp_add(&p, J713_HP_SYNC_OMUX_SEL(J713_HP_IDX), ~0U,',
     'j713_hp_add(&p, J713_HP_SYNC_OMUX_SEL(0), ~0U,'),
    ('RX conf without BCLK_POL',
     '(0xb000 | (J713_HP_SYNC_SEL << 16) | 0x100 | 0x400)',
     '(0xb000 | (J713_HP_SYNC_SEL << 16) | 0x100)'),
)


def build_plan_test(binary, header_dir):
    subprocess.run(['cc', *CFLAGS, '-I', str(header_dir), '-I', str(SPEAKER_KERNEL),
                    '-o', str(binary), str(PLAN_TEST)], check=True)


class HeadphoneSourceTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('cc'), 'no host C compiler')
    def test_register_plan_contract(self):
        # Every register value, the shared-register contract with the speaker
        # driver and the two-direction policy live in j713_hp_plan.h;
        # plan_test.c enforces them.
        with tempfile.TemporaryDirectory() as temp:
            binary = Path(temp) / 'plan_test'
            build_plan_test(binary, KERNEL)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
        self.assertIn('all checks passed', result.stdout)

    @unittest.skipUnless(shutil.which('cc'), 'no host C compiler')
    def test_plan_test_rejects_mutants(self):
        header = PLAN_HEADER.read_text()
        with tempfile.TemporaryDirectory() as temp:
            for name, original, mutated in PLAN_MUTANTS:
                with self.subTest(name):
                    self.assertEqual(header.count(original), 1, 'mutant no longer applies')
                    directory = Path(temp) / str(PLAN_MUTANTS.index((name, original, mutated)))
                    directory.mkdir()
                    (directory / 'j713_hp_plan.h').write_text(header.replace(original, mutated))
                    build_plan_test(directory / 'plan_test', directory)
                    result = subprocess.run([str(directory / 'plan_test')], capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('FAIL', result.stdout)

    def test_default_node_policy(self):
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
        driver = (KERNEL / 'j713_headphones.c').read_text()
        self.assertIn('h->card.name = "J713Headphones";', driver)
        self.assertIn('snd_soc_card_jack_new_pins(rtd->card, "Headphone Jack",', driver)
        self.assertIn('{ .pin = "Headset Mic", .mask = SND_JACK_MICROPHONE },', driver)
        device = (UCM / 'HiFi.conf').read_text()
        self.assertIn('JackControl "Headphone Jack"', device)
        self.assertIn('PlaybackPCM "hw:${CardId},0"', device)
        self.assertIn('JackControl "Headset Mic Jack"', device)
        self.assertIn('CapturePCM "hw:${CardId},0"', device)
        self.assertIn('File "HiFi.conf"', (UCM / 'J713Headphones.conf').read_text())
        config = CONF.read_text()
        self.assertEqual(config.count('api.alsa.path = "hw:J713Headphones,0"'), 2)
        nodes = re.findall(r'media\.class = "([^"]+)"\s*\}\s*\]\s*actions = \{\s*update-props = \{\s*'
                           r'node\.name = "([^"]+)"', config)
        self.assertEqual(nodes, [('Audio/Sink', 'alsa_output.j713.Headphones'),
                                 ('Audio/Source', 'alsa_input.j713.Headset')])
        hook = HOOK.read_text()
        for media_class, node in nodes:
            self.assertIn(f'name = "{node}", class = "{media_class}"', hook)

    def test_capture_dma_channel_agrees(self):
        # The DT names the capture channel, the plan header its registers, and
        # probe refuses a mismatch.
        overlay = (KERNEL / 'j713-headphones.dtso').read_text()
        self.assertIn('dmas = <0xfeed0003 4>, <0xfeed0003 5>;', overlay)
        self.assertIn('dma-names = "tx", "rx";', overlay)
        self.assertIn('#define J713_HP_RX_DMA_CHAN\t\t5', PLAN_HEADER.read_text())
        driver = (KERNEL / 'j713_headphones.c').read_text()
        self.assertIn('dma_request_chan(dev, "rx")', driver)
        self.assertIn('->chan_id != J713_HP_RX_DMA_CHAN', driver)


if __name__ == '__main__':
    unittest.main()
