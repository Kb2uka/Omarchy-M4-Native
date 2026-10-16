#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Sample SMC keys (via /proc/j713_smc_read), cluster clocks and CPU busy % every 0.5 s.
usage: sample.py SECONDS LABEL  -> prints CSV rows and a summary line."""
import glob, struct, sys, time
secs, label = float(sys.argv[1]), sys.argv[2]
def smc():
    out = {}
    for line in open('/proc/j713_smc_read'):
        k, bits, ret = line.split()
        if int(ret) == 4:
            out[k] = struct.unpack('>f', bytes.fromhex(bits))[0]
    return out
def freqs():
    return [int(open(f'/sys/devices/system/cpu/cpufreq/{p}/scaling_cur_freq').read()) // 1000 for p in ('policy0', 'policy1')]
def stat():
    f = open('/proc/stat').readline().split()[1:]
    v = list(map(int, f)); idle = v[3] + v[4]
    return sum(v), idle
rows = []
t0 = time.time(); s0 = stat()
print('t,TCMz,PSTR,PDTR,PHPC,Pmhz,Emhz,busy')
while time.time() - t0 < secs:
    time.sleep(0.5)
    s1 = stat(); d = s1[0] - s0[0]; busy = 100 * (1 - (s1[1] - s0[1]) / d) if d else 0; s0 = s1
    k = smc(); p, e = freqs()
    r = (time.time() - t0, k.get('TCMz', 0), k.get('PSTR', 0), k.get('PDTR', 0), k.get('PHPC', 0), p, e, busy)
    rows.append(r); print('%.1f,%.1f,%.2f,%.2f,%.4f,%d,%d,%.1f' % r, flush=True)
n = len(rows); avg = lambda i: sum(r[i] for r in rows) / n
print(f'SUMMARY {label}: n={n} TCMz avg {avg(1):.1f} max {max(r[1] for r in rows):.1f} | PSTR avg {avg(2):.2f} W | PDTR avg {avg(3):.2f} W | P {avg(5):.0f} MHz E {avg(6):.0f} MHz | busy {avg(7):.1f}%')
