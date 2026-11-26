"""Check recurring speaker labels using locally synthesized known A/B turns."""
import json
import os
from pathlib import Path
import av
import numpy as np
import wave
from local.speech import recognize_turns, RATE

root=Path(os.environ['LOCALAPPDATA'])/'HotelVoice'
qa=root/'qa/speakers'

def load(path):
    result=[]
    with av.open(str(path)) as container:
        converter=av.AudioResampler(format='fltp',layout='mono',rate=RATE)
        for frame in container.decode(audio=0):
            result.extend(f.to_ndarray().flatten() for f in converter.resample(frame))
        result.extend(f.to_ndarray().flatten() for f in converter.resample(None))
    return np.concatenate(result)

pieces=[]
truth=[]
offset=0
for name,person in [('a1.wav','A'),('b1.wav','B'),('a2.wav','A'),('b2.wav','B')]:
    audio=load(qa/name)
    pieces.append(audio)
    truth.append({'start':offset/RATE,'end':(offset+len(audio))/RATE,'person':person})
    gap=np.zeros(int(.6*RATE),np.float32)
    pieces.append(gap)
    offset+=len(audio)+len(gap)
values=np.concatenate(pieces)
with wave.open(str(qa/'双人轮流对话-合成测试.wav'),'wb') as out:
    out.setnchannels(1);out.setsampwidth(2);out.setframerate(RATE)
    out.writeframes((values*32767).astype('<i2').tobytes())
result=recognize_turns(values,root/'models',language='zh',expected_speakers=0)
assignments=[]
for t in truth:
    totals={}
    for turn in result['speakerTurns']:
        overlap=max(0,min(turn['end'],t['end'])-max(turn['start'],t['start']))
        totals[turn['speakerId']]=totals.get(turn['speakerId'],0)+overlap
    assignments.append(max(totals,key=totals.get) if totals else None)
assert assignments[0] and assignments[0]==assignments[2],assignments
assert assignments[1] and assignments[1]==assignments[3],assignments
assert assignments[0]!=assignments[1],assignments
assert [s['speakerId'] for s in result['segments']]==assignments, result['segments']
(qa/'test-result.json').write_text(json.dumps({'truth':truth,'assignments':assignments,'result':result},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'speakers':len(result['speakers']),'segments':len(result['segments']),'assignments':assignments,'pass':True}),flush=True)
