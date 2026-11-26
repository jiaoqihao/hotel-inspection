"""Inspect the original telephone channels without assigning identities to mixed audio."""

import av
import numpy as np
from local.audio import SAMPLE_RATE


def stereo_blocks(path):
    with av.open(str(path)) as src:
        if src.streams.audio[0].codec_context.channels != 2:
            raise ValueError("清单标为双声道，原录音声道数不符")
        conv = av.AudioResampler(format="fltp", layout="stereo", rate=SAMPLE_RATE)
        frames = []
        count = 0
        for frame in src.decode(audio=0):
            for out in conv.resample(frame):
                values = out.to_ndarray()
                frames.append(values)
                count += values.shape[1]
                if count >= SAMPLE_RATE * 300:
                    yield np.concatenate(frames, axis=1)
                    frames = []
                    count = 0
        frames.extend(out.to_ndarray() for out in conv.resample(None))
        if frames:
            yield np.concatenate(frames, axis=1)


def inspect_levels(values):
    # 20 ms RMS windows provide signal warnings, not a speaker identity.
    windows = values[: len(values) // 320 * 320]
    if not len(windows):
        return False, False
    rms = np.sqrt(np.mean(windows.reshape(-1, 320) ** 2, axis=1))
    active = np.percentile(rms, 95)
    return bool(np.max(rms) > 0.004), bool(
        active > 0.015 and np.percentile(rms, 10) > 0.45 * active
    )


def quality(path):
    with av.open(str(path)) as src:
        stereo = src.streams.audio[0].codec_context.channels == 2
    if not stereo:
        return []
    active = [False, False]
    for block in stereo_blocks(path):
        for i, channel in enumerate(block):
            active[i] |= inspect_levels(channel)[0]
    if not any(active):
        return ["双声道均接近静音，请核对录音来源"]
    if not all(active):
        return ["一个声道接近静音，请确认双方是否都能听清"]
    return []


def transcribe_channels(path, mapping, recognize, models):
    segments = []
    offset = 0
    for block in stereo_blocks(path):
        for i, channel in enumerate(block):
            if not inspect_levels(channel)[0]:
                continue
            for s in recognize(channel, models, "zh", 2, False)["segments"]:
                segments.append(
                    {
                        **s,
                        "start": s["start"] + offset,
                        "end": s["end"] + offset,
                        "speaker": mapping[i],
                        "speakerIds": [str(i)],
                        "channel": i,
                    }
                )
        offset += block.shape[1] / SAMPLE_RATE
    segments.sort(key=lambda s: (s["start"], s["channel"]))
    ends = [-1.0, -1.0]
    overlap = False
    for i, s in enumerate(segments):
        s["id"] = i
        ch = s["channel"]
        if ends[1 - ch] > s["start"] + 0.15:
            overlap = True
        ends[ch] = max(ends[ch], s["end"])
    return segments, overlap
