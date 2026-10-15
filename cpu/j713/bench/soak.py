#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""All-core soak: one busy loop pinned to each CPU in CPUS for SECONDS, sampling every
0.5 s: SMC TCMz/PSTR (via /proc/j713_smc_read), cluster clocks, thermal zone cooling
states. Kills the load at once if TCMz reaches ABORT_C. usage: soak.py SECONDS CPUS ABORT_C OUT.csv"""
import glob, os, signal, struct, subprocess, sys, time
secs, cpus, abort_c, out = float(sys.argv[1]), [int(c) for c in sys.argv[2].split(',')], float(sys.argv[3]), sys.argv[4]
def smc():
    d = {}
    for line in open('/proc/j713_smc_read'):
        k, bits, ret = line.split()
        if int(ret) == 4: d[k] = struct.unpack('>f', bytes.fromhex(bits))[0]
    return d
rd = lambda p: open(p).read().strip()
cdev = {rd(f'{d}/type'): f'{d}/cur_state' for d in glob.glob('/sys/class/thermal/cooling_device*')}
loads = [subprocess.Popen(['taskset', '-c', str(c), sys.executable, '-c', 'while True: pass']) for c in cpus]
reason = 'time'; peak = 0; rows = []
try:
    t0 = time.time()
    with open(out, 'w') as f:
        f.write('t,TCMz,PSTR,Pmhz,Emhz,Pcool,Ecool\n')
        while time.time() - t0 < secs:
            time.sleep(0.5)
            k = smc(); t = k.get('TCMz', 0); peak = max(peak, t)
            r = (time.time() - t0, t, k.get('PSTR', 0), int(rd('/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq')) // 1000,
                 int(rd('/sys/devices/system/cpu/cpufreq/policy1/scaling_cur_freq')) // 1000,
                 rd(cdev.get('cpufreq-cpu0', '/dev/null')) if 'cpufreq-cpu0' in cdev else '-', rd(cdev['cpufreq-cpu1']) if 'cpufreq-cpu1' in cdev else '-')
            rows.append(r); f.write('%.1f,%.1f,%.2f,%d,%d,%s,%s\n' % r); f.flush()
            if t >= abort_c:
                reason = 'abort'; break
finally:
    for p in loads: p.kill()
    for p in loads: p.wait()
last = rows[-60:] if len(rows) >= 60 else rows
avg = lambda rs, i: sum(r[i] for r in rs) / len(rs)
print(f'soak cpus={cpus} {rows[-1][0]:.0f}s stop={reason} peak TCMz {peak:.1f} | last 30 s: TCMz {avg(last,1):.1f} PSTR {avg(last,2):.2f} W P {avg(last,3):.0f} MHz E {avg(last,4):.0f} MHz')
