"""Speaker-protection level writes must fit the daemon's renewal window.

Each amp transfer on the J713 I2C bus takes about 0.37 ms. During the startup
ramp, speakersafetyd changes up to four amp levels per 21.3 ms period and must
renew its lease with at most 512 frames (10.7 ms) queued. Seven transfers per
level change (write, read-back, a second read-back, then four limit reads)
overran that window at boot; five fit.
"""
from pathlib import Path
import re
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'audio/j713/kernel/j713-audio.c'


def code(text):
    """Drop C comments so prose naming a function is not counted as a call."""
    return re.sub(r'/\*.*?\*/|//[^\n]*', '', text, flags=re.S)


def function(text, name):
    start = re.search(r'^(?:static )?int ' + re.escape(name) + r'\(', text, re.M)
    if not start:
        raise AssertionError('missing function ' + name)
    body = text.index('{', start.end())
    depth, pos = 0, body
    while True:
        depth += {'{': 1, '}': -1}.get(text[pos], 0)
        if depth == 0:
            return text[body:pos + 1]
        pos += 1


def calls(body, *names):
    return [m.group(1) for m in re.finditer(r'\b(' + '|'.join(names) + r')\(', body)]


class LevelWriteTraffic(unittest.TestCase):
    def setUp(self):
        self.text = code(SOURCE.read_text())
        self.level = function(self.text, 'j713_audio_level')

    def test_level_write_is_one_store_then_full_verification(self):
        self.assertEqual(calls(self.level, 'amp_store', 'amp_write', 'amp_read', 'verify_limits'),
                         ['amp_store', 'verify_limits'])

    def test_successful_store_is_always_verified(self):
        lines = [l.strip() for l in self.level.splitlines()]
        # Unchanged puts must leave before any bus traffic.
        sequence = ['if (write && *value == *stored) goto out;',
                    'old = *stored;',
                    'if (write) ret = amp_store(a, index, reg, *value, gen);',
                    'if (!ret && write) *stored = *value;',
                    'if (!ret) ret = verify_limits(a, index, mode, gen);',
                    'if (ret && write) *stored = old;',
                    'if (!ret) *value = *stored;']
        for line in sequence:
            self.assertEqual(lines.count(line), 1, line)
        self.assertEqual([lines.index(line) for line in sequence],
                         sorted(lines.index(line) for line in sequence))

    def test_verification_reads_every_limit_register(self):
        verify = function(self.text, 'verify_limits')
        registers = re.findall(r'expect_byte\(a, i, (0x[0-9a-f]+|\d+),', verify)
        self.assertEqual(sorted(int(r, 0) for r in registers), [0, 2, 3, 0x1a])
        self.assertIn('a->gain[i]', verify)
        self.assertIn('a->dvc[i]', verify)

    def test_failed_verification_restores_the_stored_level(self):
        self.assertRegex(self.level, r'old = \*stored;')
        self.assertRegex(self.level, r'if \(ret && write\) \*stored = old;')
        store = self.level.index('amp_store(')
        commit = self.level.index('*stored = *value;', store)
        self.assertLess(commit, self.level.index('verify_limits('),
                        'verify_limits() must compare against the new value')

    def test_store_does_not_read_back(self):
        self.assertEqual(calls(function(self.text, 'amp_store'), 'amp_read', 'expect_byte'), [])

    def test_worst_case_period_fits_the_renewal_window(self):
        cost = {'amp_store': 1, 'amp_write': 2, 'amp_read': 1,
                'verify_limits': len(re.findall(r'expect_byte\(', function(self.text, 'verify_limits')))}
        transfers = sum(cost[name] for name in calls(self.level, *cost))
        amps, transfer_ms, window_ms = 4, 0.37, 1024 / 2 / 48
        self.assertLess(amps * transfers * transfer_ms, 0.75 * window_ms)


if __name__ == '__main__':
    unittest.main()
