"""Start the local services without visible terminal windows."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(os.environ.get('HOTEL_VOICE_DATA',Path(os.environ['LOCALAPPDATA']) / 'HotelVoice'))
PROJECT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

def healthy(url):
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

def spawn(args, name):
    with (ROOT / (name + '.log')).open('ab') as log:
        child = subprocess.Popen(args, cwd=PROJECT, stdout=log, stderr=log, creationflags=FLAGS)
    print(name + ' started: ' + str(child.pid), flush=True)
    return child.pid

if __name__ == '__main__':
    ROOT.mkdir(exist_ok=True, parents=True)
    if not (PROJECT / 'dist-local/index.html').exists():
        raise SystemExit('Frontend build missing. Run npm run build:local first.')
    pids = {}
    model = ROOT / 'models/qwen/qwen2.5-1.5b-instruct-q4_k_m.gguf'
    runtime = list((ROOT / 'llama').rglob('llama-server.exe'))
    if runtime and model.exists() and not healthy('http://127.0.0.1:8766/health'):
        pids['llama'] = spawn([str(runtime[0]), '-m', str(model), '--host', '127.0.0.1', '--port', '8766', '-ngl', '99', '--device', 'Vulkan0', '-c', '12288', '-t', '6', '--parallel', '1', '--no-webui', '--log-disable'], 'model-server')
    if not healthy('http://127.0.0.1:8765/api/ping'):
        pids['app'] = spawn([sys.executable, '-m', 'uvicorn', 'local.server:app', '--host', '127.0.0.1', '--port', '8765', '--no-access-log'], 'app-server')
    if pids:
        target = ROOT / 'service-pids.json'
        prior = json.loads(target.read_text()) if target.exists() else {}
        target.write_text(json.dumps({**prior, **pids}), encoding='utf-8')
    for _ in range(30):
        if healthy('http://127.0.0.1:8765/api/ping'):
            print('Local app ready: http://127.0.0.1:8765', flush=True)
            if '--no-browser' not in sys.argv:
                webbrowser.open('http://127.0.0.1:8765')
            break
        time.sleep(1)
    else:
        raise SystemExit('Local app did not start. See app-server.log in ' + str(ROOT))
