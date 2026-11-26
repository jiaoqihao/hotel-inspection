"""Download local speaker segmentation and Chinese voice embedding models."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request
from local.setup_models import ROOT, MODELS, download

DEST = MODELS / 'speakers'
DEST.mkdir(parents=True, exist_ok=True)
FILES = {
    'segmentation.tar.bz2': 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2',
    'embedding.onnx': 'https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx',
}

def fetch(name, url):
    with urllib.request.urlopen(urllib.request.Request(url,method='HEAD'),timeout=30) as response:
        size=int(response.headers.get('Content-Length',0))
    print(f'Downloading {name}: {size // 1048576} MB',flush=True)
    path=download(url,DEST/name,size or None)
    with path.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
    return {'file':name,'source':url,'sha256':digest,'bytes':path.stat().st_size}

if __name__=='__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda kv:fetch(*kv),FILES.items()))
    with tarfile.open(DEST/'segmentation.tar.bz2','r:bz2') as archive:
        member=next(m for m in archive.getmembers() if m.isfile() and m.name.endswith('/model.onnx'))
        with archive.extractfile(member) as source, (DEST/'segmentation.onnx.partial').open('wb') as out:
            shutil.copyfileobj(source,out)
        (DEST/'segmentation.onnx.partial').replace(DEST/'segmentation.onnx')
    (DEST/'manifest.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    print('Local diarization models ready.',flush=True)
