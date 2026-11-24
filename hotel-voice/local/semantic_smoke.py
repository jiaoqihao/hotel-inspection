"""Verify local generative reports for all three hotel scenes."""
import json
import os
from pathlib import Path
import time
import requests

s=requests.Session()
s.trust_env=False
base='http://127.0.0.1:8765'
health=s.get(base+'/api/status',timeout=10).json()
if not health['semantic']:
    raise SystemExit('Local Qwen model is not ready yet.')
headers={'x-local-token':health['token']}
cases=[('餐厅服务','餐厅服务-合成测试.wav'),('会议','会议-合成测试.wav'),('销售沟通','销售-合成测试.wav')]
ids=[]
for scene,filename in cases:
    source=Path(os.environ['LOCALAPPDATA'])/'HotelVoice/qa'/filename
    with source.open('rb') as f:
        r=s.post(base+'/api/upload',headers=headers,files={'file':('系统自测-'+scene+'-本地语义.wav',f,'audio/wav')},data={'employee':'系统自测（合成语音）','scene':'自动识别','note':'自动验证用的合成音频，不是真实员工录音。'},timeout=20)
    r.raise_for_status()
    id=r.json()['id']
    s.post(base+'/api/records/'+id+'/analyze',headers=headers,timeout=10).raise_for_status()
    ids.append((scene,id))
    print('Queued:',scene,id,flush=True)
for scene,id in ids:
    for _ in range(180):
        r=s.get(base+'/api/records/'+id,timeout=10).json()
        if r['status']=='error':
            raise RuntimeError(scene+': '+r['error'])
        if r['status']=='done':
            assert r['report']['mode']=='semantic'
            assert r['report']['scene']==scene, json.dumps(r['report'],ensure_ascii=True)
            assert r['report']['checks'] or r['report']['insights'] or r['report']['todos'], 'No grounded findings'
            assert r['report']['todos'], 'Missed explicit follow-up actions'
            if scene == '销售沟通':
                assert any('6000' in e['value'] for e in r['report']['insights']), 'Missed explicit budget'
                assert any('停车' in e['value'] for e in r['report']['insights']), 'Missed customer concern'
            print(json.dumps({'scene':scene,'checks':len(r['report']['checks']),'todos':len(r['report']['todos']),'insights':len(r['report']['insights']),'summary':r['report']['summary']},ensure_ascii=True),flush=True)
            break
        time.sleep(3)
    else:
        raise TimeoutError('Semantic test timed out')
print('PASS: local semantic reports for restaurant / meeting / sales',flush=True)
