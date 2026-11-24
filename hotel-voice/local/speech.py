"""Local diarization and turn-level ASR. No persistent voice identity registry."""
from functools import lru_cache
import re
from pathlib import Path
import numpy as np
import sherpa_onnx

RATE = 16000

def speakers_ready(models):
    root = Path(models) / 'speakers'
    return (root/'segmentation.onnx').is_file() and (root/'embedding.onnx').is_file()

@lru_cache(maxsize=1)
def recognizer(models, language):
    return sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(Path(models)/'sensevoice/model.int8.onnx'),
        tokens=str(Path(models)/'sensevoice/tokens.txt'),
        num_threads=4, language=language, use_itn=True)

def diarize(values, models, expected_speakers=2, progress=None, threshold=.65):
    root=Path(models)/'speakers'
    config=sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(root/'segmentation.onnx'),window_shift_ratio=.1),
            num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(root/'embedding.onnx'),num_threads=4),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=expected_speakers if expected_speakers else -1,threshold=threshold),
        min_duration_on=.2,min_duration_off=.3)
    if not config.validate():
        raise ValueError('本地声纹模型配置无效，请检查模型文件')
    engine=sherpa_onnx.OfflineSpeakerDiarization(config)
    def callback(current,total):
        if progress:
            progress(current/max(total,1))
        return 0
    result=engine.process(values,callback=callback).sort_by_start_time()
    # Stable labels follow first appearance, not arbitrary clustering IDs.
    mapping={}
    turns=[]
    for turn in result:
        if turn.speaker not in mapping:
            mapping[turn.speaker]=f'spk_{len(mapping)}'
        turns.append({'start':float(turn.start),'end':min(float(turn.end),len(values)/RATE),'speakerId':mapping[turn.speaker]})
    return turns

def intervals(turns, duration):
    """Each instant is transcribed once; overlaps keep all candidate speakers."""
    events={}
    for turn in turns:
        start=max(0.,min(duration,turn['start']))
        end=max(0.,min(duration,turn['end']))
        if end<=start:
            continue
        events.setdefault(start,[]).append((turn['speakerId'],1))
        events.setdefault(end,[]).append((turn['speakerId'],-1))
    counts={}
    result=[]
    last=None
    for point in sorted(events):
        active=tuple(sorted(k for k,v in counts.items() if v>0))
        if last is not None and point>last and active:
            if result and result[-1]['speakerIds']==list(active) and abs(result[-1]['end']-last)<1e-6:
                result[-1]['end']=point
            else:
                result.append({'start':last,'end':point,'speakerIds':list(active),'overlap':len(active)>1})
        for sid,delta in events[point]:
            counts[sid]=counts.get(sid,0)+delta
        last=point
    return result

def conversation_spans(spans, max_pause=1.5):
    """Keep a speaker's continuous utterance intact across brief pauses.

    Never bridge another speaker or an overlapping interval. Silence between
    these spans is decoded with the speech, preserving the acoustic context.
    """
    result=[]
    for span in spans:
        if (result and result[-1]['speakerIds']==span['speakerIds']
                and result[-1]['overlap']==span['overlap']
                and 0 <= span['start']-result[-1]['end'] <= max_pause):
            result[-1]['end']=span['end']
        else:
            result.append({**span,'speakerIds':list(span['speakerIds'])})
    return result


def merge_utterances(segments, max_pause=1.5):
    """Hide internal model windows, retaining evidence IDs for whole utterances."""
    result=[]
    for segment in segments:
        prev=result[-1] if result else None
        if (prev and prev.get('speakerIds')==segment.get('speakerIds')
                and prev.get('overlap')==segment.get('overlap')
                and 0 <= segment['start']-prev['end'] <= max_pause
                and not prev['text'].startswith('[片段过短')
                and not segment['text'].startswith('[片段过短')):
            # Do not invent punctuation or rewrite what the acoustic model heard.
            separator=' ' if re.search(r'[A-Za-z0-9]$',prev['text']) and re.match(r'[A-Za-z0-9]',segment['text']) else ''
            prev['text']+=separator+segment['text']
            prev['end']=segment['end']
            prev['needsReview']=prev.get('needsReview',False) or segment.get('needsReview',False)
        else:
            result.append({**segment,'id':len(result)})
    return result


def split_turn(values, start, end, max_seconds=30):
    a,b=max(0,int(start*RATE)),min(len(values),int(end*RATE))
    limit=int(max_seconds*RATE)
    while a<b:
        finish=min(b,a+limit)
        if finish<b:
            lookback=min(2*RATE,finish-a-1)
            region=values[finish-lookback:finish]
            n=len(region)//400
            if n:
                energies=np.mean(region[:n*400].reshape(n,400)**2,axis=1)
                finish=finish-lookback+(int(np.argmin(energies))+1)*400
        yield a,finish
        a=finish

def label_for(sid):
    n=int(sid.split('_')[-1])
    return '说话人 '+(chr(65+n) if n<26 else str(n+1))

def recognize_turns(values, models, language='zh', expected_speakers=2, use_speakers=True, progress=None):
    duration=len(values)/RATE
    warnings=[]
    turns=[]
    if use_speakers:
        if not speakers_ready(models):
            raise ValueError('说话人模型未就绪，请等待模型安装完成')
        turns=diarize(values,models,expected_speakers,lambda p:progress('diarizing',p) if progress else None)
        spans=intervals(turns,duration)
        if not spans:
            warnings.append('说话人检测没有返回可靠片段，已改为短片段转写；不能判断是否无人说话。')
            spans=[{'start':0,'end':duration,'speakerIds':[],'overlap':False}]
    else:
        spans=[{'start':0,'end':duration,'speakerIds':[],'overlap':False}]
    speakers={sid:{'name':label_for(sid),'source':'automatic'} for t in turns for sid in [t['speakerId']]}
    if len(speakers)>8:
        warnings.append('自动分组超过 8 人，噪声可能造成同一人被拆分；可指定已知人数重跑。')
    overlap_seconds=round(sum(s['end']-s['start'] for s in spans if s['overlap']),2)
    spans=conversation_spans(spans)
    chunks=[(a,b,span) for span in spans for a,b in split_turn(values,span['start'],span['end']) if b>a]
    asr=recognizer(str(models),language)
    segments=[]
    for index,(a,b,span) in enumerate(chunks):
        sample=values[a:b]
        # Retain short and low-volume speech; no loudness-based speaker attribution.
        if len(sample)<int(.15*RATE):
            text='[片段过短，需回听]'
        elif float(np.max(np.abs(sample)))<1e-6:
            continue
        else:
            stream=asr.create_stream()
            stream.accept_waveform(RATE,sample)
            asr.decode_stream(stream)
            text=re.sub(r'<\|[^|]+\|>','',stream.result.text).strip()
        sid=span['speakerIds'][0] if len(span['speakerIds'])==1 else None
        label=speakers[sid]['name'] if sid else ('多人重叠（归属待确认）' if span['overlap'] else '说话人未确认')
        if text:
            segments.append({'id':len(segments),'start':round(a/RATE,3),'end':round(b/RATE,3),'text':text,'speaker':label,'speakerId':sid,'speakerIds':span['speakerIds'],'overlap':span['overlap'],'needsReview':span['overlap'] or len(sample)<RATE*.7,'attribution':'automatic' if sid else 'unknown'})
        if progress:
            progress('transcribing',(index+1)/max(len(chunks),1))
    return {'segments':merge_utterances(segments),'speakers':speakers,'speakerTurns':turns,'overlapSeconds':overlap_seconds,'warnings':warnings}
