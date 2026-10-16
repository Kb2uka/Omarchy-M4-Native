#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""(root) Settled heat of P states (#66 heat model). For each STATE: with NCORES P cores busy at
P4 (1800 MHz) for PRE s, j713_pstate_bench holds STATE for HOLD_MS (95 C abort), then the cores stay
busy at P4 for POST s. TCMz and the cluster's DVFS state are logged throughout.
Needs j713_smc_read loaded and j713_pstate_bench.ko (both built from this directory) next to this
script. usage: settle.py STATES NCORES HOLD_MS OUT_DIR   (env COOL_C: start below this, default 58)"""
import json, os, struct, subprocess, sys, time
steps, ncores, hold_ms, out = [int(s) for s in sys.argv[1].split(',')], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
KO = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'j713_pstate_bench.ko')
PDIR = '/sys/devices/system/cpu/cpufreq/policy0'
CPUS = [0, 7, 8, 9][:ncores]
PRE, POST, COOL = 8.0, 10.0, float(os.environ.get('COOL_C', 58))
LOOP = 'while True: pass'
os.makedirs(out, exist_ok=True)
def tcmz():
    for line in open('/proc/j713_smc_read'):
        k, bits, ret = line.split()
        if k == 'TCMz' and int(ret) == 4:
            return round(struct.unpack('>f', bytes.fromhex(bits))[0], 2)
def cur_khz():
    return int(open(f'{PDIR}/cpuinfo_cur_freq').read())
def wset(path, val):
    with open(path, 'w') as f:
        f.write(str(val))
def sample(seconds, t0):
    rows, end = [], time.monotonic() + seconds
    while time.monotonic() < end:
        rows.append((round(time.monotonic() - t0, 2), tcmz(), cur_khz() // 1000))
        time.sleep(0.25)
    return rows
old_gov = open(f'{PDIR}/scaling_governor').read().strip()
loads = []
try:
    wset(f'{PDIR}/scaling_governor', 'userspace'); wset(f'{PDIR}/scaling_setspeed', 1800000); time.sleep(0.5)
    for step in steps:
        end = time.monotonic() + 600
        while tcmz() > COOL and time.monotonic() < end:
            time.sleep(2)
        rec = {'pstate': step, 'ncores': ncores, 'hold_ms': hold_ms, 'start_c': tcmz()}
        loads = [subprocess.Popen([sys.executable, '-c', LOOP]) for _ in CPUS]
        for p, c in zip(loads, CPUS):
            os.sched_setaffinity(p.pid, {c})
        t0 = time.monotonic()
        rec['pre'] = sample(PRE, t0)
        r = subprocess.run(['insmod', KO, 'cluster=p', f'pstate={step}', 'abort_c=95', f'max_ms={hold_ms}'], capture_output=True, text=True)
        if r.returncode:
            rec['error'] = r.stderr.strip()
        else:
            rec['t_hold'] = round(time.monotonic() - t0, 2)
            give_up = time.monotonic() + hold_ms / 1000 + 10  # the module stops itself at hold_ms
            while 'state stopped' not in open('/proc/j713_pstate_bench').read().splitlines()[1]:
                if time.monotonic() > give_up:
                    rec['error'] = 'bench did not report a stop'
                    break
                time.sleep(0.25)
            rec['bench'] = open('/proc/j713_pstate_bench').read()
            subprocess.run(['rmmod', 'j713_pstate_bench'], check=True)
            rec['t_post'] = round(time.monotonic() - t0, 2)
            rec['post'] = sample(POST, t0)
        for p in loads:
            p.kill(); p.wait()
        loads = []
        json.dump(rec, open(f'{out}/n{ncores}-P{step}.json', 'w'))
        print(f"n{ncores} P{step}: {rec.get('error') or rec['bench'].splitlines()[1]}", flush=True)
finally:
    for p in loads:
        p.kill()
    if os.path.exists('/sys/module/j713_pstate_bench'):
        subprocess.run(['rmmod', 'j713_pstate_bench'])
    wset(f'{PDIR}/scaling_governor', old_gov)
