# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import hashlib
import json
from pathlib import Path
import random
import struct
import subprocess

FRAMES = 73728
assert hashlib.sha256(Path('/System/Library/Components/AudioDSP.component/Contents/MacOS/AudioDSP').read_bytes()).hexdigest() == json.loads(Path('owner-inputs.json').read_text())['AudioDSP']
for name, sha in {'Tone1':'6c90f3d56b362ac1833ade1684cc63a77dbb126130fe803d36d6c414294e98c1', 'Tone2':'d0924cea31ddd106155f67d8856d667b804fcfd59439c5c0e8ff645258159f57','Tone3':'0a6db4c22206053c84477fbd67c640f3e12fc132a4de17ef42fe8a3d4dae1c5b'}.items():
    assert hashlib.sha256(Path('decoded',name+'.aupreset').read_bytes()).hexdigest() == sha
rows=[]
def run(name, preset, lane=0, amplitude='0.0001', source='impulse', subset='none'):
    output=Path('output',name+'.f32')
    args=['./measure-memory','decoded/'+preset+'.aupreset',str(output),str(lane),amplitude,'0',source,subset]
    proc=subprocess.run(args,capture_output=True,text=True,timeout=30)
    Path('output',name+'.stdout').write_text(proc.stdout)
    Path('output',name+'.stderr').write_text(proc.stderr)
    if proc.returncode: raise RuntimeError((name,proc.returncode,proc.stderr))
    row=json.loads(proc.stdout)
    row.update(name=name,argv=args,file=output.name,sha256=hashlib.sha256(output.read_bytes()).hexdigest(),bytes=output.stat().st_size)
    rows.append(row)
    print(json.dumps(row),flush=True)
    return output
for preset,subset in [('Tone1','hp90'),('Tone2','hp80')]:
    for amp in ['0.0001','0.0002']:
        for lane in [0,1]: run(subset+'-amp'+amp+'-lane'+str(lane),preset,lane,amp,subset=subset)
for amplitude in [0.0001,0.125]:
    rng=random.Random(713)
    data=[]
    for lane in range(4):
        data.extend([0.]*4096+[amplitude*(2*rng.random()-1) for _ in range(8192)]+[0.]*(FRAMES-12288))
    path=Path('output','noise-'+str(amplitude)+'.f32')
    path.write_bytes(struct.pack('<'+'f'*len(data),*data))
    run('tone3-noise-'+str(amplitude),'Tone3',source=str(path))
source='output/noise-0.125.f32'
hp90=run('chain-hp90','Tone1',source=source,subset='hp90')
hp80=run('chain-hp80','Tone2',source=str(hp90),subset='hp80')
data=list(struct.unpack('<'+'f'*(4*FRAMES),hp80.read_bytes()))
data[2*FRAMES:3*FRAMES]=data[:FRAMES]
data[3*FRAMES:]=data[FRAMES:2*FRAMES]
split=Path('output/chain-split.f32')
split.write_bytes(struct.pack('<'+'f'*len(data),*data))
run('chain-tone3','Tone3',source=str(split))
Path('output/memory-runs.json').write_text(json.dumps(rows,indent=2)+'\n')
Path('output/files.sha256').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(Path('output').glob('*.f32'))))
