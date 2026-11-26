"""Decode uploaded media for the badge and phone transcribers."""

import av
import numpy as np

SAMPLE_RATE = 16000
MAX_SECONDS = 1800


def _frames(path):
    with av.open(str(path)) as container:
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(audio=0):
            for output in resampler.resample(frame):
                yield output.to_ndarray().ravel()
        for output in resampler.resample(None):
            yield output.to_ndarray().ravel()


def decode_blocks(path, seconds=MAX_SECONDS):
    """Yield bounded float32 blocks without splitting or duplicating a sample."""
    limit = SAMPLE_RATE * seconds
    frames = []
    size = 0
    seen = False
    for values in _frames(path):
        while len(values):
            take = min(limit - size, len(values))
            frames.append(values[:take])
            size += take
            values = values[take:]
            if size == limit:
                yield np.concatenate(frames)
                seen = True
                frames, size = [], 0
    if size:
        yield np.concatenate(frames)
    elif not seen:
        raise ValueError("音频没有可解码的内容")


def decode(path):
    blocks = decode_blocks(path, seconds=MAX_SECONDS + 1)
    first = next(blocks)
    if len(first) > MAX_SECONDS * SAMPLE_RATE:
        raise ValueError("录音超过 30 分钟，请分段上传")
    return first
