# SPDX-License-Identifier: MIT
# Copyright (c) 2026 KB2UKA
import hashlib
import json
import math
from pathlib import Path
import struct
import numpy as np
from analyze_transfer import AMP, FRAMES, IMPULSE, load, select_length, wav_float32

RATE=48000

def fft_convolve(a,b):
    size=len(a)+len(b)-1
    fftsize=1 << (size-1).bit_length()
    return np.fft.irfft(np.fft.rfft(a,fftsize)*np.fft.rfft(b,fftsize),fftsize)[:size]

def float_coeff(values):
    return np.asarray(values,dtype='<f4').astype(np.float64)

def compare(actual,predicted):
    peak=float(np.max(np.abs(actual)))
    assert peak>0
    diff=np.asarray(actual,dtype=float)-predicted[:len(actual)]
    result={'peak_output':peak,'peak_error':float(np.max(np.abs(diff))),
            'relative_peak_error':float(np.max(np.abs(diff)))/peak,
            'rms_error':float(np.sqrt(np.mean(diff*diff)))}
    result['strict_1e4_relative_pass'] = result['relative_peak_error'] < 1e-4
    return result

def main():
    out=Path('output'); out.mkdir()
    root=Path('native-memory')
    for line in (root/'files.sha256').read_text().splitlines():
        sha,name=line.split('  ')
        assert hashlib.sha256((root/name).read_bytes()).hexdigest()==sha
    rows={r['name']:r for r in json.loads((root/'memory-runs.json').read_text())}
    def samples(name):
        raw=(root/(name+'.f32')).read_bytes()
        values=np.frombuffer(raw,dtype='<f4').reshape(4,FRAMES)
        assert np.isfinite(values).all()
        return values.astype(np.float64)
    hps={}; hp_results=[]
    for label in ('hp90','hp80'):
        low=load(rows[label+'-amp0.0001-lane0'],root)
        other=load(rows[label+'-amp0.0001-lane1'],root)
        high=load(rows[label+'-amp0.0002-lane0'],root)
        high_other=load(rows[label+'-amp0.0002-lane1'],root)
        for values,lane in ((low,0),(other,1),(high,0),(high_other,1)):
            assert all(x==0 for c in values for x in c[:IMPULSE])
            assert all(x==0 for n,c in enumerate(values) if n!=lane for x in c)
        assert low[0]==other[1] and high[0]==high_other[1]
        h=np.asarray(low[0][IMPULSE:])/AMP
        relative=float(np.max(np.abs(np.asarray(high[0][IMPULSE:])/(2*AMP)-h)))/float(np.max(np.abs(h)))
        assert relative<1e-5
        length,tails=select_length(h)
        hps[label]=float_coeff(h[:length])
        wav_float32(out/(label+'-48000.wav'),hps[label])
        hp_results.append({'stage':label,'taps':length,'tails':tails,'two_amplitude_relative_error':relative,'identical_lr':True})
    tone3=[]; tone3rows=json.loads(Path('native-transfer/transfers.json').read_text())
    for lane in range(4):
        row=next(r for r in tone3rows if r['bypass']==0 and r['amplitude']<0.00015 and r['lane']==lane)
        h=np.asarray(load(row,Path('native-transfer'))[lane][IMPULSE:])/AMP
        length,_=select_length(h)
        tone3.append(float_coeff(h[:length]))
    native_checks=[]
    for amplitude in ('0.0001','0.125'):
        x=samples('noise-'+amplitude); actual=samples('tone3-noise-'+amplitude)
        for lane in range(4):
            result=compare(actual[lane],fft_convolve(x[lane],tone3[lane]))
            result.update(stage='Tone3',amplitude=amplitude,lane=lane)
            native_checks.append(result)
    x=samples('noise-0.125'); actual=samples('chain-tone3'); filters=[]
    for label, native_input, native_output in [('hp90','noise-0.125','chain-hp90'),('hp80','chain-hp90','chain-hp80')]:
        for lane in (0,1):
            result=compare(samples(native_output)[lane],fft_convolve(samples(native_input)[lane],hps[label]))
            result.update(stage=label,amplitude='0.125',lane=lane)
            native_checks.append(result)
    for lane in range(4):
        result=compare(actual[lane],fft_convolve(samples('chain-split')[lane],tone3[lane]))
        result.update(stage='Tone3 on native HP output',amplitude='0.125',lane=lane)
        native_checks.append(result)
    pre=fft_convolve(hps['hp90'],hps['hp80'])
    for lane in range(4):
        full=fft_convolve(pre,tone3[lane])
        length,tails=select_length(full)
        h=float_coeff(full[:length])
        name='j713-static-minimal-lane'+str(lane)+'-48000.wav'
        wav_float32(out/name,h)
        result=compare(actual[lane],fft_convolve(x[lane%2],h))
        result.update(stage='HP90 + HP80 + Tone3',amplitude='0.125',lane=lane)
        native_checks.append(result)
        response=[]
        for frequency in (0,20,50,80,90,100,250,500,1000,2495,2500,5000,10000,20000):
            z=np.exp(-2j*np.pi*frequency*np.arange(len(h))/RATE)
            value=np.sum(h*z)
            response.append({'hz':frequency,'db':20*math.log10(max(abs(value),1e-300)), 'phase_deg':float(np.angle(value,deg=True))})
        filters.append({'lane':lane,'input_lane':lane%2,'taps':length,'tail_candidates':tails,
                        'l1_bound':float(np.sum(np.abs(h))), 'file':name,
                        'sha256':hashlib.sha256((out/name).read_bytes()).hexdigest(),'frequencies':response})
    report={'installable':False,'accepted_protection_profile':False,
        'scope':'Measured native HP-only subsets Tone1 filter0 and Tone2 filter5, then exact Tone3; SystemVolume=1 at every stage',
        'omits':'All other Tone1/Tone2 filters and dynamic effects, Norm/VirtBass/XTC/Comp/volume taper/ControlFreak/LSM',
        'numpy_version':np.__version__,'highpasses':hp_results,'native_memory_comparisons':native_checks,'filters':filters}
    (out/'qualified-memory.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'comparisons':native_checks,'filters':[{k:r[k] for k in ('lane','taps','l1_bound','sha256')} for r in filters]},indent=2))

if __name__=='__main__': main()
