#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Self-checking workload that idles constantly: short compute bursts (SHA-256,
integer and float math with known answers) separated by 0.2-2 ms sleeps, so the
CPU it runs on enters and leaves idle thousands of times. Any wrong result means
state was lost across idle. usage: verify.py SECONDS"""
import hashlib, math, random, sys, time
secs = float(sys.argv[1])
buf = bytes(range(256)) * 64
want_h = hashlib.sha256(buf).hexdigest()
want_f = sum(math.sin(i * 0.001) * math.sqrt(i + 1.5) for i in range(2000))
want_i = sum(i * i ^ (i << 3) for i in range(5000))
it = bad = 0; t0 = time.time()
while time.time() - t0 < secs:
    if hashlib.sha256(buf).hexdigest() != want_h: bad += 1
    if sum(math.sin(i * 0.001) * math.sqrt(i + 1.5) for i in range(2000)) != want_f: bad += 1
    if sum(i * i ^ (i << 3) for i in range(5000)) != want_i: bad += 1
    it += 1
    time.sleep(random.uniform(0.0002, 0.002))
print(f'verify: {it} rounds, {bad} wrong results')
sys.exit(1 if bad else 0)
