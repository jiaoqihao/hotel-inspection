"""Isolated device registry, migration, vendor presence and permissions tests."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from contextlib import contextmanager
from local.badge_devices import cached_state, CHINA
from local.badge_archive import migrate
from local.badge_sync import BadgeSync, Configuration
from local.access import allowed


class DeviceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)/'records.sqlite3'
        with self.connection() as db:
            db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,data TEXT NOT NULL)')
            migrate(db)
        self.sync = BadgeSync(self.connection,None,None)
        self.registry = self.sync.directory
        app = FastAPI()
        app.include_router(self.registry.router())
        app.include_router(self.sync.router())
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        with patch('local.badge_sync.vendor', side_effect=[{'accessToken':'fixture'}, {'items':[]}]):
            self.sync.configure(Configuration(appId='test',secret='synthetic'))
        self.path_api='/api/badge/devices'

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path,timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self,*devices):
        return self.client.post(self.path_api,json={'app':'test','devices':devices})

    def test_rejected_replacement_preserves_connection(self):
        from local.badge_sync import SyncError
        self.add(dict(number='TEST-1',source='胸牌')).raise_for_status()
        before = dict(self.sync.settings())
        for replies in ([SyncError('令牌被拒绝')], [{'accessToken':'fixture'}, SyncError('设备清单被拒绝')]):
            with patch('local.badge_sync.vendor', side_effect=replies):
                result = self.client.put('/api/badge/sync',json={'appId':'test','secret':'rejected'})
            self.assertEqual(result.status_code,400)
            self.assertIn('本次配置未保存',result.json()['detail'])
            self.assertEqual(dict(self.sync.settings()),before)

    def change(self,d,**fields):
        body=dict(d,test=bool(d['test']),enabled=bool(d['enabled']),retired=bool(d['retired']))
        return self.client.patch(self.path_api+'/'+str(d['id']),json={**body,**fields})

    def test_individual_edits_replacement_legacy_write_and_restore(self):
        fixtures=[dict(number='TEST-'+str(i),source='挂牌' if i<2 else '胸牌') for i in range(5)]
        items=self.add(*fixtures).json()['devices']
        self.assertEqual(len(items),5)
        self.assertEqual(len(self.add(*fixtures).json()['devices']),5)
        edited=self.change(items[0],name='合成设备',position='前台',test=False,enabled=False).json()['devices']
        self.assertEqual(edited[1:],items[1:])
        self.assertEqual(self.change(items[0],name='旧页面').status_code,409)
        self.sync.configure(Configuration(appId='test',devices=fixtures))
        self.assertEqual(self.registry.rows()['devices'][0],edited[0])
        self.assertEqual(self.change(edited[0],replacement=fixtures[1]).status_code,409)
        self.change(edited[0],replacement={'number':'TEST-NEW','source':'挂牌'}).raise_for_status()
        rows=self.registry.rows()['devices']
        old=next(r for r in rows if r['id']==items[0]['id'])
        self.assertTrue(old['retired'])
        self.assertEqual(len(rows),6)
        self.change(old,retired=False).raise_for_status()
        with self.connection() as db:
            devices=json.loads(db.execute('SELECT devices FROM badge_sync_config').fetchone()[0])
            self.assertEqual(len(devices),6)
        self.assertFalse(allowed({'role':'质检员'},self.path_api,'PATCH'))
        self.assertTrue(allowed({'role':'质检员'},self.path_api+'/presence','GET'))
        self.assertFalse(allowed({'role':'前台'},self.path_api,'GET'))
        self.assertFalse(allowed({'role':'经理'},self.path_api+'/catalog','GET'))

    def test_v3_migration_preserves_history_and_cursor(self):
        with self.connection() as db:
            db.execute('DROP TABLE badge_devices')
            db.execute('PRAGMA user_version=3')
            db.execute('UPDATE badge_sync_config SET devices=?', (json.dumps([dict(number='QA',source='挂牌')]),))
            db.execute("INSERT INTO badge_sync_cursor VALUES('test','QA','2026-11-24','2026-11-20')")
            db.execute("INSERT INTO badge_sync_files VALUES('test','QA','test.wav','R')")
            db.execute('INSERT INTO records VALUES(?,?)',('R',json.dumps(dict(badge='QA',source='挂牌',test=True))))
            db.execute('INSERT INTO records VALUES(?,?)',('old',json.dumps(dict(filename='历史录音'))))
            db.commit()
            migrate(db)
            migrate(db)
            unit=db.execute('SELECT id,test FROM badge_devices').fetchone()
            r=json.loads(db.execute("SELECT data FROM records WHERE id='R'").fetchone()[0])
            self.assertEqual(r['deviceId'],unit['id'])
            self.assertFalse(unit['test'])
            self.assertTrue(r['wasMarkedTest'])
            self.assertFalse(r['test'])
            self.assertEqual(db.execute('SELECT until FROM badge_sync_cursor').fetchone()[0],'2026-11-24')
            self.assertEqual(db.execute('SELECT count(*) FROM badge_sync_files').fetchone()[0],1)
            self.assertNotIn('deviceId',json.loads(db.execute("SELECT data FROM records WHERE id='old'").fetchone()[0]))

    def test_vendor_pagination_cache_failure_and_heartbeat(self):
        self.add(dict(number='A',source='挂牌'),dict(number='B',source='胸牌')).raise_for_status()
        now=datetime.now(CHINA)
        def page(path,body,token):
            self.assertEqual(path,'/Device/Page')
            first=body['page']==1
            return {'items':[dict(deviceNo='A' if first else 'B',deviceType='Hanging4G' if first else 'Bg4g',
                status='Recording' if first else 'Offline',lastHeartbeatTime=now.isoformat(),heartbeatInterval=60,
                remainPower=85)],'hasNextPages':first,'totalPages':2}
        with patch('local.badge_sync.access_token',return_value='fake'),patch('local.badge_sync.vendor',side_effect=page) as call:
            with ThreadPoolExecutor(2) as pool:
                responses=list(pool.map(lambda _:self.registry.presence(),range(2)))
            self.assertEqual(call.call_count,2)
            self.assertEqual(responses[0],responses[1])
            self.assertEqual([s['group'] for s in responses[0]['states'].values()],['在线','离线'])
            self.assertEqual(self.registry.items['A']['battery'],85)
            with patch.object(self.registry,'fetch',side_effect=TimeoutError):
                self.registry.attempt=0
                self.registry.updated-=timedelta(seconds=91)
                failed=self.registry.presence()
                self.assertTrue(failed['error'])
                self.assertEqual({s['detail'] for s in failed['states'].values()},{'状态已过期'})
        r=self.registry.items['A']
        for status in ('Recording','Uploading','Upgrading','Online'):
            self.assertEqual(cached_state({**r,'status':status},now,now)[0],'在线')
        self.assertEqual(cached_state({**r,'heartbeat':(now-timedelta(seconds=182)).isoformat()},now,now)[1],'心跳延迟')
        self.assertEqual(cached_state(None,now,now)[0],'待确认')

    def test_per_device_cursor_and_download_failure_isolation(self):
        items=self.add(*[dict(number='TEST'+str(i),source='挂牌',test=i!=1) for i in range(3)]).json()['devices']
        self.change(items[2],enabled=False).raise_for_status()
        calls=[]
        def visit(config,d,token):
            calls.append((d['number'],bool(d['test'])))
            if d['number']=='TEST0':
                raise TimeoutError()
        catalog={n:dict(source='挂牌',status=status,heartbeat=None,interval=None,battery=None)
                 for n,status in [('TEST0','Online'),('TEST1','Recording'),('TEST2','Online'),('DISCOVERED','Offline')]}
        with patch.object(self.registry,'fetch',return_value=catalog), patch('local.badge_sync.access_token',return_value='fake'),patch.object(self.sync,'sync_device',side_effect=visit):
            self.sync.sync(self.sync.settings())
            self.sync.sync(self.sync.settings())
        self.assertCountEqual(calls,[('TEST0',False),('TEST1',False)]*2)
        self.assertEqual([d['number'] for d in self.registry.rows()['devices']].count('DISCOVERED'),1)
        self.assertIn('TimeoutError',self.registry.rows()['devices'][0]['error'])
