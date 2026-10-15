#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""(root) Prove cpu-die-thermal throttles: lower its passive trip to TRIP_C, load all
ten cores for SECONDS, sample TCMz, cluster clocks and cooling states every 0.5 s,
then always restore the 95 C trip and stop the load. usage: zone_test.py TRIP_C SECONDS OUT.csv"""
import glob, struct, subprocess, sys, time
trip_c, secs, out = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]
rd = lambda p: open(p).read().strip()
zone = next(z for z in glob.glob('/sys/class/thermal/thermal_zone*') if rd(z + '/type') == 'cpu-die-thermal')
cdev = {rd(d + '/type'): d + '/cur_state' for d in glob.glob('/sys/class/thermal/cooling_device*')}
trip = zone + '/trip_point_0_temp'
original = rd(trip)
assert original == '95000', original
def smc():
    for line in open('/proc/j713_smc_read'):
        k, bits, ret = line.split()
        if k == 'TCMz' and int(ret) == 4:
            return struct.unpack('>f', bytes.fromhex(bits))[0]
loads = []
rows = []
try:
    open(trip, 'w').write(str(int(trip_c * 1000)))
    loads = [subprocess.Popen(['taskset', '-c', str(c), sys.executable, '-c', 'while True: pass']) for c in range(10)]
    t0 = time.time()
    with open(out, 'w') as f:
        f.write('t,TCMz,zone,Pmhz,Emhz,Pcool,Ecool\n')
        while time.time() - t0 < secs:
            time.sleep(0.5)
            r = (time.time() - t0, smc(), int(rd(zone + '/temp')) / 1000,
                 int(rd('/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq')) // 1000,
                 int(rd('/sys/devices/system/cpu/cpufreq/policy1/scaling_cur_freq')) // 1000,
                 int(rd(cdev['cpufreq-cpu0'])), int(rd(cdev['cpufreq-cpu1'])))
            rows.append(r); f.write('%.1f,%.1f,%.1f,%d,%d,%d,%d\n' % r); f.flush()
finally:
    for p in loads: p.kill()
    for p in loads: p.wait()
    open(trip, 'w').write(original)
    print('restored trip', rd(trip))
first = next((r for r in rows if r[5] or r[6]), None)
print(f'trip {trip_c} C: first throttle at {first[0]:.1f} s (TCMz {first[1]:.1f})' if first else 'never throttled')
late = rows[-40:]
print('last 20 s: TCMz %.1f, P %d-%d MHz, E %d-%d MHz, cooling P %s E %s' % (
    sum(r[1] for r in late) / len(late), min(r[3] for r in late), max(r[3] for r in late),
    min(r[4] for r in late), max(r[4] for r in late), sorted({r[5] for r in late}), sorted({r[6] for r in late})))
time.sleep(5)
print('after restore: cooling P %s E %s' % (rd(cdev['cpufreq-cpu0']), rd(cdev['cpufreq-cpu1'])))
