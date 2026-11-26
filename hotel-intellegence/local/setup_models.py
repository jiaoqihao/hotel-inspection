import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT = Path(os.environ.get('HOTEL_VOICE_DATA', Path(os.environ['LOCALAPPDATA']) / 'HotelVoice'))
MODELS = ROOT / 'models'
MODELS.mkdir(parents=True, exist_ok=True)

def get(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'HotelVoice/1.0'}), timeout=60)

def download(url, path, expected=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (not expected or path.stat().st_size == expected):
        print('Ready: ' + path.name, flush=True)
        return path
    if expected and expected > 20 * 1024 * 1024:
        return parallel_download(url, path, expected)
    part = path.with_suffix(path.suffix + '.partial')
    for attempt in range(3):
        try:
            with get(url) as response, part.open('wb') as out:
                total = int(response.headers.get('Content-Length', 0))
                size = 0
                last = time.monotonic()
                while chunk := response.read(1024 * 1024 * 4):
                    out.write(chunk)
                    size += len(chunk)
                    if time.monotonic() - last > 20:
                        print(f'{path.name}: {size // 1048576} MB / {total // 1048576} MB', flush=True)
                        last = time.monotonic()
            if expected and size != expected:
                raise ValueError('Download size mismatch')
            if total and size != total:
                raise ValueError('Incomplete download')
            part.replace(path)
            print('Downloaded: ' + path.name, flush=True)
            return path
        except Exception as exc:
            print(f'Retry {attempt + 1}: {path.name}: {exc}', flush=True)
            if attempt == 2:
                raise

def parallel_download(url, path, size):
    chunk_size = 1024 * 1024
    directory = path.parent / (path.name + '.parts-1m')
    directory.mkdir(exist_ok=True)
    count = (size + chunk_size - 1) // chunk_size
    done = 0
    previous = path.with_suffix(path.suffix + '.partial')
    if previous.exists():
        with previous.open('rb') as source:
            for i in range(min(previous.stat().st_size // chunk_size, count)):
                target = directory / str(i)
                block = source.read(chunk_size)
                if not target.exists():
                    target.write_bytes(block)
    def fetch(index):
        start, end = index * chunk_size, min((index + 1) * chunk_size, size) - 1
        target = directory / str(index)
        if target.exists() and target.stat().st_size == end - start + 1:
            return
        for attempt in range(4):
            try:
                req = urllib.request.Request(url + ('&' if '?' in url else '?') + f'download=true&piece1m={index}&retry={attempt}&run=2', headers={'Range': f'bytes={start}-{end}', 'User-Agent': 'HotelVoice/1.0'})
                begin = time.monotonic()
                with urllib.request.urlopen(req, timeout=20) as response, target.open('wb') as out:
                    if response.status != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {start}-{end}/'):
                        raise ValueError('Server did not honor range')
                    while block := response.read1(256 * 1024):
                        out.write(block)
                        if time.monotonic() - begin > 50:
                            raise TimeoutError('Slow download part')
                if target.stat().st_size != end - start + 1:
                    raise ValueError('Incomplete chunk')
                return
            except Exception:
                if attempt == 3:
                    raise
    with concurrent.futures.ThreadPoolExecutor(max_workers=24) as pool:
        for future in concurrent.futures.as_completed([pool.submit(fetch, i) for i in range(count)]):
            future.result()
            done += 1
            if done % 64 == 0 or done == count:
                print(f'{path.name}: {done}/{count} download parts', flush=True)
    partial = path.with_suffix(path.suffix + '.partial')
    with partial.open('wb') as out:
        for i in range(count):
            with (directory / str(i)).open('rb') as source:
                while block := source.read(4 * 1024 * 1024):
                    out.write(block)
    partial.replace(path)
    for piece in directory.iterdir():
        piece.unlink()
    directory.rmdir()
    return path

def hf(repo, names, directory):
    meta = json.load(get('https://huggingface.co/api/models/' + repo + '?blobs=true'))
    revision = meta['sha']
    siblings = {s['rfilename']: s for s in meta['siblings']}
    for name in names:
        info = siblings[name]
        path = download(f'https://huggingface.co/{repo}/resolve/{revision}/{name}', directory / name, info.get('size'))
        sha = info.get('lfs', {}).get('sha256')
        if sha:
            with path.open('rb') as f:
                actual = hashlib.file_digest(f, 'sha256').hexdigest()
            if actual != sha:
                path.unlink()
                raise ValueError('Model checksum mismatch: ' + name)
    return {'repository': repo, 'revision': revision, 'files': names}

def runtime():
    tag = 'b12359'
    html = get('https://github.com/ggml-org/llama.cpp/releases/expanded_assets/' + tag).read().decode()
    links = re.findall(r'href="([^"]+win-vulkan-x64[^"]*\.zip)"', html)
    if not links:
        raise RuntimeError('No Windows Vulkan runtime in release ' + tag)
    url = 'https://github.com' + links[0]
    with urllib.request.urlopen(urllib.request.Request(url, method='HEAD'), timeout=30) as response:
        size = int(response.headers.get('Content-Length', 0))
    archive = download(url, ROOT / 'runtime.zip', size or None)
    with archive.open('rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != 'e8d18c6e4210cca87e7307c2ea78363ac8d158e4e1397358ec96ab70b53a0d5e':
            raise ValueError('Runtime checksum mismatch')
    dest = ROOT / 'llama'
    dest.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for entry in z.infolist():
            target = (dest / entry.filename).resolve()
            if not target.is_relative_to(dest.resolve()):
                raise ValueError('Unsafe archive entry')
        z.extractall(dest)
    return {'runtime': tag}

if __name__ == '__main__':
    jobs = [
        lambda: hf('csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17', ['model.int8.onnx', 'tokens.txt', 'test_wavs/zh.wav'], MODELS / 'sensevoice'),
        lambda: hf('Qwen/Qwen2.5-1.5B-Instruct-GGUF', ['qwen2.5-1.5b-instruct-q4_k_m.gguf'], MODELS / 'qwen'),
        runtime,
    ]
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = []
        for future in concurrent.futures.as_completed([pool.submit(fn) for fn in jobs]):
            try:
                results.append(future.result())
            except Exception as exc:
                print('SETUP ERROR: ' + str(exc), flush=True)
                raise
    (MODELS / 'manifest.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print('All local models ready.', flush=True)
    subprocess.run([sys.executable, str(Path(__file__).with_name('launch.py')), '--no-browser'], check=True)
