"""Exercise the local upload pipeline with generated QA audio only."""
import os
from pathlib import Path
import time
import requests

base = 'http://127.0.0.1:8765'
s = requests.Session()
s.trust_env = False
health = s.get(base + '/api/status', timeout=10).json()
headers = {'x-local-token': health['token']}
assert s.post(base + '/api/upload').status_code == 403, 'Missing token was accepted'
assert s.post(base + '/api/upload', headers={**headers, 'Origin':'https://example.org'}).status_code == 403, 'Cross-site request accepted'
assert s.post(base + '/api/upload', headers=headers, files={'file':('bad.wav',b'not audio')}).status_code == 400, 'Corrupt audio accepted'
source = Path(os.environ['LOCALAPPDATA']) / 'HotelVoice/qa/餐厅服务-合成测试.wav'
with source.open('rb') as f:
    response = s.post(base + '/api/upload', headers=headers, files={'file':('系统自测-合成餐厅语音.wav',f,'audio/wav')}, data={'employee':'系统自测（合成音频，非员工）','scene':'自动识别','note':'验证本地转写与报告流程的合成音频，不是真实服务记录。'}, timeout=30)
response.raise_for_status()
item = response.json()
print('Uploaded QA audio:', item['id'], flush=True)
assert s.get(base + '/api/records/' + item['id'] + '/audio', timeout=10).status_code == 200
r = s.post(base + '/api/records/' + item['id'] + '/analyze', headers=headers, timeout=15)
r.raise_for_status()
for _ in range(120):
    item = s.get(base + '/api/records/' + item['id'], timeout=15).json()
    print('Pipeline:', item['status'], item['progress'], flush=True)
    if item['status'] == 'done':
        assert item['segments'], 'No transcript'
        assert item['report'], 'No report'
        assert item['report']['scene'] == '餐厅服务', item['report']['scene']
        checked = s.patch(base + '/api/records/' + item['id'] + '/review', headers=headers, json={'reviewed':True})
        checked.raise_for_status()
        assert checked.json()['reviewed']
        if item['report']['todos']:
            updated = s.patch(base + '/api/records/' + item['id'] + '/tasks/0', headers=headers, json={'done':True})
            updated.raise_for_status()
            assert updated.json()['report']['todos'][0]['done']
            s.patch(base + '/api/records/' + item['id'] + '/tasks/0', headers=headers, json={'done':False}).raise_for_status()
        s.patch(base + '/api/records/' + item['id'] + '/review', headers=headers, json={'reviewed':False}).raise_for_status()
        print('PASS upload / playback / ASR / scene / report / review / task persistence', flush=True)
        print('Mode:', item['report'].get('mode'), 'Segments:', len(item['segments']), flush=True)
        break
    if item['status'] == 'error':
        raise RuntimeError(item['error'])
    time.sleep(5)
else:
    raise TimeoutError('Analysis exceeded 10 minutes')
