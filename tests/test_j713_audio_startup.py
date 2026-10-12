# SPDX-License-Identifier: MIT
import configparser
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AudioStartupOrderingTests(unittest.TestCase):
    def test_ready_audio_does_not_wait_for_unrelated_settle_job(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(ROOT / 'packaging/j713-audio/m4-j713-audio-register.service')
        after = set(unit['Unit']['After'].split())
        # The trigger has submitted device events. An unrelated device can
        # leave the global settle job pending while audio providers are ready.
        pending_jobs = {'systemd-udev-settle.service'}
        self.assertFalse(after & pending_jobs,
                         'Ready audio is still ordered behind the unrelated device queue')
        self.assertIn('systemd-udev-trigger.service', after)
        self.assertEqual(unit['Service']['Restart'], 'no')


if __name__ == '__main__':
    unittest.main()
