# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import array
import cmath
import hashlib
import json
import math
from pathlib import Path
import struct
import sys

FRAMES = 73728
IMPULSE = 4096
RATE = 48000
AMP = struct.unpack('<f', struct.pack('<f', 0.0001))[0]
AMP_BITS = struct.unpack('<I', struct.pack('<f', AMP))[0]
AMP_ULP = struct.unpack('<f', struct.pack('<I', AMP_BITS + 1))[0] - AMP


def load(row, root):
    raw = (root / row['file']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == row['sha256']
    assert len(raw) == row['bytes'] == 4 * FRAMES * 4
    assert row['frames'] == FRAMES and row['impulse_frame'] == IMPULSE
    assert row['channels'] == 4 and row['sample_rate'] == RATE
    assert row['system_volume'] == 1 and row['impulses'] == 1
    assert row['callbacks'] == FRAMES // 256
    data = array.array('f')
    data.frombytes(raw)
    if sys.byteorder != 'little':
        data.byteswap()
    assert all(math.isfinite(x) for x in data)
    return [list(data[n * FRAMES:(n + 1) * FRAMES]) for n in range(4)]


def validate_lane(data, lane, bypass):
    assert all(x == 0 for ch in data for x in ch[:IMPULSE]), 'pre-impulse output'
    assert all(x == 0 for n, ch in enumerate(data) if n != lane for x in ch), 'cross-lane output'
    if bypass:
        assert abs(data[lane][IMPULSE] - AMP) <= AMP_ULP, 'bypass amplitude'
        assert all(x == 0 for x in data[lane][IMPULSE + 1:]), 'bypass tail'
    else:
        assert any(x != 0 for x in data[lane][IMPULSE:]), 'empty active response'


def select_length(response, bound=1e-6):
    full_l1 = math.fsum(abs(x) for x in response)
    assert full_l1 > 0
    candidates = []
    for length in (1024, 2048, 4096, 8192, 16384, 32768, 65536):
        tail_l1 = math.fsum(abs(x) for x in response[length:])
        candidates.append({'taps': length, 'tail_l1': tail_l1,
                           'tail_l1_relative': tail_l1 / full_l1,
                           'tail_peak': max(map(abs, response[length:]), default=0)})
    eligible = [r for r in candidates if r['tail_l1_relative'] <= bound]
    assert eligible, 'measured tail not negligible within candidate length'
    return eligible[0]['taps'], candidates


def wav_float32(path, samples):
    data = struct.pack('<' + 'f' * len(samples), *samples)
    fmt = struct.pack('<HHIIHH', 3, 1, RATE, RATE * 4, 4, 32)
    chunks = b'fmt ' + struct.pack('<I', len(fmt)) + fmt
    chunks += b'fact' + struct.pack('<II', 4, len(samples))
    chunks += b'data' + struct.pack('<I', len(data)) + data
    path.write_bytes(b'RIFF' + struct.pack('<I', len(chunks) + 4) + b'WAVE' + chunks)


def main():
    root = Path('native-transfer')
    rows = json.loads((root / 'transfers.json').read_text())
    assert len(rows) == 12
    bykey = {(r['bypass'], round(r['amplitude'], 7), r['lane']): r for r in rows}
    assert len(bykey) == 12
    out = Path('output')
    out.mkdir()
    lanes = []
    for lane in range(4):
        low = load(bykey[(0, 0.0001, lane)], root)
        high = load(bykey[(0, 0.0002, lane)], root)
        bypass = load(bykey[(1, 0.0001, lane)], root)
        validate_lane(low, lane, False)
        validate_lane(high, lane, False)
        validate_lane(bypass, lane, True)
        response = [x / AMP for x in low[lane][IMPULSE:]]
        difference = [hi / (2 * AMP) - lo / AMP for hi, lo in zip(high[lane][IMPULSE:], low[lane][IMPULSE:])]
        peak = max(map(abs, response))
        relative_error = max(map(abs, difference)) / peak
        assert relative_error < 1e-5, 'two-amplitude normalization mismatch'
        taps, tails = select_length(response)
        filename = f'tone3-lane{lane}-48000.wav'
        wav_float32(out / filename, response[:taps])
        quantized = struct.unpack('<' + 'f' * taps, struct.pack('<' + 'f' * taps, *response[:taps]))
        frequencies = []
        for frequency in (0, 20, 50, 80, 100, 250, 500, 1000, 2495, 2500, 5000, 10000, 20000, 23900):
            value = sum(x * cmath.exp(-2j * math.pi * frequency * n / RATE) for n, x in enumerate(response[:taps]))
            frequencies.append({'hz': frequency, 'db': 20 * math.log10(max(abs(value), 1e-300)), 'phase_deg': math.degrees(cmath.phase(value))})
        lanes.append({'lane': lane, 'first_coefficient': response[0], 'peak': peak,
                      'peak_index': max(range(len(response)), key=lambda n: abs(response[n])),
                      'two_amplitude_relative_peak_error': relative_error,
                      'bypass_amplitude_error_ulps': (bypass[lane][IMPULSE] - AMP) / AMP_ULP,
                      'measured_response_frames': len(response), 'measured_l1': math.fsum(map(abs, response)),
                      'tail_candidates': tails, 'selected_taps': taps,
                      'float32_quantization_l1': math.fsum(abs(a - b) for a, b in zip(quantized, response)),
                      'frequencies': frequencies, 'file': filename,
                      'sha256': hashlib.sha256((out / filename).read_bytes()).hexdigest()})
    result = {'scope': 'Tone3 only; exact native preset with SystemVolume=1; no dynamic/protection equivalence claim',
              'sample_rate': RATE, 'bypass_controls': 'all four identity within one input-float ULP, no pre-impulse or cross-lane output',
              'lanes': lanes}
    (out / 'transfer-analysis.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps([{k: r[k] for k in ('lane', 'first_coefficient', 'peak_index', 'selected_taps', 'two_amplitude_relative_peak_error')} for r in lanes]))


if __name__ == '__main__':
    main()
