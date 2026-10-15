#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Decode settle.py runs: P4 base, rise at 1.5 s and at the end of the hold, the fast heat
(gone 2 s after the drop back to P4), and the slow creep that stays. usage: settle_decode.py DIR"""
import glob, json, struct, sys


def f(bits): return struct.unpack('>f', bytes.fromhex(bits))[0]
def at(rows, t):  # first value at or after t (rows of (t, v))
    for x, v in rows:
        if x >= t: return v
    return rows[-1][1]
out = {}
for p in sorted(glob.glob(sys.argv[1] + '/n*-P*.json'), key=lambda s: (int(s.rsplit('/', 1)[1][1:].split('-')[0]), int(s.rsplit('-P', 1)[1].split('.')[0]))):
    r = json.load(open(p)); n, s = r['ncores'], r['pstate']
    pre = [(t, c) for t, c, _ in r['pre']]
    b = r['bench'].splitlines()
    hold = [(int(ms) / 1000, f(tc), (int(st, 16) >> 5) & 31) for ms, st, tc in (l.split() for l in b[2:]) if tc != '00000000']
    states = sorted(set(h[2] for h in hold))
    ht = [(t, c) for t, c, _ in hold]
    post = [(t - r['t_post'], c) for t, c, _ in r['post']]
    t0 = sum(c for t, c in pre if t >= 6) / max(1, len([1 for t, c in pre if t >= 6]))
    hold_end = ht[-1]; dur = hold_end[0]
    up15 = at(ht, 1.5) - t0
    p2 = at(post, 2.0); p5 = at(post, 5.0); pend = post[-1][1]
    fast_down = hold_end[1] - p2
    creep = p2 - t0
    out[(n, s)] = dict(t0=round(t0, 1), up15=round(up15, 1), end=round(hold_end[1], 1), dur=dur, fast_down=round(fast_down, 1), creep=round(creep, 1), states=states)
    print(f"n{n} P{s:2d}: P4 base {t0:5.1f} | +1.5s {up15:+5.1f} | held {dur:4.1f}s end {hold_end[1]:5.1f} (rise {hold_end[1]-t0:+5.1f}) | drop: 2s later {p2:5.1f} (fast {fast_down:+5.1f}), 5s {p5:5.1f}, 10s {pend:5.1f} | creep {creep:+4.1f} | DVFS cur {states}")
json.dump({f"{k[0]}-{k[1]}": v for k, v in out.items()}, open(sys.argv[1] + "/summary.json", "w"), indent=1)
