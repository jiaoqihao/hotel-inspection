"""Periodic RTSP snapshots. One service process owns the scheduler and four workers."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from urllib.parse import urlsplit
import uuid

import av
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr, ValidationError

from local.vision_model import PROFILES, thumbnail
from local.frontdesk_watch import Watchers
from local.frontdesk_pose import SCENE

WORKERS = 4
MIN_FREE = 2 * 1024**3
LOG = logging.getLogger(__name__)
router = APIRouter(prefix='/api/patrol')


class Blob(ctypes.Structure):
    _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(value: bytes, decrypt=False) -> bytes:
    if os.name != 'nt':
        from cryptography.fernet import Fernet
        cipher = Fernet(os.environ['HOTEL_CREDENTIAL_KEY'].encode('ascii'))
        return cipher.decrypt(value) if decrypt else cipher.encrypt(value)
    # Windows DPAPI, current user; keep the entire RTSP URI out of plaintext storage.
    buf = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, result = Blob(len(value), buf), Blob()
    operation = (ctypes.windll.crypt32.CryptUnprotectData if decrypt
                 else ctypes.windll.crypt32.CryptProtectData)
    if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError('无法使用当前 Windows 账户读取或保存摄像头凭据')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


class Camera(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: SecretStr | None = None
    minutes: int = Field(default=10, ge=1, le=1440)
    scene: str = '公区卫生'
    standard: str = Field(default='', max_length=1200)
    tieStyle: str = Field(default='', max_length=400)
    hairStyle: str = Field(default='', max_length=400)


def settings(camera, require_url=True):
    name = camera.name.strip()
    if not name or '://' in name or camera.scene not in PROFILES:
        raise ValueError('请填写点位名称并选择有效场景')
    data = dict(name=name, minutes=camera.minutes, scene=camera.scene, standard=camera.standard.strip())
    for field in ('tieStyle', 'hairStyle'):
        if require_url or field in camera.model_fields_set:
            data[field] = getattr(camera, field).strip()
    url = camera.url.get_secret_value().strip() if camera.url else ''
    if not url and not require_url:
        return data
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (len(url) > 2048 or parts.scheme.lower() != 'rtsp' or not host or parts.fragment
                or any(c.isspace() or ord(c) < 32 for c in url)):
            raise ValueError
        endpoint = f'{host.lower()}:{parts.port or 554}{parts.path}?{parts.query}'
    except ValueError:
        raise ValueError('请提供有效的 rtsp:// 视频流地址') from None
    data.update(secret=protect(url.encode()), fingerprint=hashlib.sha256(endpoint.encode()).hexdigest())
    return data


def capture(url, sequence):
    # Keep the decoded image in memory until the stream has been closed.
    result = []
    with av.open(url, format='rtsp', options={'rtsp_transport': 'tcp', 'allowed_media_types': 'video'},
                 timeout=(5, 7)) as src:
        stream = src.streams.video[0]
        deadline = time.monotonic() + 9
        start_pts = None
        start_clock = time.monotonic()
        for frame in src.decode(stream):
            if time.monotonic() > deadline:
                break
            if frame.pts is not None and frame.time_base is not None:
                stamp = float(frame.pts * frame.time_base)
                if start_pts is None:
                    start_pts = stamp
                offset = stamp - start_pts
            else:
                offset = time.monotonic() - start_clock
            if result and offset < len(result) * .5:
                continue
            result.append((round(max(offset, 0), 3) if sequence else None, thumbnail(frame.to_image())))
            if not sequence or len(result) == 3:
                break
    if not result:
        raise ValueError('no video frame')
    return result


class Patrol:
    def __init__(self, root, submit, grab=capture, workers=WORKERS, submit_event=None):
        self.root = Path(root)
        self.db = self.root / 'patrol.sqlite3'
        self.submit, self.grab, self.workers = submit, grab, workers
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread = None
        self.watchers = Watchers(self, submit_event) if submit_event else None
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cameras(
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, secret BLOB NOT NULL,
                  fingerprint TEXT UNIQUE NOT NULL, minutes INTEGER NOT NULL, scene TEXT NOT NULL,
                  standard TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, due REAL NOT NULL,
                  phase TEXT NOT NULL DEFAULT 'idle', started REAL, ended REAL, outcome TEXT,
                  last_job TEXT, active_job TEXT);
                CREATE INDEX IF NOT EXISTS camera_due ON cameras(enabled,phase,due);
                CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,camera TEXT,name TEXT,
                  started REAL,ended REAL,outcome TEXT,job TEXT);
                CREATE INDEX IF NOT EXISTS attempts_time ON attempts(started DESC);
            """)
            db.execute('BEGIN IMMEDIATE')
            if db.execute('PRAGMA user_version').fetchone()[0] < 1:
                db.execute("ALTER TABLE cameras ADD COLUMN tieStyle TEXT NOT NULL DEFAULT ''")
                db.execute("ALTER TABLE cameras ADD COLUMN hairStyle TEXT NOT NULL DEFAULT ''")
                db.execute('ALTER TABLE cameras ADD COLUMN gesture_job TEXT')
                db.execute('PRAGMA user_version=1')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.db, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, cameras):
        data = [settings(c) for c in cameras]
        now = time.time()
        with self.connection() as db:
            for index, row in enumerate(data):
                db.execute("""INSERT INTO cameras(id,name,secret,fingerprint,minutes,scene,standard,due,tieStyle,hairStyle)
                    VALUES(:id,:name,:secret,:fingerprint,:minutes,:scene,:standard,:due,:tieStyle,:hairStyle)""",
                           dict(row, id=uuid.uuid4().hex, due=now+index*.5))
        self.wake.set()

    def start(self):
        self.stop_event.clear()
        with self.connection() as db:
            db.execute("UPDATE attempts SET ended=?,outcome='服务中断' WHERE ended IS NULL", (time.time(),))
            db.execute("""UPDATE cameras SET phase='idle',ended=?,outcome='服务中断',
                       active_job=NULL WHERE phase!='idle'""", (time.time(),))
        self.pool = ThreadPoolExecutor(max_workers=self.workers, thread_name_prefix='patrol-capture')
        self.thread = threading.Thread(target=self.loop, name='patrol-scheduler', daemon=True)
        self.thread.start()
        if self.watchers:
            self.watchers.start()

    def stop(self):
        self.stop_event.set()
        self.wake.set()
        if self.watchers:
            self.watchers.stop()
        if self.thread:
            self.thread.join()
            self.pool.shutdown(wait=True)

    def claim(self):
        now = time.time()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            occupied = db.execute("SELECT count(*) FROM cameras WHERE phase!='idle'").fetchone()[0]
            if occupied >= self.workers:
                return None
            row = db.execute("""SELECT * FROM cameras WHERE enabled=1 AND phase='idle' AND due<=?
                             ORDER BY due,id LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            run_id = uuid.uuid4().hex
            period = row['minutes'] * 60
            due = row['due'] + (int((now-row['due']) // period)+1)*period
            db.execute("UPDATE cameras SET phase='capture',started=?,due=?,active_job=? WHERE id=?",
                       (now,due,run_id,row['id']))
            db.execute("INSERT INTO attempts(id,camera,name,started,outcome) VALUES(?,?,?,?,'抓帧中')",
                       (run_id,row['id'],row['name'],now))
            return dict(row), run_id

    def loop(self):
        while not self.stop_event.is_set():
            try:
                task = self.claim()
                if task:
                    self.pool.submit(self.execute, *task)
                    continue
            except Exception:
                LOG.exception('巡检调度失败')
            self.wake.wait(1)
            self.wake.clear()

    def execute(self, camera, run_id):
        stage = '抓帧失败，请检查设备连接及凭据'
        job = None
        try:
            if shutil.disk_usage(self.root).free < MIN_FREE:
                stage = '剩余磁盘不足 2 GB，已跳过本次巡检'
                raise OSError()
            url = protect(camera['secret'], decrypt=True).decode()
            with av.logging.Capture(local=True):
                samples = self.grab(url, '双手递物' in PROFILES[camera['scene']])
            captured = time.time()
            stage = '本地模型分析失败'
            with self.connection() as db:
                db.execute("UPDATE cameras SET phase='analysis' WHERE id=?", (camera['id'],))
                db.execute("UPDATE attempts SET outcome='等待分析' WHERE id=?", (run_id,))
            job, future = self.submit(camera, samples, captured, run_id)
            with self.connection() as db:
                db.execute('UPDATE attempts SET job=? WHERE id=?',(job,run_id))
                db.execute('UPDATE cameras SET last_job=? WHERE id=?',(job,camera['id']))
            outcome = future.result()
        except Exception:
            # Camera/decoder exceptions can contain the authenticated URL.
            outcome = stage
        finally:
            finished = time.time()
            with self.connection() as db:
                db.execute('UPDATE attempts SET ended=?,outcome=?,job=? WHERE id=?',
                           (finished,outcome,job,run_id))
                row = db.execute('SELECT due,minutes FROM cameras WHERE id=?',(camera['id'],)).fetchone()
                if row:
                    period = row['minutes'] * 60
                    due = row['due']
                    if due < finished:
                        due += (int((finished-due)//period)+1)*period
                    db.execute("""UPDATE cameras SET phase='idle',ended=?,outcome=?,active_job=NULL,due=?
                                WHERE id=?""", (finished,outcome,due,camera['id']))
            self.wake.set()

    def overview(self):
        with self.connection() as db:
            cameras = db.execute("""SELECT id,name,scene,standard,tieStyle,hairStyle,gesture_job,minutes,enabled,due,phase,
                started,ended,outcome,last_job FROM cameras ORDER BY name,id""").fetchall()
            history = db.execute("""SELECT name,started,ended,outcome,job FROM attempts
                                     ORDER BY started DESC LIMIT 40""").fetchall()
        statuses = self.watchers.snapshot() if self.watchers else {}
        cameras = [dict(r) for r in cameras]
        for camera in cameras:
            if camera['scene'] == SCENE:
                camera['gestureStatus'] = statuses.get(camera['id'], '等待监听') if camera['enabled'] else '已暂停监听'
        return {'cameras': cameras, 'history': [dict(r) for r in history],
                'workers': self.workers, 'running': bool(self.thread and self.thread.is_alive())}


def service(request):
    return request.app.state.patrol


@router.get('')
def overview(request: Request):
    return service(request).overview()


@router.post('/cameras')
async def add(request: Request):
    # Avoid putting a user-supplied URL in a validation error response.
    try:
        body = await request.json()
        if not isinstance(body, list) or not 1 <= len(body) <= 256:
            raise ValueError('每批可添加 1 至 256 个点位')
        cameras = [Camera.model_validate(c) for c in body]
        service(request).add(cameras)
    except sqlite3.IntegrityError:
        raise HTTPException(409, '存在重复视频流，本批未添加') from None
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(400, '请检查名称、RTSP 地址、场景及巡检间隔（1–1440 分钟）') from None
    return {'added': len(cameras)}


@router.put('/cameras/{key}')
async def edit(key: str, request: Request):
    try:
        data = settings(Camera.model_validate(await request.json()), require_url=False)
        with service(request).connection() as db:
            fields = ','.join(col+'=:'+col for col in data)
            changed = db.execute('UPDATE cameras SET '+fields+
                                 ',due=:due WHERE id=:id',dict(data,id=key,due=time.time()+data['minutes']*60))
            if not changed.rowcount:
                raise HTTPException(404,'点位不存在')
    except sqlite3.IntegrityError:
        raise HTTPException(409,'该视频流已存在') from None
    except (ValidationError, ValueError, TypeError):
        raise HTTPException(400,'点位配置无效') from None
    return {'ok': True}


@router.post('/cameras/{key}/{action}')
def control(key: str, action: str, request: Request):
    patrol = service(request)
    with patrol.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM cameras WHERE id=?',(key,)).fetchone()
        if not row:
            raise HTTPException(404,'点位不存在')
        if action == 'pause':
            db.execute('UPDATE cameras SET enabled=0 WHERE id=?',(key,))
        elif action == 'resume':
            db.execute('UPDATE cameras SET enabled=1,due=? WHERE id=?',(time.time(),key))
        elif action == 'now':
            if not row['enabled'] or row['phase'] != 'idle':
                raise HTTPException(409,'请先启用点位并等待当前巡检结束')
            db.execute('UPDATE cameras SET due=? WHERE id=?',(time.time(),key))
        elif action == 'remove':
            if row['phase'] != 'idle':
                raise HTTPException(409,'请暂停点位，待当前巡检结束后移除')
            db.execute('DELETE FROM cameras WHERE id=?',(key,))
        else:
            raise HTTPException(404,'未知操作')
    patrol.wake.set()
    return {'ok': True}

