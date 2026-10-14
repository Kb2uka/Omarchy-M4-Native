#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
"""Desktop video check (#55 method): play CLIP full screen and muted in mpv, launched
through Hyprland like a user launch (vaapi-copy, gpu-next), then read mpv's own frame
drop counters over its IPC socket. Samples TCMz, PSTR and clocks every 0.5 s.
Run as the desktop user inside the session. usage: video.py CLIP"""
import json, os, socket, struct, subprocess, sys, time
clip = sys.argv[1]
sock = f'/run/user/{os.getuid()}/mpv-video-check.sock'
if os.path.exists(sock):
    os.unlink(sock)
cmd = (f'mpv --fs --mute=yes --hwdec=vaapi-copy --vo=gpu-next --keep-open=yes '
       f'--input-ipc-server={sock} {clip}')
subprocess.run(['hyprctl', 'dispatch', f'hl.dsp.exec_cmd("{cmd}")'], check=True, capture_output=True)
def ask(prop):
    s = socket.socket(socket.AF_UNIX); s.connect(sock)
    s.sendall(json.dumps({'command': ['get_property', prop]}).encode() + b'\n')
    data = b''
    while not data.endswith(b'\n'):
        data += s.recv(4096)
    s.close()
    return json.loads(data.splitlines()[-1]).get('data')
def smc():
    d = {}
    for line in open('/proc/j713_smc_read'):
        k, bits, ret = line.split()
        if int(ret) == 4: d[k] = struct.unpack('>f', bytes.fromhex(bits))[0]
    return d
for _ in range(100):
    if os.path.exists(sock): break
    time.sleep(0.1)
rows = []
while True:
    time.sleep(0.5)
    try:
        if ask('eof-reached'): break
    except OSError:
        break
    k = smc()
    rows.append((k.get('TCMz', 0), k.get('PSTR', 0),
                 int(open('/sys/devices/system/cpu/cpufreq/policy0/scaling_cur_freq').read()) // 1000))
result = {p: ask(p) for p in ('frame-drop-count', 'decoder-frame-drop-count', 'vo-delayed-frame-count',
                              'estimated-frame-number', 'hwdec-current')}
s = socket.socket(socket.AF_UNIX); s.connect(sock); s.sendall(b'{"command": ["quit"]}\n'); s.close()
n = len(rows)
result.update(TCMz_avg=round(sum(r[0] for r in rows) / n, 1), TCMz_max=round(max(r[0] for r in rows), 1),
              PSTR_avg=round(sum(r[1] for r in rows) / n, 2), P_MHz_avg=round(sum(r[2] for r in rows) / n))
print(json.dumps(result))
