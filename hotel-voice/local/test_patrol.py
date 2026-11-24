"""Exercise 26 configured streams using local fakes; no camera network traffic."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest

from local.patrol import Camera, Patrol, protect


class PatrolTests(unittest.TestCase):
    def test_scheduler_capacity_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=1) as model:
            gate = threading.Event()
            lock = threading.Lock()
            active = set()
            peak = [0]
            captured = []
            def grab(url, sequence):
                with lock:
                    self.assertNotIn(url, active)
                    active.add(url)
                    peak[0] = max(peak[0], len(active))
                try:
                    gate.wait(4)
                    if 'offline' in url:
                        raise ConnectionError('offline')
                    return [(None, None)]
                finally:
                    with lock:
                        active.remove(url)
                        captured.append(url)
            def submit(camera, samples, captured, run_id):
                return run_id, model.submit(lambda: '未见异常')
            scheduler = Patrol(directory, submit, grab)
            cams = [Camera(name=f'点位{i}',url=f'rtsp://test:example@camera/{"offline" if i==0 else i}')
                    for i in range(26)]
            scheduler.add(cams)
            with self.assertRaises(Exception):
                scheduler.add([Camera(name='新点位',url='rtsp://x/new'),cams[0]])
            with scheduler.connection() as db:
                db.execute('UPDATE cameras SET due=0')
            scheduler.start()
            try:
                self.until(lambda: peak[0]==4)
                gate.set()
                self.until(lambda: sum(r['ended'] is not None for r in scheduler.overview()['history'])==26)
                result=scheduler.overview()
                self.assertEqual(len(result['cameras']),26)
                self.assertEqual(peak[0],4)
                self.assertEqual(len(set(captured)),26)
                self.assertEqual(len([r for r in result['history'] if r['job']]),25)
                self.assertNotIn('example',str(result))
                self.assertNotIn(b'rtsp://',Path(directory,'patrol.sqlite3').read_bytes())
                for c in result['cameras']:
                    self.assertGreater(c['due'],time.time())
                with scheduler.connection() as db:
                    db.execute('UPDATE cameras SET enabled=0,due=0')
                self.assertIsNone(scheduler.claim())
                # Simulate the next timer boundary for one enabled stream only.
                with scheduler.connection() as db:
                    db.execute("UPDATE cameras SET enabled=1 WHERE name='点位1'")
                scheduler.wake.set()
                self.until(lambda: len(captured)==27 and all(
                    c['phase']=='idle' for c in scheduler.overview()['cameras']))
                self.assertEqual(captured[-1], 'rtsp://test:example@camera/1')
            finally:
                gate.set()
                scheduler.stop()
            reopened=Patrol(directory,submit,grab)
            self.assertEqual(len(reopened.overview()['cameras']),26)
            self.assertIsNone(reopened.claim())

    def until(self, check):
        end=time.monotonic()+12
        while time.monotonic()<end:
            if check():
                return
            time.sleep(.02)
        self.fail('scheduler deadline exceeded')

    def test_interrupted_attempt_and_encrypted_url(self):
        value=b'rtsp://camera:secret@example.test/1'
        self.assertEqual(protect(protect(value),decrypt=True),value)
        with tempfile.TemporaryDirectory() as directory:
            scheduler=Patrol(directory,None)
            scheduler.add([Camera(name='楼层',url=value.decode())])
            camera,run_id=scheduler.claim()
            with scheduler.connection() as db:
                db.execute('UPDATE cameras SET enabled=0')
            scheduler.start()
            try:
                state=scheduler.overview()
                self.assertEqual(state['history'][0]['outcome'],'服务中断')
                self.assertEqual(state['cameras'][0]['phase'],'idle')
                self.assertEqual(state['cameras'][0]['enabled'],0)
            finally:
                scheduler.stop()

