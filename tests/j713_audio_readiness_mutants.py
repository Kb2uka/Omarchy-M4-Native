# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Run in a disposable leased fleet checkout; restore each mutated helper."""
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/m4-j713-audio-ready.py'
CASES = (
    ('first-lease-pending',
     'if initial and fields.get("phase") == "2" and fields.get("sequence") == "0":',
     'if False:'),
    ('configuration-deadline',
     'require(now < int(fields["deadline_ns"]) <= now + 2_000_000_000,',
     'require(True,'),
    ('initial-epoch',
     'require(first_generation is None or previous["generation"] == first_generation,',
     'require(True,'),
    ('fresh-low',
     '"gpio_hw": "00070a02", "gpio_cache": "00070a02", "deadline_ns": "0"',
     '"deadline_ns": "0"'),
    ('steady-lease',
     'require(now < deadline <= now + 50_000_000, "Lease expired or outside qualified 50 ms horizon")',
     'pass'),
    ('closed-tx',
     'require(read(root, PCM + "p/sub0/status") == "closed", "First lease requires closed TX")',
     'pass'),
)


def main():
    original = SOURCE.read_bytes()
    text = original.decode()
    for name, before, after in CASES:
        assert text.count(before) == 1, (name, 'Mutation anchor changed')
        try:
            SOURCE.write_text(text.replace(before, after))
            result = subprocess.run(
                [sys.executable, '-B', '-m', 'unittest', 'test_m4_j713_audio_ready'],
                cwd=ROOT / 'tests', text=True, capture_output=True)
            print('MUTANT', name, 'rc', result.returncode, flush=True)
            print(result.stdout + result.stderr, flush=True)
            assert result.returncode and 'FAIL:' in result.stderr, 'Mutant survived or fixture failed'
            assert 'ERROR:' not in result.stderr, 'Fixture error is not a rejected mutant'
        finally:
            SOURCE.write_bytes(original)


if __name__ == '__main__':
    main()
