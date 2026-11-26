from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from html import escape
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
import requests

from local.setup_vision import FILES, MODEL, PROJECTOR
from local.frontdesk_pose import CHECK, NO_EVENT, TURN_NO_EVENT, SCENE, pose_ready, scan
from local.frontdesk_review import archived_lower_views, check_person, check_lower_body
from local.vision_model import IMAGE, VIDEO, PROFILES, URL, encoded_image, inspect_group, media_groups

router = APIRouter(prefix='/api/vision')
ROOT = Path(os.environ.get('HOTEL_VOICE_DATA', str(Path(os.environ['LOCALAPPDATA']) / 'HotelVoice')))
STORE = ROOT / 'inspections'
WEIGHTS = ROOT / 'models/vision'
STORE.mkdir(parents=True, exist_ok=True)
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.RLock()
SLOTS = threading.BoundedSemaphore(4)
MODEL_NAME = 'Qwen2.5-VL-3B-Instruct · Q4_K_M'
INDEX = ROOT / 'inspection-index.sqlite3'
SUMMARY = ('id', 'filename', 'created', 'scene', 'state', 'review')


@contextmanager
def report_index():
    db = sqlite3.connect(INDEX, timeout=10)
    try:
        with db:
            yield db
    finally:
        db.close()


def index_report(report):
    with report_index() as db:
        db.execute('INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?)', tuple(report[k] for k in SUMMARY))


def find(job_id):
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise HTTPException(404, '找不到巡检记录')
    folder = STORE / job_id
    if not (folder / 'report.json').is_file():
        raise HTTPException(404, '找不到巡检记录')
    return folder


def save(folder, report):
    with LOCK:
        tmp = folder / 'report.tmp'
        tmp.write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')
        tmp.replace(folder / 'report.json')
        index_report(report)


def read(folder):
    with LOCK:
        return json.loads((folder / 'report.json').read_text(encoding='utf-8'))


with report_index() as db:
    db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, filename, created, scene, state, review)')
    db.execute('CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC,id)')
    indexed = {r[0] for r in db.execute("SELECT id FROM jobs WHERE state NOT IN ('分析中','排队中','复测中')")}
for existing in STORE.glob('*/report.json'):
    if existing.parent.name not in indexed:
        old = read(existing.parent)
        if old['state'] in ('排队中', '分析中'):
            old.update(state='失败', error='上次服务已中断，请重新分析。')
        elif old['state'] == '复测中':
            old.update(state='已完成', retestError='上次复测中断，已保留上次结果。')
        save(existing.parent, old)


def installed():
    return all((WEIGHTS / name).is_file() and (WEIGHTS / name).stat().st_size == size
               for name, (size, _) in FILES.items())


def ready():
    try:
        return requests.get(URL + '/health', timeout=1).ok
    except requests.RequestException:
        return False


def ensure_model():
    if ready():
        return
    exe = (ROOT / 'llama/llama-server.exe' if os.name == 'nt'
           else Path(os.environ.get('HOTEL_LLAMA_SERVER', ROOT / 'llama/llama-server')))
    if not installed() or not exe.exists():
        raise ValueError('模型或运行库未就绪。请完成视觉模型安装。')
    args = [str(exe), '-m', str(WEIGHTS / MODEL), '--mmproj', str(WEIGHTS / PROJECTOR),
            '--alias', 'hotel-vision', '--host', '127.0.0.1', '--port', '8767',
            *(['-ngl', '99', '--device', 'Vulkan0'] if os.name == 'nt' else ['-ngl', '0', '-t', '2']),
            '-c', '8192', '--parallel', '1',
            '--image-max-tokens', '768', '--no-webui', '--no-warmup', '--log-disable']
    with (ROOT / 'vision-model.log').open('ab') as log:
        proc = subprocess.Popen(args, stdout=log, stderr=log, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    for _ in range(90):
        if ready():
            return
        if proc.poll() is not None:
            raise ValueError('视觉模型启动失败，请检查 vision-model.log 与可用显存。')
        time.sleep(1)
    proc.terminate()
    raise ValueError('视觉模型启动超时，请重新分析。')


def store_event(folder, event, prefix, live=False):
    frames = []
    for i, (role, stamp, image) in enumerate(event.frames):
        filename = f'{prefix}-{i}.jpg'
        image.save(folder / filename, quality=94)
        frames.append(dict(file=filename, seconds=round(stamp - event.stamp if live else stamp, 3), role=role))
    return dict(person=event.person, seconds=frames[0]['seconds'], frames=frames)


def inspect_event(folder, report, event):
    ensure_model()
    crops = [folder / frame['file'] for frame in event['frames'] if frame['role'] in ('上身细节','放手后细节','转回正面上身')]
    body = [encoded_image(folder / f['file']) for f in event['frames'] if f['role'] == '全身细节'][:1]
    tie_style, hair_style = report['tieStyle'], report['hairStyle']
    hair_views = [encoded_image(folder / f['file']) for role in ('背面头部','左侧头部','右侧头部')
                  for f in event['frames'] if f['role'] == role]
    style, findings = check_person(crops, tie_style, hair_style, report['standard'], body, hair_views)
    applicable = {'领带款':tie_style, '扎发款':hair_style}.get(style,'\n'.join((tie_style,hair_style)))
    recovered = {}
    if not body and crops and not any(f['role']=='鞋履细节' for f in event['frames']):
        scenes = [f for f in event['frames'] if f['role']=='举手全景']
        if scenes:
            recovered = archived_lower_views(folder/scenes[0]['file'], crops[0])
    for name, roles in [('下装',('下装细节','放手后下装')),('皮鞋',('鞋履细节','放手后鞋履'))]:
        part = '鞋履' if name == '皮鞋' else name
        angles = tuple(view+part for view in ('左侧','背面','右侧','正面'))
        detail_frames = [f for f in event['frames'] if f['role'] in roles+angles]
        paths = [folder / f['file'] for f in detail_frames]
        context = [recovered[name]] if name in recovered else body
        findings.append(check_lower_body(paths, name, applicable+'\n'+report['standard'], context,
                                         [f['role'] for f in detail_frames]))
    return dict(frames=event['frames'], findings=findings,
                gesture=dict(person=event['person'], seconds=event['seconds'], style=style))


def run(folder, report, manual=True):
    try:
        report.update(state='分析中', error='')
        save(folder, report)
        checks = report['checks']
        if report.get('gestureVersion') and 'captureEvent' not in report:
            source = folder / ('source' + report['extension'])
            count = 0
            for idx, event in enumerate(scan(source)):
                stored = store_event(folder, event, f'gesture-{idx}')
                report['groups'].append(inspect_event(folder, report, stored))
                count += 1
                save(folder, report)
            report['gestureVersion'] = 2
            report['gestureCount'] = count
            checks = [name for name in checks if name != CHECK]
        if 'captureEvent' in report:
            report['groups'].append(inspect_event(folder, report, report['captureEvent']))
            report['gestureCount'] = 1
            checks = []
        if checks:
            ensure_model()
        windows = (media_groups(folder / ('source' + report['extension']), report['interval'])
                   if checks and 'captureFrames' not in report else ())
        if 'captureFrames' in report:
            frames = report['captureFrames']
            findings = inspect_group(report['checks'], [folder / f['file'] for f in frames],
                                     [f['seconds'] for f in frames], report['standard'])
            report['groups'].append({'frames': frames, 'findings': findings})
        for idx, window in enumerate(windows):
            paths = []
            frames = []
            for frame_index, (stamp, image) in enumerate(window):
                name = f'frame-{idx}-{frame_index}.jpg'
                target = folder / name
                image.save(target, quality=90)
                paths.append(target)
                frames.append({'file': name, 'seconds': stamp})
            findings = inspect_group(checks, paths, [f['seconds'] for f in frames], report['standard'])
            report['groups'].append({'frames': frames, 'findings': findings})
            save(folder, report)
        if not report['groups'] and report.get('gestureCount') != 0:
            raise ValueError('没有解码到有效画面')
        report['state'] = '已完成'
    except Exception as exc:
        logging.getLogger(__name__).exception('Vision inspection failed for %s', folder.name)
        report.update(state='失败', error=str(exc)[:220] if isinstance(exc, ValueError)
                      else '无法解码媒体或本地模型推理失败。请检查文件与本地服务后重试。')
    finally:
        try:
            save(folder, report)
        finally:
            if manual:
                SLOTS.release()
    return patrol_outcome(report)


def patrol_outcome(report):
    if report['state'] == '已完成' and 'cameraId' in report:
        findings = [f for g in report['groups'] for f in g['findings']]
        if any(f['status']=='发现异常' for f in findings):
            return '待复核：'+'、'.join(f['name']+('（'+f['level']+'）' if f.get('level') in ('取证缺失','标准缺失') else '')
                                      for f in findings if f['status']=='发现异常')
        return '含无法判断项' if any(f['status']=='无法判断' for f in findings) else '未见异常'
    return report['state']


def repair_gesture_times(patrol):
    """Repair v1 live clocks only when independent local evidence confirms the error."""
    with patrol.connection() as db:
        rows = db.execute('SELECT job, name, camera, started, ended FROM attempts '
                          'WHERE job=id AND started>ended').fetchall()
        for row in rows:
            job = row['job']
            if len(job) != 32 or any(c not in '0123456789abcdef' for c in job):
                continue
            folder = STORE / job
            try:
                report = read(folder)
                if (report.get('kind') != '举手仪容检查' or report.get('cameraId') != row['camera']
                        or report.get('id') != job or not report.get('captureEvent')):
                    continue
                if report.get('gestureClockVersion', 1) == 1:
                    original = report['created']
                    if abs(datetime.fromisoformat(original).timestamp() - row['started']) > .01:
                        continue
                    saved = (folder / 'gesture-0-0.jpg').stat().st_mtime
                    if saved > row['ended'] or row['started'] - saved < 60:
                        continue
                    captured = datetime.fromtimestamp(saved, timezone.utc)
                    report.update(created=captured.isoformat(), gestureClockVersion=2,
                                  timeCorrection=dict(originalCreated=original, source='证据写入时间'),
                                  filename=row['name']+' · 举手仪容检查 '+captured.astimezone().strftime('%H:%M:%S'))
                elif not report.get('timeCorrection'):
                    continue
                # Replay after an interruption between JSON, index and patrol writes.
                save(folder, report)
                db.execute('UPDATE attempts SET started=? WHERE id=?',
                           (datetime.fromisoformat(report['created']).timestamp(), job))
            except (FileNotFoundError, ValueError, KeyError) as exc:
                logging.getLogger(__name__).warning('无法核实旧举手记录时间 %s: %s', job, type(exc).__name__)


def submit_event(camera, event):
    job = uuid.uuid4().hex
    folder = STORE / job
    folder.mkdir()
    try:
        captured = datetime.fromtimestamp(event.stamp, timezone.utc)
        report = dict(id=job, model=MODEL_NAME, created=captured.isoformat(), kind='举手仪容检查',
                      filename=camera['name']+' · 举手仪容检查 '+captured.astimezone().strftime('%H:%M:%S'),
                      scene=SCENE, checks=[CHECK], standard=camera['standard'], cameraId=camera['id'],
                      tieStyle=camera['tieStyle'], hairStyle=camera['hairStyle'], gestureVersion=2, gestureClockVersion=2,
                      captureEvent=store_event(folder, event, 'gesture-0', live=True),
                      state='排队中', error='', review='待复核', groups=[])
        save(folder, report)
    except Exception:
        shutil.rmtree(folder)
        raise
    return job, POOL.submit(run, folder, report, False)


def submit_capture(camera, samples, captured, run_id):
    folder = STORE / run_id
    folder.mkdir()
    try:
        frames = []
        for i,(stamp,image) in enumerate(samples):
            name = f'frame-0-{i}.jpg'
            image.save(folder / name, quality=90)
            frames.append({'file': name, 'seconds': stamp})
        created = datetime.fromtimestamp(captured, timezone.utc)
        report = dict(id=run_id, model=MODEL_NAME, created=created.isoformat(),
                      filename=camera['name']+' · '+created.astimezone().strftime('%m-%d %H:%M:%S'),
                      scene=camera['scene'], checks=[name for name in PROFILES[camera['scene']]
                          if camera['scene'] != SCENE or name != CHECK], standard=camera['standard'],
                      interval=0.5, state='排队中', error='', review='待复核', groups=[],
                      kind='定期抓拍', cameraId=camera['id'], captureFrames=frames)
        save(folder, report)
        return run_id, POOL.submit(run, folder, report, False)
    except Exception:
        shutil.rmtree(folder)
        with report_index() as db:
            db.execute('DELETE FROM jobs WHERE id=?',(run_id,))
        raise


@router.get('/status')
def status():
    return {'installed': installed(), 'model': MODEL_NAME, 'profiles': PROFILES, 'poseInstalled': pose_ready()}


@router.get('/jobs')
def jobs(limit: int = -1, offset: int = 0, pending:bool=False):
    with report_index() as db:
        db.row_factory = sqlite3.Row
        where=" WHERE review='待整改'" if pending else ''
        rows = db.execute('SELECT * FROM jobs'+where+' ORDER BY created DESC,id LIMIT ? OFFSET ?',
                          (limit, max(offset,0))).fetchall()
    return [dict(row) for row in rows]


@router.get('/jobs/{job_id}')
def detail(job_id: str):
    return read(find(job_id))


@router.get('/jobs/{job_id}/frames/{name}')
def frame(job_id: str, name: str):
    folder = find(job_id)
    report = read(folder)
    if not any(f['file'] == name for group in report['groups'] for f in group['frames']):
        raise HTTPException(404, '找不到证据图')
    return FileResponse(folder / name, media_type='image/jpeg')


@router.get('/jobs/{job_id}/export', response_class=HTMLResponse)
def export(job_id: str):
    folder = find(job_id)
    r = read(folder)
    if r['state'] != '已完成':
        raise HTTPException(409, '报告尚未完成')
    title = escape(r['filename'])
    sections = [f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>{title} · 巡检报告</title>',
                '<style>body{font:16px/1.6 system-ui;max-width:850px;margin:32px auto;padding:0 20px}'
                'img{max-width:100%;max-height:380px}figure{margin:10px 0}li{margin:8px 0}'
                '@media print{figure,li{break-inside:avoid}}</style>',
                f'<h1>{title}</h1><p>{escape(r["scene"])} · {escape(r["model"])} · {escape(r["created"])}</p>',
                f'<p>复核状态：{escape(r["review"])}　{escape(r.get("reviewNote",""))}</p>',
                f'<p>现场标准：{escape(r["standard"] or "默认")}</p>',
                '<p>结论仅适用于以下采样画面。未采样的时段可能包含其他事件。</p>']
    if r.get('gestureCount') == 0:
        sections.append(f"<p>{TURN_NO_EVENT if r.get('gestureVersion',1)>=2 else NO_EVENT}</p>")
    for i, group in enumerate(r['groups'], 1):
        sections.append(f'<h2>第 {i} 组证据</h2>')
        if gesture := group.get('gesture'):
            sections.append(f'<p>举手者 {gesture["person"]} · {gesture["seconds"]:.2f} 秒 · {escape(gesture["style"])}</p>')
        for frame_item in group['frames']:
            label = '输入图片' if frame_item['seconds'] is None else f'{frame_item["seconds"]:.2f} 秒'
            if 'role' in frame_item:
                label += ' · ' + escape(frame_item['role'])
            data = encoded_image(folder / frame_item['file'])
            sections.append(f'<figure><img src="{data}" alt="证据图"><figcaption>{label}</figcaption></figure>')
        sections.append('<ul>')
        for finding in group['findings']:
            fields = ' · '.join(escape(finding[k]) for k in ('name','status','level') if finding[k])
            sections.append(f'<li><b>{fields}</b>　{escape(finding["detail"])}</li>')
        sections.append('</ul>')
    return HTMLResponse(''.join(sections) + '</html>', headers={
        'Content-Disposition': f'attachment; filename="inspection-{job_id[:8]}.html"'})


@router.post('/jobs', status_code=202)
def create(file: UploadFile = File(...), scene: str = Form('公区卫生'), interval: float = Form(5),
           standard: str = Form(''), checks: str = Form(''), tieStyle: str = Form('', max_length=400),
           hairStyle: str = Form('', max_length=400)):
    ext = Path(file.filename or '').suffix.lower()
    if ext not in IMAGE | VIDEO or scene not in PROFILES or not 1 <= interval <= 120 or len(standard) > 1200:
        raise HTTPException(400, '文件类型、场景、抽样间隔或检查标准无效')
    try:
        selected = json.loads(checks) if checks else PROFILES[scene]
        if not isinstance(selected, list) or not selected or not all(n in PROFILES[scene] for n in selected):
            raise ValueError
        selected = list(dict.fromkeys(selected))
    except (ValueError, TypeError):
        raise HTTPException(400, '请选择有效的检查项')
    if not installed():
        raise HTTPException(503, '本地视觉模型尚未就绪')
    gesture_scan = ext in VIDEO and scene == SCENE and CHECK in selected
    if gesture_scan and not pose_ready():
        raise HTTPException(503, '姿态模型未安装，请运行 python -m local.setup_vision')
    if not SLOTS.acquire(blocking=False):
        raise HTTPException(429, '已有四个巡检任务，请稍后再提交')
    folder = STORE / uuid.uuid4().hex
    try:
        folder.mkdir()
        size = 0
        with (folder / ('source' + ext)).open('wb') as out:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > 300 * 1024**2:
                    raise HTTPException(413, '单个文件限 300 MB')
                out.write(block)
        if not size:
            raise HTTPException(400, '文件为空')
        report = {'id': folder.name, 'model': MODEL_NAME, 'created': datetime.now(timezone.utc).isoformat(),
                  'filename': Path(file.filename).name, 'extension': ext, 'scene': scene, 'checks': selected,
                  'standard': standard.strip(), 'interval': interval, 'state': '排队中', 'error': '',
                  'review': '待复核', 'groups': [], 'kind': '图片' if ext in IMAGE else '视频'}
        if gesture_scan:
            report.update(gestureVersion=2, tieStyle=tieStyle.strip(), hairStyle=hairStyle.strip())
        save(folder, report)
        POOL.submit(run, folder, report)
        return {'id': folder.name}
    except Exception:
        SLOTS.release()
        if folder.exists():
            shutil.rmtree(folder)
        raise


def retest_saved(folder, report, settings, patrol=None):
    """Replace all findings together; keep the last completed report on a failure."""
    try:
        rules = {**report, **settings}
        groups = []
        ensure_model()
        for group in report['groups']:
            frames = group['frames']
            if 'gesture' in group:
                event = {**group['gesture'], 'frames':frames}
                groups.append(inspect_event(folder, rules, event))
            else:
                checks = [c for c in rules['checks'] if not (rules.get('gestureVersion') and c == CHECK)]
                findings = inspect_group(checks, [folder/f['file'] for f in frames],
                                         [f['seconds'] for f in frames], rules['standard'])
                groups.append({**group, 'findings':findings})
        if not report['groups']:
            if not report.get('gestureVersion') or not report.get('extension'):
                raise ValueError('没有可供复测的已保存画面。')
            prefix = 'retest-' + uuid.uuid4().hex
            for i, event in enumerate(scan(folder/('source'+report['extension']))):
                stored = store_event(folder,event,f'{prefix}-{i}')
                groups.append(inspect_event(folder,rules,stored))
            report['gestureVersion'] = 2
        if 'gestureCount' in report:
            report['gestureCount'] = sum('gesture' in group for group in groups)
        report.update(settings)
        report.update(groups=groups, retestedAt=datetime.now(timezone.utc).isoformat(), retestError='')
    except Exception as exc:
        logging.getLogger(__name__).exception('Vision retest failed for %s', folder.name)
        report['retestError'] = (str(exc)[:200] if isinstance(exc,ValueError) else '无法读取证据或模型运行失败。') + ' 已保留上次结果。'
    finally:
        try:
            report['state'] = '已完成'
            save(folder,report)
            if patrol is not None and not report.get('retestError'):
                with patrol.connection() as db:
                    db.execute('UPDATE attempts SET outcome=? WHERE job=? AND camera=?',
                               (patrol_outcome(report),report['id'],report['cameraId']))
        finally:
            SLOTS.release()


@router.post('/jobs/{job_id}/retest', status_code=202)
def retest(job_id: str, request: Request):
    folder = find(job_id)
    with LOCK:
        report = read(folder)
        if report['state'] != '已完成':
            raise HTTPException(409, '当前任务尚未完成或正在复测。')
        settings = {}
        if report.get('cameraId'):
            with request.app.state.patrol.connection() as db:
                row = db.execute('SELECT standard,tieStyle,hairStyle FROM cameras WHERE id=? AND scene=?',
                                 (report['cameraId'],report['scene'])).fetchone()
            if row:
                settings = dict(row)
        if not SLOTS.acquire(blocking=False):
            raise HTTPException(429, '请等待队列中的任务完成')
        try:
            report.update(state='复测中', retestError='')
            save(folder,report)
            patrol = request.app.state.patrol if report.get('kind')=='举手仪容检查' and report.get('cameraId') else None
            POOL.submit(retest_saved,folder,report,settings,patrol)
        except Exception:
            SLOTS.release()
            report['state'] = '已完成'
            save(folder,report)
            raise
    return {'id':job_id}


class Review(BaseModel):
    state: str
    note: str = Field(default='', max_length=1000)


@router.post('/jobs/{job_id}/review')
def review(job_id: str, body: Review):
    folder = find(job_id)
    with LOCK:
        report = read(folder)
        if report['state'] != '已完成' or body.state not in ('待复核', '已确认', '已标误报', '待整改', '已复查'):
            raise HTTPException(400, '当前记录无法执行该复核操作')
        report.update(review=body.state, reviewNote=body.note, reviewedAt=datetime.now(timezone.utc).isoformat())
        save(folder, report)
    return report


@router.post('/jobs/{job_id}/retry', status_code=202)
def retry(job_id: str):
    folder = find(job_id)
    with LOCK:
        report = read(folder)
        if report['state'] != '失败':
            raise HTTPException(409, '只有失败的任务可重新分析')
        if not SLOTS.acquire(blocking=False):
            raise HTTPException(429, '请等待队列中的任务完成')
        report.update(state='排队中', groups=[], error='')
        report.pop('gestureCount', None)
        save(folder, report)
        POOL.submit(run, folder, report)
    return {'id': job_id}

