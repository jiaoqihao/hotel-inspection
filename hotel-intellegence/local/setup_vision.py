"""Download and verify both components of the local multimodal model."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import time

import requests

MODEL = 'Qwen2.5-VL-3B-Instruct-Q4_K_M.gguf'
PROJECTOR = 'mmproj-Qwen2.5-VL-3B-Instruct-Q8_0.gguf'
FILES = {
    MODEL: (1929901056, 'd02fe9b69ad8cadbbd228e387667af66612c44bed29ffc8eb1e7caf9ac486c12'),
    PROJECTOR: (844757728, '980c9b2f78c04e6cff93d277ada09e768394f112d75db3b4e9dea8a69f9fb904'),
}
ROOT = Path(os.environ.get('HOTEL_VOICE_DATA', Path(os.environ['LOCALAPPDATA']) / 'HotelVoice')) / 'models/vision'
BASE = 'https://modelscope.cn/models/ggml-org/Qwen2.5-VL-3B-Instruct-GGUF/resolve/master/'
CHUNK = 4 * 1024 * 1024


def download(name, size, checksum):
    target = ROOT / name
    if target.is_file():
        with target.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() == checksum:
                return
    parts = ROOT / (name + '.chunks')
    parts.mkdir(parents=True, exist_ok=True)
    offsets = range(0, size, CHUNK)

    def fetch(start):
        end = min(start + CHUNK, size) - 1
        path = parts / str(start)
        if path.exists() and path.stat().st_size == end - start + 1:
            return
        for attempt in range(5):
            try:
                with requests.get(BASE + name, params={'offset': start, 'try': attempt},
                                  headers={'Range': f'bytes={start}-{end}'}, timeout=(15, 40)) as r:
                    r.raise_for_status()
                    if r.headers.get('Content-Range') != f'bytes {start}-{end}/{size}' or len(r.content) != end - start + 1:
                        raise ValueError('Incomplete range')
                    path.write_bytes(r.content)
                return
            except (OSError, requests.RequestException, ValueError):
                if attempt == 4:
                    raise
                time.sleep(1 + attempt)

    with ThreadPoolExecutor(max_workers=12) as pool:
        for i, _ in enumerate(pool.map(fetch, offsets), 1):
            if i % 32 == 0:
                print(name, i, '/', len(offsets), flush=True)
    tmp = target.with_suffix('.installing')
    digest = hashlib.sha256()
    with tmp.open('wb') as out:
        for start in offsets:
            data = (parts / str(start)).read_bytes()
            out.write(data)
            digest.update(data)
    if digest.hexdigest() != checksum:
        raise ValueError('Invalid SHA256: ' + name)
    tmp.replace(target)
    for start in offsets:
        (parts / str(start)).unlink()
    parts.rmdir()


if __name__ == '__main__':
    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in [pool.submit(download, name, *meta) for name, meta in FILES.items()]:
            result.result()
    print('Vision model and projector verified.', flush=True)

