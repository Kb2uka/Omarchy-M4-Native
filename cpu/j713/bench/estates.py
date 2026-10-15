#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""(root) Pin the E cluster to each offered state with the userspace governor, run a
counting loop on CPU 1 and report the clock the driver reads back (DVFS_STATUS)
and the measured throughput. Restores schedutil."""
import os, subprocess, sys, time
P = '/sys/devices/system/cpu/cpufreq/policy1'
LOOP = 'import time\nn=0;t=time.monotonic()\nwhile time.monotonic()-t<3:\n  for _ in range(20000): n+=1\nprint(n/3)'
freqs = [int(f) for f in open(f'{P}/scaling_available_frequencies').read().split()]
old = open(f'{P}/scaling_governor').read().strip()
w = lambda f, v: open(f'{P}/{f}', 'w').write(str(v))
try:
    w('scaling_governor', 'userspace')
    base = None
    for f in freqs:
        w('scaling_setspeed', f); time.sleep(0.3)
        p = subprocess.run(['taskset', '-c', '1', sys.executable, '-c', LOOP], capture_output=True, text=True)
        rate = float(p.stdout); cur = int(open(f'{P}/cpuinfo_cur_freq').read())
        base = base or (rate, f)
        print(f'requested {f//1000} MHz  read back {cur//1000} MHz  throughput {rate/base[0]:.3f}x (clock {f/base[1]:.3f}x)')
finally:
    w('scaling_governor', old)
    print('restored', open(f'{P}/scaling_governor').read().strip())
