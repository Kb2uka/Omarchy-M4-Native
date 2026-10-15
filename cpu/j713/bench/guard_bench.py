#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""(root) Guarded boost check: busy loops pinned to CPUS for SECONDS under schedutil
with j713_pguard in charge; samples TCMz, PSTR, P/E clocks and the guard cap every
0.25 s and counts loop throughput. Kills the load at once if TCMz reaches KILL_C.
JOIN_S delays every CPU after the first (one core boosts, then the rest join at once);
DUTY 'ON_MS/OFF_MS' makes the loops bursty instead of continuous.
usage: guard_bench.py LABEL CPUS SECONDS [KILL_C [JOIN_S [DUTY]]]"""
import multiprocessing as mp, os, struct, sys, time
label, cpus, secs = sys.argv[1], [int(c) for c in sys.argv[2].split(',')], float(sys.argv[3])
kill_c = float(sys.argv[4]) if len(sys.argv) > 4 else 105.0
join_s = float(sys.argv[5]) if len(sys.argv) > 5 else 0.0
on_ms, off_ms = (float(x) for x in sys.argv[6].split('/')) if len(sys.argv) > 6 else (0.0, 0.0)
rd = lambda p: open(p).read().strip()
def smc():
    d = {}
    for line in open('/proc/j713_smc_read'):
        k, bits, ret = line.split()
        if int(ret) == 4: d[k] = struct.unpack('>f', bytes.fromhex(bits))[0]
    return d
def guard():
    g = {}
    for line in open('/proc/j713_pguard'):
        f = line.split()
        g.update(zip(f[0::2], f[1::2]))
    return g
def loop(cpu, counter, delay):
    os.sched_setaffinity(0, {cpu})
    time.sleep(delay)
    n = 0
    while True:
        until = time.monotonic() + on_ms / 1000
        while True:
            for _ in range(20000): n += 1
            with counter.get_lock(): counter.value += 20000
            if on_ms and time.monotonic() >= until: break
        time.sleep(off_ms / 1000)
if __name__ == '__main__':
    mp = mp.get_context('fork')
    counters = [mp.Value('Q', 0) for _ in cpus]
    procs = [mp.Process(target=loop, args=(c, k, join_s if i else 0.0), daemon=True)
             for i, (c, k) in enumerate(zip(cpus, counters))]
    for p in procs: p.start()
    t0 = time.time(); rows = []; reason = 'time'
    try:
        while time.time() - t0 < secs:
            time.sleep(0.25)
            k, g = smc(), guard()
            r = (round(time.time() - t0, 2), k.get('TCMz', 0), k.get('PSTR', 0),
                 int(rd('/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq')) // 1000,
                 int(rd('/sys/devices/system/cpu/cpufreq/policy1/scaling_cur_freq')) // 1000,
                 int(g['cap_khz']) // 1000, int(g['base_mc']) / 1000)
            rows.append(r)
            if r[1] >= kill_c:
                reason = 'KILL'; break
    finally:
        for p in procs: p.kill()
    elapsed = time.time() - t0
    rate = sum(c.value for c in counters) / elapsed / len(cpus)
    peak = max(r[1] for r in rows)
    last = rows[-8:]
    print(f'{label}: cpus={cpus} {elapsed:.1f}s stop={reason} peak TCMz {peak:.1f} | per-core loop rate {rate/1e6:.2f} M/s | '
          f'end: TCMz {last[-1][1]:.1f} PSTR {sum(r[2] for r in last)/len(last):.1f} W P {last[-1][3]} MHz '
          f'E {last[-1][4]} MHz cap {last[-1][5]} MHz base {last[-1][6]:.1f}')
    for r in rows[::4]:
        print('  t=%5.1f TCMz %5.1f PSTR %5.1f P %4d E %4d cap %4d base %5.1f' % r)
