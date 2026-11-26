"""Poll Yuehangyi's audio list from the store PC and archive each segment once."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit

from concurrent.futures import ThreadPoolExecutor
import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, SecretStr
from local.patrol import protect

BASE = 'https://api.yuehangyi.com'
CHINA = timezone(timedelta(hours=8))
PERIOD = 60


from local.badge_devices import Device, Directory, TYPES, cached_state, mirror


class Configuration(BaseModel):
    appId: str = Field(min_length=1, max_length=200)
    secret: SecretStr | None = None
    devices: list[Device] | None = Field(default=None, max_length=100)
    test: bool = False


class SyncError(Exception):
    """A diagnostic safe to show without credentials or signed download URLs."""


def vendor(path, body, token=None):
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    response = requests.post(BASE + path, json=body, headers=headers, timeout=(8, 30), allow_redirects=False)
    if path == '/auth/token' and response.status_code == 400:
        raise SyncError('厂商拒绝签发令牌（HTTP 400），请核对所属机构、AppId 和 AppSecret')
    if response.status_code != 200:
        raise SyncError(f'{path} 返回 HTTP {response.status_code}')
    payload = response.json()
    if payload.get('success') is not True:
        raise SyncError(f'{path} 请求未获批准（{str(payload.get("code", ""))[:12]}），请核对开发凭据和 IP 白名单')
    return payload['data']


def access_token(app_id, secret):
    timestamp = int(time.time())
    signature = hashlib.md5(f'{app_id}{secret}v1{timestamp}CreateToken'.encode()).hexdigest()
    data = vendor('/auth/token', dict(accessKey=app_id, accessSecret=secret, version='v1',
                                      timestamp=timestamp, action='CreateToken', signature=signature))
    return data['accessToken']


class BadgeSync:
    def __init__(self, connection, ingest, queue_analysis):
        self.connection = connection
        self.ingest = ingest
        self.queue_analysis = queue_analysis
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.directory = Directory(self, self.fetch_catalog)
        self.syncing = set()
        self.busy = False
        self.last = ''
        self.error = ''

    def fetch_catalog(self, config):
        token = access_token(config['app'], protect(config['secret'], decrypt=True).decode())
        result = {}
        page = 1
        while True:
            data = vendor('/Device/Page', dict(page=page,pageSize=100), token)
            for item in data['items']:
                source = TYPES.get(item.get('deviceType'))
                if source and item.get('deviceNo'):
                    result[item['deviceNo']] = dict(source=source,status=item.get('status'),
                        heartbeat=item.get('lastHeartbeatTime'),interval=item.get('heartbeatInterval'),
                        battery=item.get('remainPower'))
            if not data.get('hasNextPages') and page >= (data.get('totalPages') or 1):
                return result
            if not data['items'] or page >= 10000:
                raise SyncError('设备分页结果异常')
            page += 1

    def settings(self):
        with self.connection() as db:
            return db.execute('SELECT * FROM badge_sync_config WHERE id=1').fetchone()

    def status(self):
        row = self.settings()
        with self.connection() as db:
            count = db.execute('SELECT count(*) FROM badge_sync_files').fetchone()[0]
        return dict(enabled=bool(row and row['enabled']), appId=row['app'] if row else '',
                    devices=json.loads(row['devices']) if row else [], test=bool(row['test']) if row else True,
                    imported=count, busy=self.busy, last=self.last, error=self.error)

    def configure(self, config):
        if config.devices and len({d.number for d in config.devices}) != len(config.devices):
            raise HTTPException(400, '设备编号重复')
        supplied_secret = config.secret.get_secret_value().strip() if config.secret else ''
        if supplied_secret:
            try:
                token = access_token(config.appId, supplied_secret)
                page = vendor('/Device/Page', dict(page=1, pageSize=1), token)
                if not isinstance(page, dict) or not isinstance(page.get('items'), list):
                    raise SyncError('厂商设备清单格式异常')
            except SyncError as exc:
                raise HTTPException(400, f'{exc}。本次配置未保存') from None
            except (requests.RequestException, KeyError, ValueError, TypeError):
                raise HTTPException(502, '暂时无法验证悦航益连接，本次配置未保存，请稍后再试') from None
        with self.lock, self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM badge_sync_config WHERE id=1').fetchone()
            if supplied_secret:
                secret = protect(supplied_secret.encode())
            elif old and old['app'] == config.appId:
                secret = old['secret']
            else:
                raise HTTPException(400, '请输入该 AppId 对应的 AppSecret')
            db.execute('INSERT OR REPLACE INTO badge_sync_config VALUES(1,?,?,?,?,1)',
                       (config.appId, secret, '[]', False))
            if config.devices is not None:
                keep = {d.number for d in config.devices}
                for d in config.devices:
                    existing = db.execute('SELECT source FROM badge_devices WHERE app=? AND number=?',
                                          (config.appId,d.number)).fetchone()
                    if existing and existing['source'] != d.source:
                        raise HTTPException(409, '设备编号已用于另一种设备')
                    db.execute('''INSERT INTO badge_devices(app,number,source,test) VALUES(?,?,?,?)
                                  ON CONFLICT(app,number) DO UPDATE SET retired=0,revision=revision+1
                                  WHERE retired=1''',(config.appId,d.number,d.source,False))
                for d in db.execute('SELECT id,number FROM badge_devices WHERE app=? AND retired=0',(config.appId,)).fetchall():
                    if d['number'] not in keep:
                        db.execute('UPDATE badge_devices SET retired=1,revision=revision+1 WHERE id=?',(d['id'],))
            mirror(db,config.appId)
        self.error = ''
        self.wake.set()

    def run(self):
        while not self.stop.is_set():
            self.wake.clear()
            started = time.monotonic()
            row = self.settings()
            if row and row['enabled']:
                self.busy = True
                try:
                    self.sync(row)
                except SyncError as exc:
                    self.error = str(exc)
                except Exception as exc:
                    self.error = f'平台同步失败（{type(exc).__name__}），将在下次同步时重试'
                finally:
                    self.busy = False
                    self.last = datetime.now(CHINA).isoformat(timespec='seconds')
            try:
                self.queue_analysis()
            except Exception as exc:
                self.error = f'录音分析队列暂不可用（{type(exc).__name__}），将在下次检查时重试'
            if not self.stop.is_set():
                self.wake.wait(max(0, PERIOD - (time.monotonic() - started)))

    def sync(self, config):
        items, updated, catalog_error, app = self.directory.snapshot()
        if app != config['app']:
            return
        now = datetime.now(CHINA)
        if not updated or (now - updated).total_seconds() >= 90:
            raise SyncError(catalog_error or '设备状态已过期，等待下一次同步')
        errors = [catalog_error] if catalog_error else []
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('SELECT 1 FROM badge_sync_config WHERE app=? AND enabled=1', (app,)).fetchone()
            if not active:
                return
            # Existing rows retain local names, pauses and retirements across platform discovery.
            created = db.executemany(
                'INSERT OR IGNORE INTO badge_devices(app,number,source,test) VALUES(?,?,?,0)',
                [(app, number, row['source']) for number, row in items.items()])
            if created.rowcount:
                mirror(db, app)
            registered = db.execute('SELECT * FROM badge_devices WHERE app=? AND enabled=1 AND retired=0',
                                    (app,)).fetchall()
        devices = [d for d in registered if d['number'] in items and items[d['number']]['source'] == d['source']
                   and cached_state(items[d['number']], updated, now)[0] == '在线']
        if not devices:
            self.error = '；'.join(errors)
            return
        token = access_token(config['app'], protect(config['secret'], decrypt=True).decode())
        def poll_device(device):
            if self.stop.is_set():
                return ''
            with self.lock:
                self.syncing.add(device['id'])
            try:
                self.sync_device(config, device, token)
                message = ''
            except SyncError as exc:
                message = str(exc)
            except Exception as exc:
                message = type(exc).__name__
            finally:
                with self.lock:
                    self.syncing.discard(device['id'])
            with self.connection() as db:
                db.execute('UPDATE badge_devices SET error=? WHERE id=?', (message, device['id']))
            return f'{device["number"]}：{message}' if message else ''

        with ThreadPoolExecutor(max_workers=min(4, len(devices))) as pool:
            errors.extend(message for message in pool.map(poll_device, devices) if message)
        self.error = '；'.join(errors)

    def sync_device(self, config, device, token):
        number, app = device['number'], config['app']
        now = datetime.now(CHINA).replace(tzinfo=None, microsecond=0)
        with self.connection() as db:
            row = db.execute('SELECT until,since FROM badge_sync_cursor WHERE app=? AND device=?', (app, number)).fetchone()
        # Revisit recent segments to pick up late device uploads. Catch up after downtime in daily windows.
        cursor = datetime.fromisoformat(row[0]) if row else now - timedelta(days=1)
        since = datetime.fromisoformat(row[1]) if row and row[1] else cursor
        # Devices may upload days after recording (e.g. after reconnecting to 4G).
        start = max(since, cursor - timedelta(days=7))
        end = min(now, cursor + timedelta(days=1))
        windows = [(start, end)]
        if end < now:
            # Process fresh recordings first when several days of history are outstanding.
            windows.insert(0, (max(end, now - timedelta(minutes=10)), now))
        items = []
        for beginning, finish in windows:
            data = vendor('/audio/list', dict(deviceNo=number, startDate=beginning.isoformat(), endDate=finish.isoformat()), token)
            if not isinstance(data, list):
                raise SyncError('录音列表格式异常')
            items.extend(data)
        errors = []
        for item in items:
            if self.stop.is_set():
                return
            name = item.get('filename') or item.get('fileName') or ''
            with self.connection() as db:
                seen = db.execute('SELECT 1 FROM badge_sync_files WHERE app=? AND device=? AND file=?',
                                  (app, number, name)).fetchone()
            if seen:
                continue
            try:
                self.fetch_file(config, device, item, name)
            except HTTPException as exc:
                errors.append(str(exc.detail))
            except SyncError as exc:
                errors.append(str(exc))
            except requests.RequestException as exc:
                errors.append(type(exc).__name__)
        if errors:
            raise SyncError(f'{len(errors)} 段尚未归档，{errors[0]}')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute('''SELECT 1 FROM badge_devices d JOIN badge_sync_config c ON c.app=d.app
                                   WHERE d.app=? AND d.number=? AND c.enabled AND d.enabled AND NOT d.retired''',
                                (app,number)).fetchone()
            if not active:
                return
            db.execute('UPDATE badge_devices SET synced=? WHERE app=? AND number=?',
                       (datetime.now(CHINA).isoformat(timespec='seconds'),app,number))
            db.execute('INSERT OR REPLACE INTO badge_sync_cursor(app,device,until,since) VALUES(?,?,?,?)',
                       (app, number, end.isoformat(), since.isoformat()))
        if end < now:
            self.wake.set()

    def fetch_file(self, config, device, item, name):
        url = item.get('url') or ''
        parts = urlsplit(url)
        if not name or Path(name).name != name or parts.scheme != 'https' or not parts.hostname or parts.username:
            raise SyncError('平台未提供有效的文件名或 HTTPS 下载地址')
        with requests.get(url, stream=True, timeout=(8, 45), allow_redirects=False) as response:
            if response.status_code != 200:
                raise SyncError(f'录音下载返回 HTTP {response.status_code}')
            def chunks():
                size = 0
                for chunk in response.iter_content(256 * 1024):
                    if self.stop.is_set():
                        raise SyncError('服务正停止，下次继续')
                    size += len(chunk)
                    yield chunk
                if item.get('size') and size != int(item['size']):
                    raise SyncError('下载文件大小与平台不符')
            self.ingest(chunks(), name, device, item, config)

    def router(self):
        routes = APIRouter(prefix='/api/badge/sync')

        @routes.get('')
        def status():
            return self.status()

        @routes.put('')
        def configure(body: Configuration):
            self.configure(body)
            return self.status()

        @routes.post('/run')
        def run():
            row = self.settings()
            if not row or not row['enabled']:
                raise HTTPException(409, '请先启用设备同步')
            self.wake.set()
            return self.status()

        @routes.post('/pause')
        def pause():
            with self.lock, self.connection() as db:
                db.execute('UPDATE badge_sync_config SET enabled=0 WHERE id=1')
            return self.status()

        return routes



def show_local_credentials():
    """Local interactive viewer, using the same Windows identity as the running app."""
    import os
    import sqlite3
    import tkinter as tk
    from tkinter import messagebox, ttk

    directory = Path(os.environ.get('HOTEL_VOICE_DATA', Path(os.environ['LOCALAPPDATA']) / 'HotelVoice'))
    window = tk.Tk()
    window.title('栖鉴 · 悦航益接口凭据')
    window.resizable(False, False)
    try:
        with sqlite3.connect((directory / 'records.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            row = db.execute('SELECT app,secret FROM badge_sync_config WHERE id=1').fetchone()
        if not row:
            raise ValueError('未配置')
        key = protect(row[1], decrypt=True).decode()
    except (ValueError, OSError, RuntimeError, sqlite3.Error):
        window.withdraw()
        messagebox.showerror('栖鉴', '当前 Windows 账户无法读取已配置的接口凭据。')
        window.destroy()
        return
    for index, (label, value) in enumerate([('AppId', row[0]), ('AppSecret', key)]):
        ttk.Label(window, text=label).grid(row=index, column=0, padx=10, pady=8)
        entry = ttk.Entry(window, width=54)
        entry.insert(0, value)
        entry.configure(state='readonly')
        entry.grid(row=index, column=1, padx=10, pady=8)
    ttk.Button(window, text='关闭', command=window.destroy).grid(row=2, column=1, sticky='e', padx=10, pady=8)
    window.mainloop()


if __name__ == '__main__':
    show_local_credentials()
