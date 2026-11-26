"""Local audio transcription and evidence-backed scene reports."""
import asyncio
import concurrent.futures
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import secrets
import sqlite3
import threading
import time
import uuid

import av
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import numpy as np
from pydantic import BaseModel, Field
import requests
import sherpa_onnx
from starlette.concurrency import run_in_threadpool
from local.badge_sync import BadgeSync, SyncError
from local import access, badge_archive
from local.badge_analysis import MODEL, REPORT_VERSION, SCENES, analyze_segments
from local.platform_db import migrate, database
from local.phone import PhoneWorker, router as phone_router
from local.speech import recognize_turns, speakers_ready

ROOT = Path(os.environ.get('HOTEL_VOICE_DATA', str(Path(os.environ['LOCALAPPDATA']) / 'HotelVoice')))
ROOT.mkdir(parents=True, exist_ok=True)
AUDIO = ROOT / 'recordings'
AUDIO.mkdir(exist_ok=True)
DB = ROOT / 'records.sqlite3'
MODELS = Path(os.environ.get('HOTEL_VOICE_MODELS', ROOT / 'models'))
FRONTEND = Path(__file__).resolve().parents[1] / 'dist-local'
TOKEN = secrets.token_urlsafe(32)
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=1)
ASR = None
from local.audio import decode, SAMPLE_RATE, MAX_SECONDS
MAX_BYTES = 150 * 1024 * 1024

@contextmanager
def connection():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()

with connection() as c:
    c.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
    badge_archive.migrate(c)
    for row in c.execute('SELECT * FROM records').fetchall():
        item = json.loads(row['data'])
        if item['status'] in ['queued', 'diarizing', 'transcribing', 'analyzing']:
            if item.get('autoAnalyze'):
                item.update(status='uploaded', error=None, progress=0)
            else:
                item.update(status='error', error='上次服务已中断。请重新分析。')
            c.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))

def all_records():
    with connection() as c:
        return [json.loads(r['data']) for r in c.execute('SELECT data FROM records ORDER BY rowid DESC')]

def record(id):
    with connection() as c:
        row = c.execute('SELECT data FROM records WHERE id=?', (id,)).fetchone()
    if not row:
        raise HTTPException(404, '录音不存在')
    return json.loads(row['data'])

def save(item):
    with connection() as c:
        c.execute('INSERT INTO records(id,data) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data', (item['id'], json.dumps(item, ensure_ascii=False)))

@contextmanager
def edit_record(id):
    """Serialize reads and edits so concurrent reviews cannot overwrite each other."""
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM records WHERE id=?',(id,)).fetchone()
        if not row:
            raise HTTPException(404,'录音不存在')
        item = json.loads(row[0])
        yield db,item
        db.execute('UPDATE records SET data=? WHERE id=?',(json.dumps(item,ensure_ascii=False),id))

def update(id, **values):
    with edit_record(id) as (_, item):
        item.update(values)

def model_status():
    sense = (MODELS / 'sensevoice/model.int8.onnx').exists() and (MODELS / 'sensevoice/tokens.txt').exists()
    return {'asr': sense, 'diarization': speakers_ready(MODELS), 'analysis': True,
            'semantic': bool(os.environ.get('DEEPSEEK_API_KEY', '').strip()), 'provider': 'deepseek',
            'storage': str(ROOT), 'asrModel': 'SenseVoice · 连续发言转写',
            'speakerModel': 'Pyannote + 3D-Speaker · 本地声纹聚类',
            'analysisModel': MODEL, 'badgeReportVersion': REPORT_VERSION, 'maxMinutes': 30, 'maxMB': 150}


from local.patrol import Patrol, router as patrol_router
from local.vision_api import repair_gesture_times, submit_capture, submit_event


@asynccontextmanager
async def lifespan(app):
    scheduler = Patrol(ROOT, submit_capture, submit_event=submit_event)
    repair_gesture_times(scheduler)
    app.state.patrol = scheduler
    migrate()
    phones = PhoneWorker()
    app.state.phones=phones
    phones.start()
    scheduler.start()
    badge_sync.stop.clear()
    sync_thread = threading.Thread(target=badge_sync.run, name='badge-sync', daemon=True)
    sync_thread.start()
    alerts=asyncio.create_task(duty_clock(app))
    try:
        yield
    finally:
        alerts.cancel()
        await asyncio.gather(alerts,return_exceptions=True)
        badge_sync.stop.set()
        badge_sync.wake.set()
        await asyncio.to_thread(sync_thread.join)
        scheduler.stop()
        phones.stop()


app = FastAPI(title='栖鉴 · 酒店智能巡检与服务分析', lifespan=lifespan)
app.include_router(patrol_router)
app.include_router(access.router)
app.include_router(badge_archive.router(connection,edit_record))
from local.phone_insights import router as phone_insights_router
from local.phone_reports import router as phone_reports_router
from local.phone_desk import router as desk_router, duty_clock
app.include_router(desk_router)
app.include_router(phone_insights_router)
app.include_router(phone_reports_router)
app.include_router(phone_router)

@app.get('/api/ping')
def ping():
    return {'service': 'hotel-voice', 'ok': True}

@app.middleware('http')
async def local_boundary(request: Request, call_next):
    # Reject cross-site writes and DNS-rebinding. API operations require a per-process token.
    path = request.url.path
    writing = request.method not in ('GET','HEAD','OPTIONS')
    origin = request.headers.get('origin')
    if access.STORE:
        if request.headers.get('host') != access.ORIGIN.split('://',1)[1]:
            return JSONResponse({'detail':'门店地址不匹配'},status_code=403)
        if writing and origin != access.ORIGIN:
            return JSONResponse({'detail':'请求来源无效'},status_code=403)
        if path.startswith('/api/') and path not in ('/api/session','/api/session/login','/api/ping'):
            user = access.identity(request)
            if not user:
                return JSONResponse({'detail':'请登录门店账号'},status_code=401)
            if not access.allowed(user,path,request.method):
                return JSONResponse({'detail':'当前岗位无权操作'},status_code=403)
    else:
        if request.url.hostname not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'detail':'仅允许本机访问'},status_code=403)
        if writing and path.startswith('/api/'):
            if origin and origin not in ('http://127.0.0.1:8765','http://localhost:8765'):
                return JSONResponse({'detail':'拒绝跨站请求'},status_code=403)
            if not secrets.compare_digest(request.headers.get('x-local-token',''), TOKEN):
                return JSONResponse({'detail':'页面已过期，请刷新'},status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    if request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store'
    return response

@app.get('/api/status')
def status():
    return {**model_status(), 'token': 'session' if access.STORE else TOKEN}

@app.get('/api/overview')
def overview(request:Request):
    from local.vision_api import report_index
    user=access.identity(request)
    stats={}
    if user['role'] in access.QUALITY:
        with report_index() as db:
            stats['camera']=db.execute("SELECT count(*) FROM jobs WHERE review='待整改'").fetchone()[0]
        stats['badge']=sum(r['status']=='done' and not r.get('reviewed') for r in all_records())
    with database() as db:
        if user['role'] in access.PHONE:
            stats['duty']=db.execute("SELECT count(*) FROM phone_alert_tasks t JOIN calls c ON c.id=t.call_id JOIN phone_alerts a ON a.call_id=c.id WHERE c.test=0 AND t.state='待接手' AND a.level IN ('S','A') AND a.state<>'已排除'").fetchone()[0]
            stats['phone']=db.execute("SELECT count(*) FROM items i JOIN calls c ON c.id=i.call_id WHERE i.state='待确认' AND c.test=0").fetchone()[0]
        if user['role'] in access.PHONE or user['role']=='部门员工':
            clause,args=('',()) if user['role'] in access.PHONE else (' AND o.department=?',(user['department'],))
            stats['overdue']=db.execute("SELECT count(*) FROM orders o JOIN items i ON i.id=o.item JOIN calls c ON c.id=i.call_id WHERE o.state!='已关闭' AND o.due<? AND c.test=0"+clause,(time.time(),*args)).fetchone()[0]
    return stats

@app.get('/api/reports')
def reports(request:Request):
    from local.vision_api import report_index
    role=access.identity(request)['role']
    report={}
    if role in access.QUALITY:
        with report_index() as db:
            report['摄像头巡检']={k:v for k,v in db.execute("SELECT review,count(*) FROM jobs GROUP BY review")}
        with connection() as db:
            report['胸牌语音']={k:v for k,v in db.execute(
                "SELECT CASE json_extract(data,'$.source') WHEN '胸牌' THEN '胸牌归档' WHEN '挂牌' THEN '挂牌归档' ELSE '历史导入' END,count(*) FROM records GROUP BY 1")}
    if role in access.PHONE:
        with database() as db:
            report['前台电话']={k:v for k,v in db.execute('SELECT direction,count(*) FROM calls WHERE test=0 GROUP BY direction')}
            report['电话工单']={k:v for k,v in db.execute('SELECT o.state,count(*) FROM orders o JOIN items i ON i.id=o.item JOIN calls c ON c.id=i.call_id WHERE c.test=0 GROUP BY o.state')}
    return report

@app.get('/api/records')
def list_records():
    return all_records()

@app.get('/api/records/{id}')
def get_record(id: str):
    return record(id)

@app.get('/api/records/{id}/export')
def export_record(id:str):
    item = record(id)
    if (item.get('report') or {}).get('reportVersion', 1) >= 3:
        return {k:item[k] for k in ('id','filename','source','duration','test','reviewed','report') if k in item}
    return item

@app.post('/api/upload')
async def upload(file: UploadFile = File(...), employee: str = Form(''), scene: str = Form('自动识别'), note: str = Form(''), language: str = Form('zh'), diarization: bool | None = Form(None), speakerCount: int = Form(2), badge: str = Form(''), recordedAt: str = Form(''), source: str = Form('历史导入'), test: bool = Form(False)):
    item = await run_in_threadpool(store_recording, iter(lambda: file.file.read(1024 * 1024), b''),
                                   file.filename or '', employee, scene, note, language, diarization,
                                   speakerCount, badge, recordedAt, source, test)
    await run_in_threadpool(queue_badge_analysis)
    return record(item['id'])


def store_recording(chunks, filename, employee, scene, note, language, diarization, speakerCount,
                    badge, recordedAt, source, test, remote_key=None):
    extension = Path(filename).suffix.lower()
    if extension not in ('.wav', '.mp3', '.m4a', '.aac', '.ogg', '.flac', '.mp4', '.webm'):
        raise HTTPException(400, '请选择 WAV、MP3、M4A、AAC、OGG、FLAC、MP4 或 WEBM 音频')
    if scene == '其他':  # Older upload forms used this as their unassigned hint.
        scene = '自动识别'
    if scene != '自动识别' and scene not in SCENES:
        raise HTTPException(400, '场景无效')
    if language not in ('zh','auto') or not 0 <= speakerCount <= 8:
        raise HTTPException(400, '转写语言或说话人数无效')
    if diarization is None:
        diarization = speakers_ready(MODELS)
    if recordedAt:
        try:
            datetime.fromisoformat(recordedAt)
        except ValueError:
            raise HTTPException(400,'录制时间无效')
    id = uuid.uuid4().hex
    path = AUDIO / (id + extension)
    size = 0
    try:
        with path.open('wb') as out:
            for chunk in chunks:
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, '单个文件不能超过 150 MB')
                out.write(chunk)
        if not size:
            raise HTTPException(400, '录音文件为空')
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise HTTPException(400, '文件中没有音轨')
            duration = float(container.duration / av.time_base) if container.duration else None
            if duration and duration > MAX_SECONDS:
                raise HTTPException(400, '第一版支持单段最多 30 分钟，请分段上传')
    except (HTTPException, SyncError, requests.RequestException):
        path.unlink(missing_ok=True)
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise HTTPException(400, '无法读取音频，请检查文件是否损坏')
    item = {'id': id, 'filename': Path(filename).name, 'extension': extension, 'size': size, 'duration': duration, 'employee': employee.strip()[:80] or '未指定员工', 'sceneHint': scene, 'note': note[:1500], 'createdAt': datetime.now(timezone.utc).isoformat(), 'status': 'uploaded', 'progress': 0, 'segments': [], 'report': None, 'reviewed': False, 'language':language, 'diarization':diarization,'speakerCount':speakerCount,'asrRevision':3}
    item.update(source=source if source in ('胸牌','挂牌') else '历史导入', test=bool(test), autoAnalyze=True, badge=badge.strip()[:80], recordedAt=recordedAt[:40])
    try:
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if remote_key:
                device = db.execute('''SELECT d.id,d.test FROM badge_devices d JOIN badge_sync_config c ON d.app=c.app
                    WHERE d.app=? AND d.number=? AND d.enabled AND NOT d.retired AND c.enabled''',remote_key[:2]).fetchone()
                if not device:
                    raise SyncError('设备同步已暂停')
                item.update(deviceId=device['id'])
                accepted = db.execute('INSERT OR IGNORE INTO badge_sync_files VALUES(?,?,?,?)', (*remote_key, id))
                if not accepted.rowcount:
                    path.unlink(missing_ok=True)
                    return None
                item['importedFrom'] = '悦航益'
            elif item['badge']:
                device = db.execute('''SELECT d.id FROM badge_devices d JOIN badge_sync_config c ON d.app=c.app
                    WHERE d.number=? AND d.source=?''',(item['badge'],item['source'])).fetchone()
                if device:
                    item['deviceId'] = device['id']
            db.execute('INSERT INTO records VALUES(?,?)', (id, json.dumps(item, ensure_ascii=False)))
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return item


def ingest_badge(chunks, filename, device, metadata, config):
    key = (config['app'], device['number'], filename)
    return store_recording(chunks, filename, '', '自动识别', '', 'zh', None, 2, device['number'],
                           metadata.get('startTime') or '', device['source'], bool(config['test']), key)


def queue_badge_analysis():
    health = model_status()
    with connection() as db:
        pending = db.execute("""SELECT id FROM records WHERE data->>'$.autoAnalyze'=1 AND data->>'$.status'='uploaded'
                                AND (json_array_length(data,'$.segments')>0 OR (? AND (NOT coalesce(data->>'$.diarization',0) OR ?)))
                                ORDER BY rowid LIMIT 5""", (health['asr'], health.get('diarization', False))).fetchall()
    for row in pending:
        try:
            analyze(row[0])
        except HTTPException as exc:
            if exc.status_code == 429:
                break
            if exc.status_code not in (409, 503):
                raise


badge_sync = BadgeSync(connection, ingest_badge, queue_badge_analysis)
app.include_router(badge_sync.router())
app.include_router(badge_sync.directory.router())


class Review(BaseModel):
    reviewed: bool
    note: str | None = Field(default=None,max_length=1500)

@app.patch('/api/records/{id}/review')
def review(id: str, body: Review):
    with edit_record(id) as (_,item):
        if item['status'] != 'done':
            raise HTTPException(409, '分析完成后才可复核')
        if body.note is not None:
            item['reviewNote']=body.note
        item.update(reviewed=body.reviewed, reviewedAt=datetime.now(timezone.utc).isoformat() if body.reviewed else None)
    return item

class TaskUpdate(BaseModel):
    done: bool

@app.patch('/api/records/{id}/tasks/{index}')
def task_update(id: str, index: int, body: TaskUpdate):
    with edit_record(id) as (_,item):
        tasks = (item.get('report') or {}).get('todos', [])
        if not 0 <= index < len(tasks):
            raise HTTPException(404, '待办不存在')
        tasks[index]['done'] = body.done
    return item

class SpeakerLabel(BaseModel):
    name: str = Field(min_length=1,max_length=40)

@app.patch('/api/records/{id}/speakers/{speaker_id}')
def speaker_label(id: str, speaker_id: str, body: SpeakerLabel):
    with edit_record(id) as (_,item):
        if item['status'] not in ('done','error'):
            raise HTTPException(409,'请等待本次分析结束后再命名说话人')
        if speaker_id not in item.get('speakers',{}):
            raise HTTPException(404,'说话人分组不存在')
        name=body.name.strip()
        if not name:
            raise HTTPException(400,'名称不能为空')
        item['speakers'][speaker_id].update(name=name,source='manual')
        for segment in item['segments']:
            if segment.get('speakerId')==speaker_id:
                segment['speaker']=name
        item['reviewed']=False
        item.pop('reviewedAt',None)
    return item

class RetrySpeech(BaseModel):
    language: str = 'zh'
    diarization: bool = True
    speakerCount: int = Field(default=2,ge=0,le=8)

@app.post('/api/records/{id}/retranscribe')
def retranscribe(id: str, body: RetrySpeech):
    item=record(id)
    if item['status'] in ('queued','diarizing','transcribing','analyzing'):
        raise HTTPException(409,'本次分析尚未完成')
    if body.language not in ('zh','auto'):
        raise HTTPException(400,'语言选项无效')
    if body.diarization and not speakers_ready(MODELS):
        raise HTTPException(503,'本地声纹模型尚未就绪')
    if sum(r['status'] in ('queued','diarizing','transcribing','analyzing') for r in all_records())>=5:
        raise HTTPException(429,'队列已满，请稍后重试')
    child={k:v for k,v in item.items() if k in ('extension','size','duration','employee','sceneHint','note','source','badge','recordedAt','test','deviceId')}
    child.update(id=uuid.uuid4().hex,filename=Path(item['filename']).stem+('-双人对话复核' if body.speakerCount==2 else '-对话复核')+item['extension'],sourceId=id,createdAt=datetime.now(timezone.utc).isoformat(),status='queued',progress=0,segments=[],report=None,reviewed=False,language=body.language,diarization=body.diarization,speakerCount=body.speakerCount,asrRevision=3)
    shutil.copyfile(AUDIO/(id+item['extension']),AUDIO/(child['id']+child['extension']))
    save(child)
    POOL.submit(process,child['id'])
    return child

class RecordingMetadata(BaseModel):
    filename: str = Field(min_length=1, max_length=240)
    employee: str = Field(max_length=80)
    note: str = Field(max_length=1500)


@app.patch('/api/records/{id}/metadata')
def update_metadata(id: str, body: RecordingMetadata):
    name = body.filename.strip()
    if not name or Path(name).name != name or any(ord(c) < 32 or c in '/\\' for c in name):
        raise HTTPException(400, '请输入有效的录音名称')
    with edit_record(id) as (_, item):
        if item['status'] in ('queued', 'diarizing', 'transcribing', 'analyzing'):
            raise HTTPException(409, '请等待分析结束后编辑')
        item.update(filename=name, employee=body.employee.strip() or '未指定员工', note=body.note.strip())
    return item


@app.delete('/api/records/{id}')
def remove(id: str):
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT data FROM records WHERE id=?', (id,)).fetchone()
        if not row:
            raise HTTPException(404, '录音不存在')
        item = json.loads(row[0])
        if item['status'] in ('queued', 'diarizing', 'transcribing', 'analyzing'):
            raise HTTPException(409, '请等待分析结束后删除')
        db.execute('DELETE FROM records WHERE id=?', (id,))
    # Keep the vendor file receipt so the next poll cannot reimport a deleted recording.
    for path in (AUDIO / (id + item['extension']), AUDIO / (id + '.play.wav')):
        path.unlink(missing_ok=True)
    return {'ok': True}

@app.get('/api/records/{id}/audio')
def audio(id: str):
    item = record(id)
    preview = AUDIO / (id + '.play.wav')
    path = preview if preview.exists() else AUDIO / (id + item['extension'])
    return FileResponse(path)

def transcribe(id):
    global ASR
    item = record(id)
    values = decode(AUDIO / (id + item['extension']))
    duration = len(values) / SAMPLE_RATE
    # Browser-compatible playback; raw audio is preserved separately.
    import wave
    with wave.open(str(AUDIO / (id + '.play.wav')), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes((np.clip(values, -1, 1) * 32767).astype('<i2').tobytes())
    rms = float(np.sqrt(np.mean(values ** 2)))
    clipped = float(np.mean(np.abs(values) >= .995))
    warnings = ['声纹分组不等于员工身份确认；同一分组仍可能混入其他人，标签仅在本次录音内有效。', '单声道重叠讲话尚未做声音分离；检测结果也不能排除未发现的重叠。', '未计算客户可听清率或背景噪声信噪比；请回听确认轻声与口音内容。']
    if rms < .006:
        warnings.append('整体音量较低，可能存在漏字。')
    if clipped > .002:
        warnings.append('检测到较多接近满幅的采样点，可能存在失真。')
    quality = {'rmsDb': round(20 * math.log10(max(rms, 1e-8)), 1), 'clippedPercent': round(clipped * 100, 3), 'warnings': warnings}
    update(id, duration=duration, quality=quality)
    def progress(stage,fraction):
        update(id,status=stage,progress=round(10+20*fraction if stage=='diarizing' else 30+30*fraction))
    result=recognize_turns(values,MODELS,language=item.get('language','zh'),expected_speakers=item.get('speakerCount',2),use_speakers=item.get('diarization',False),progress=progress)
    segments=result['segments']
    quality['warnings'].extend(result['warnings'])
    quality['detectedOverlapSeconds']=result['overlapSeconds']
    update(id,quality=quality,segments=segments,speakers=result['speakers'],speakerTurns=result['speakerTurns'],asrRevision=3)
    if not segments:
        raise ValueError('未识别到可用语音。请检查是否静音或严重噪声。')
    return segments

def process(id):
    try:
        item = record(id)
        segments = item.get('segments')
        if not segments:
            update(id, status='transcribing', progress=5)
            segments = transcribe(id)
        update(id, status='analyzing', progress=65)
        report = analyze_segments(segments, item, lambda value: update(id, progress=value),
                                  lambda scenes: update(id, detectedScenes=scenes, progress=72))
        update(id, status='done', progress=100, report=report)
    except Exception as exc:
        # Never expose transcript contents in errors or logs.
        update(id, status='error', error=str(exc)[:300])

def ensure_queue_space(db):
    active = db.execute("""SELECT count(*) FROM records WHERE json_extract(data,'$.status')
                        IN ('queued','diarizing','transcribing','analyzing')""").fetchone()[0]
    if active >= 5:
        raise HTTPException(429, '队列已满，请等待已有任务完成')


@app.post('/api/records/{id}/deepseek-copy')
def deepseek_copy(id: str):
    child_id = uuid.uuid4().hex
    files = []
    try:
        with edit_record(id) as (db, original):
            if original['status'] != 'done' or not original.get('segments') or (original.get('report') or {}).get('reportVersion', 1) >= REPORT_VERSION:
                raise HTTPException(409, '请选择已有转写的历史报告')
            prior = db.execute("""SELECT data FROM records WHERE json_extract(data,'$.deepseekSource')=? AND data->>'$.deepseekVersion'=?""", (id, REPORT_VERSION)).fetchone()
            if prior:
                return json.loads(prior[0])
            ensure_queue_space(db)
            child = {k: original[k] for k in ('extension','size','duration','employee','sceneHint','note',
                     'source','badge','recordedAt','test','deviceId','language','diarization','speakerCount',
                     'asrRevision','segments','speakers','speakerTurns','quality') if k in original}
            child.update(id=child_id, sourceId=id, deepseekSource=id, deepseekVersion=REPORT_VERSION, filename=Path(original['filename']).stem+' · DeepSeek 分析',
                         createdAt=datetime.now(timezone.utc).isoformat(), status='queued', progress=0, report=None, reviewed=False)
            for suffix in (original['extension'], '.play.wav'):
                source = AUDIO / (id + suffix)
                if suffix == '.play.wav' and not source.exists():
                    continue
                target = AUDIO / (child_id + suffix)
                files.append(target)
                shutil.copyfile(source, target)
            db.execute('INSERT INTO records VALUES(?,?)', (child_id, json.dumps(child, ensure_ascii=False)))
    except Exception:
        for target in files:
            target.unlink(missing_ok=True)
        raise
    POOL.submit(process, child_id)
    return child


def empty_report_can_retry(item):
    report = item.get('report') or {}
    return (item['status'] == 'done' and item.get('segments') and report.get('mode') == 'noEvidence'
            and not report.get('scene') and not item.get('reviewed') and not item.get('reviewNote')
            and not any(report.get(group) for group in badge_archive.GROUPS)
            and report.get('reportVersion', 1) < REPORT_VERSION)


@app.post('/api/records/{id}/analyze')
def analyze(id: str):
    item = record(id)
    if item['status'] in ('queued', 'diarizing', 'transcribing', 'analyzing'):
        raise HTTPException(409, '这条录音已在分析队列中')
    if item['status'] == 'done' and not empty_report_can_retry(item):
        raise HTTPException(409, '已有分析报告，请保留复核与待办结果')
    health = model_status()
    if not item.get('segments') and not health['asr']:
        raise HTTPException(503, '本地模型尚未就绪，请等待模型下载或启动完成')
    if not item.get('segments') and item.get('diarization') and not health['diarization']:
        raise HTTPException(503,'本地声纹模型尚未就绪')
    with edit_record(id) as (db, latest):
        ensure_queue_space(db)
        if latest['status'] not in ('error', 'uploaded') and not empty_report_can_retry(latest):
            raise HTTPException(409, '录音状态已变更，请刷新重试')
        latest.update(status='queued', progress=0, error=None, detectedScenes=[], report=None)
    POOL.submit(process, id)
    return record(id)

from local.vision_api import router as vision_router
app.include_router(vision_router)

if FRONTEND.exists():
    app.mount('/', StaticFiles(directory=FRONTEND, html=True), name='frontend')
