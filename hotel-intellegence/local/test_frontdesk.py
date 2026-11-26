"""Synthetic videos, landmarks and model observations; no hotel data or devices."""
from concurrent.futures import Future
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import av
from PIL import Image

from local.frontdesk_pose import Event, Gestures, observation, scan
from local.frontdesk_review import check_person
from local.patrol import Camera, Patrol


def person(gesture='left', x=.5, *, cropped=False):
    def point(px, py):
        return SimpleNamespace(x=px, y=py, z=0, presence=1, visibility=1)
    points = [point(x,.23) for _ in range(33)]
    points[2], points[5] = point(x+.02,.21), point(x-.02,.21)
    points[11], points[12] = point(x+.08,.4), point(x-.08,.4)
    points[15] = point(x+.12,.15 if gesture in ('both','left') else .65)
    points[16] = point(x-.12,.15 if gesture in ('both','right') else .65)
    if gesture == 'back':
        points[11], points[12] = points[12], points[11]
    if gesture == 'hidden':
        points[16].visibility = .1
    if cropped:
        points[2].y = points[5].y = -.02
        points[9], points[10] = point(x+.02,.28), point(x-.02,.28)
        points[14], points[16] = point(x-.09,.85), point(x-.09,1.1)
        points[16].presence = .1
    return observation(points)


def test_video(path):
    with av.open(str(path), 'w') as output:
        stream = output.add_stream('mpeg4', rate=8)
        stream.width, stream.height, stream.pix_fmt = 160, 120, 'yuv420p'
        for _ in range(80):
            frame = av.VideoFrame.from_image(Image.new('RGB',(160,120),'gray'))
            output.mux(stream.encode(frame))
        output.mux(stream.encode())


class FrontDeskTests(unittest.TestCase):
    def test_half_body_with_lowered_hand_out_of_view(self):
        self.assertEqual(person(cropped=True).gesture,'left')
        self.assertEqual(person('down',cropped=True).gesture,'down')
        self.assertEqual(person('back',cropped=True).gesture,'unknown')
        self.assertEqual(person('hidden').gesture,'unknown')

    def test_full_video_and_rearm(self):
        class FakeDetector:
            def observe(self, image, millis):
                g = ('left' if 2000 <= millis < 4000 else 'right' if 5500 <= millis < 8000 else 'down')
                return [person(g)]
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp,'synthetic.mp4')
            test_video(path)
            events = list(scan(path, detector_factory=FakeDetector))
        self.assertEqual([(e.person,e.stamp) for e in events], [(1,2.5),(1,6)])
        self.assertEqual(events[0].frames[-1][0],'放手后细节')

    def test_separate_people_and_negative_poses(self):
        image = Image.new('RGB',(500,300))
        tracker = Gestures()
        for i in range(9):
            self.assertEqual(tracker.step(i/4,image,[person(g,x) for g,x in
                zip(('back','both','hidden','down'),(.15,.4,.65,.85))]), [])
        events = []
        for i in range(9,25):
            observations = [person('left',.15),person('right',.65)]
            events.extend(tracker.step(i/4,image,observations if i%2 else observations[::-1]))
        self.assertEqual(len(events),2)
        self.assertEqual(len({e.person for e in events}),2)
        self.assertEqual(tracker.finish(),[])

    def test_missing_frames_do_not_confirm_a_hold(self):
        tracker = Gestures()
        image = Image.new('RGB',(100,100))
        for t in (0, .6, 1.2):
            self.assertFalse(tracker.step(t,image,[person()]))
        self.assertFalse(tracker.finish())

    def test_empty_and_interrupted_scans_have_distinct_reports(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HOTEL_VOICE_DATA=directory):
            from local import vision_api
            folder = Path(directory)
            report = dict(id='test', scene='前台接待', extension='.mp4', checks=['仪容仪表'],
                          gestureVersion=1, interval=5, groups=[])
            with (patch.object(vision_api,'save'), patch.object(vision_api,'ensure_model') as model,
                  patch.object(vision_api,'scan', return_value=[])):
                self.assertEqual(vision_api.run(folder,report,False),'已完成')
                self.assertEqual(report['gestureCount'],0)
                model.assert_not_called()
            report.pop('gestureCount')
            with (patch.object(vision_api,'save'), patch.object(vision_api,'scan',side_effect=ValueError('视频提前结束')),
                  self.assertLogs('local.vision_api',level='ERROR')):
                self.assertEqual(vision_api.run(folder,report,False),'失败')
                self.assertNotIn('gestureCount',report)

    def test_style_and_visibility(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp,'crop.jpg')
            image = Image.new('RGB',(120,160),'white')
            image.paste('navy',(20,20,100,140))
            image.save(path)
            def results(choices, tie='藏青尖领', hair='红色立领'):
                replies = [{'choice':letter} for letter in choices]
                with patch('local.frontdesk_review.ask_vision', side_effect=replies):
                    style, rows = check_person([path],tie,hair,'')
                return {row['name']:row for row in rows}
            tie = results('ACB')
            self.assertEqual(tie['领带']['status'],'发现异常')
            self.assertEqual(tie['胸牌']['status'],'无法判断')
            self.assertNotIn('扎发', tie)
            self.assertEqual(results('C','','')['工服']['status'],'无法判断')
            self.assertEqual(results('C','','')['领带']['status'],'无法判断')
            self.assertIn('短发免扎',results('BABB')['扎发']['detail'])
            hair = results('BBC')
            self.assertEqual(hair['胸牌']['status'],'发现异常')
            self.assertEqual(hair['扎发']['status'],'发现异常')
            self.assertEqual(results('CC')['工服']['status'],'发现异常')

    def test_migrate_existing_camera(self):
        with tempfile.TemporaryDirectory() as temp:
            with closing(sqlite3.connect(Path(temp,'patrol.sqlite3'))) as db, db:
                db.execute("""CREATE TABLE cameras(id TEXT PRIMARY KEY,name,secret,fingerprint,minutes,
                    scene,standard,enabled INTEGER,due REAL,phase,started,ended,outcome,last_job,active_job)""")
                db.execute("""INSERT INTO cameras(id,name,scene,enabled,due,minutes,phase,last_job)
                    VALUES('old','测试旧前台','前台接待',0,0,10,'idle','old-link')""")
            for _ in range(2):
                row = Patrol(temp,None).overview()['cameras'][0]
                self.assertEqual((row['tieStyle'],row['hairStyle'],row['last_job']),('','','old-link'))

    def test_camera_listener_lifecycle(self):
        started = threading.Event()
        disconnected = threading.Event()
        accepted = []
        def stream(url, *, stop, connected):
            connected()
            started.set()
            yield Event(1,time.time(),[])
            stop.wait(6)
            disconnected.set()
        def submit(camera,event):
            accepted.append(camera['tieStyle'])
            future = Future()
            future.set_result('未见异常')
            return 'synthetic-job',future
        with tempfile.TemporaryDirectory() as temp, patch('local.frontdesk_watch.scan',stream):
            service = Patrol(temp,None,submit_event=submit)
            service.add([Camera(name='测试前台',url='rtsp://test.invalid/fixture',scene='前台接待',tieStyle='深色西装')])
            with service.connection() as db:
                db.execute('UPDATE cameras SET due=?',(time.time()+3600,))
            service.start()
            try:
                self.assertTrue(started.wait(5))
                for _ in range(100):
                    state=service.overview()
                    if state['history'] and state['history'][0]['ended']:
                        break
                    time.sleep(.02)
                self.assertEqual(accepted,['深色西装'])
                self.assertEqual(state['cameras'][0]['gesture_job'],'synthetic-job')
                self.assertEqual(state['cameras'][0]['gestureStatus'],'举手监听中')
                with service.connection() as db:
                    db.execute('UPDATE cameras SET enabled=0')
                service.watchers.wake.set()
                self.assertTrue(disconnected.wait(4))
            finally:
                service.stop()
