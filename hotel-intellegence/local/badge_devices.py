"""Per-device registry and shared, read-through vendor presence cache."""
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

CHINA = timezone(timedelta(hours=8))
TYPES = {'Hanging4G': '挂牌', 'Bg4g': '胸牌'}
ONLINE = {'Online': '在线', 'Recording': '录音中', 'Uploading': '上传中', 'Upgrading': '升级中'}


class Device(BaseModel):
    number: str = Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')
    source: Literal['胸牌', '挂牌']


class Profile(BaseModel):
    name: str = Field(default='', max_length=80)
    position: str = Field(default='', max_length=80)
    test: bool = False


class NewDevice(Device, Profile):
    pass


class Batch(BaseModel):
    app: str
    devices: list[NewDevice] = Field(min_length=1, max_length=100)


class Edit(Profile):
    revision: int
    enabled: bool
    retired: bool
    replacement: Device | None = None


def mirror(db, app):
    devices = [dict(r) for r in db.execute(
        'SELECT number,source FROM badge_devices WHERE app=? AND retired=0 ORDER BY id', (app,))]
    db.execute('UPDATE badge_sync_config SET devices=? WHERE app=?', (json.dumps(devices), app))


def cached_state(row, updated, now):
    if not updated or (now - updated).total_seconds() >= 90:
        return '待确认', '状态已过期' if updated else '尚未更新'
    if not row:
        return '待确认', '厂商未返回该设备'
    status = row['status']
    if status == 'Offline':
        return '离线', '离线'
    if status not in ONLINE:
        return '待确认', '尚未激活' if status == 'Factory' else '未知设备状态'
    try:
        beat = datetime.fromisoformat(row['heartbeat'])
        if not beat.tzinfo:
            beat = beat.replace(tzinfo=CHINA)
        interval = int(row['interval'])
        if interval > 0 and (now - beat).total_seconds() > 3 * interval:
            return '待确认', '心跳延迟'
    except (TypeError, ValueError):
        pass
    return '在线', ONLINE[status]


class Directory:
    def __init__(self, sync, fetch):
        self.sync = sync
        self.fetch = fetch
        self.lock = threading.Lock()
        self.key = None
        self.items = {}
        self.updated = None
        self.attempt = 0
        self.error = ''

    def snapshot(self, force=False):
        with self.lock:
            cfg = self.sync.settings()
            key = (cfg['app'], cfg['secret']) if cfg else None
            if key != self.key:
                self.key, self.items, self.updated, self.attempt, self.error = key, {}, None, 0, ''
            if key and (not self.attempt or time.monotonic()-self.attempt >= (3 if force else 30)):
                try:
                    self.items = self.fetch(cfg)
                    self.updated = datetime.now(CHINA)
                    self.error = ''
                except Exception:
                    self.error = '厂商状态更新失败，请检查平台连接或稍后刷新'
                self.attempt = time.monotonic()
            return self.items, self.updated, self.error, key[0] if key else ''

    def rows(self):
        with self.sync.connection() as db:
            rows = db.execute("""SELECT d.id,d.number,d.source,d.name,d.position,d.test,d.enabled,d.retired,
                    d.revision,d.synced,d.error FROM badge_devices d JOIN badge_sync_config c ON d.app=c.app
                    ORDER BY d.retired,d.id""").fetchall()
            config = db.execute('SELECT app,enabled FROM badge_sync_config WHERE id=1').fetchone()
        with self.sync.lock:
            syncing = sorted(self.sync.syncing)
        return dict(devices=[dict(r) for r in rows], connected=bool(config), enabled=bool(config and config['enabled']), syncing=syncing,current=next(iter(syncing),None), syncError=self.sync.error)

    def presence(self, force: bool = False):
        devices = self.rows()['devices']
        if not any(not d['retired'] for d in devices):
            return dict(states={}, updated=None, error='')
        items, updated, error, _ = self.snapshot(force)
        now = datetime.now(CHINA)
        states = {}
        for d in devices:
            if d['retired']:
                continue
            row = items.get(d['number'])
            group, detail = cached_state(row, updated, now)
            if row and row['source'] != d['source']:
                group, detail = '待确认','设备类型不符'
            states[d['id']] = dict(group=group, detail=detail, heartbeat=row and row['heartbeat'],
                                   battery=row and row['battery'], vendorStatus=row and row['status'])
        return dict(states=states, updated=updated, error=error)

    def router(self):
        routes = APIRouter(prefix='/api/badge/devices')
        routes.get('')(self.rows)
        routes.get('/presence')(self.presence)

        @routes.get('/catalog')
        def catalog():
            items, _, error, app = self.snapshot()
            return dict(devices=[dict(number=k, source=v['source']) for k,v in items.items()], error=error, app=app)

        @routes.post('')
        def add(batch: Batch):
            with self.sync.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                cfg = db.execute('SELECT app FROM badge_sync_config').fetchone()
                if not cfg or cfg['app'] != batch.app:
                    raise HTTPException(409, '平台连接已变更，请重新打开添加设备')
                for d in batch.devices:
                    existing = db.execute('SELECT source,retired FROM badge_devices WHERE app=? AND number=?',
                                          (batch.app, d.number)).fetchone()
                    if existing:
                        if existing['retired'] or existing['source'] != d.source:
                            raise HTTPException(409, '编号已登记，请在原设备档案中查看或恢复')
                        continue
                    db.execute('INSERT INTO badge_devices(app,number,source,name,position,test) VALUES(?,?,?,?,?,?)',
                               (batch.app,d.number,d.source,d.name.strip(),d.position.strip(),False))
                mirror(db,batch.app)
            self.sync.wake.set()
            return self.rows()

        @routes.patch('/{device_id}')
        def change(device_id: int, body: Edit):
            with self.sync.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("""SELECT d.* FROM badge_devices d JOIN badge_sync_config c ON c.app=d.app
                                    WHERE d.id=?""",(device_id,)).fetchone()
                if not row or row['revision'] != body.revision:
                    raise HTTPException(409, '设备已变更，请重新打开编辑')
                if body.replacement:
                    d = body.replacement
                    if row['retired'] or db.execute('SELECT 1 FROM badge_devices WHERE app=? AND number=?',
                                                    (row['app'],d.number)).fetchone():
                        raise HTTPException(409, '请选择未登记的新编号，并确认原设备在用')
                    db.execute('INSERT INTO badge_devices(app,number,source,name,position,test,enabled) VALUES(?,?,?,?,?,?,?)',
                               (row['app'],d.number,d.source,body.name.strip(),body.position.strip(),False,body.enabled))
                    db.execute('UPDATE badge_devices SET retired=1,revision=revision+1 WHERE id=?',(device_id,))
                else:
                    db.execute("""UPDATE badge_devices SET name=?,position=?,test=?,enabled=?,retired=?,
                                  revision=revision+1 WHERE id=?""",
                               (body.name.strip(),body.position.strip(),False,body.enabled,body.retired,device_id))
                mirror(db,row['app'])
            self.sync.wake.set()
            return self.rows()

        return routes

