"""Synthetic videos, landmarks and model observations; no hotel data or devices."""
from concurrent.futures import Future
from contextlib import closing, nullcontext
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from math import cos, radians, sin
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
from local.frontdesk_review import check_person, check_lower_body
from local.patrol import Camera, Patrol


def person(gesture='left', x=.5, *, cropped=False, yaw=0, hide_far_leg=False):
    def point(px, py):
        return SimpleNamespace(x=px, y=py, z=0, presence=1, visibility=1)
    points = [point(x,.23) for _ in range(33)]
    points[2], points[5] = point(x+.02,.21), point(x-.02,.21)
    points[11], points[12] = point(x+.08,.4), point(x-.08,.4)
    for i, spread, height in [(13,.1,.5),(23,.06,.62),(25,.06,.77),(27,.06,.9),(29,.06,.92),(31,.07,.95)]:
        points[i], points[i+1] = point(x+spread,height), point(x-spread,height)
    if gesture == 'upper_only':
        for i in range(23,33):
            points[i].visibility = .05
    if gesture == 'feet_hidden':
        points[31].visibility = .2
    if gesture == 'clipped':
        points[32].y = 1
    if gesture == 'side':
        points[23].z = .3
    points[15] = point(x+.12,.15 if gesture in ('both','left','upper_only') else .65)
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
    if yaw:
        for a,b in ((11,12),(23,24)):
            spread=(points[a].x-points[b].x)/2
            points[a].x=x+spread*cos(radians(yaw))
            points[b].x=x-spread*cos(radians(yaw))
            points[a].z=spread*sin(radians(yaw))
            points[b].z=-points[a].z
    if hide_far_leg:
        for i in (24,26,28,30,32):
            points[i].visibility=.12
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
    def test_live_gesture_uses_receiver_clock_across_pts_jump(self):
        wall = 1_800_000_000
        frames = [SimpleNamespace(time=t, to_image=lambda: Image.new('RGB',(160,120)))
                  for t in [0, None, 29440.5]+list(range(8))]
        container = SimpleNamespace(streams=SimpleNamespace(video=[SimpleNamespace(duration=None)]),
                                    duration=None, decode=lambda _:iter(frames))
        poses = iter([person()]*3+[person('down',yaw=a) for a in [90,90,180,180,-90,-90,0,0]])
        detector = SimpleNamespace(observe=lambda image, millis:[next(poses)], close=lambda:None)
        with (patch('local.frontdesk_pose.av.open', return_value=nullcontext(container)),
              patch('local.frontdesk_pose.time.monotonic', side_effect=[10+i/4 for i in range(len(frames))]),
              patch('local.frontdesk_pose.time.time', return_value=wall)):
            events = list(scan('fixture', stop=threading.Event(), detector_factory=lambda:detector))
        self.assertEqual([event.stamp for event in events],[wall+.5])

    def test_repair_report_index_and_attempt_idempotently(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, HOTEL_VOICE_DATA=directory):
            from local import vision_api
            store = Path(directory)
            patrol = Patrol(store,None)
            job = 'a'*32
            folder = store/job
            folder.mkdir()
            evidence = folder/'gesture-0-0.jpg'
            evidence.touch()
            saved = evidence.stat().st_mtime
            wrong = saved + 29440
            report = dict(id=job,filename='测试前台 · 举手仪容检查 21:20:37',cameraId='camera',
                          created=datetime.fromtimestamp(wrong,timezone.utc).isoformat(),scene='前台接待',
                          state='已完成',review='已确认',kind='举手仪容检查',captureEvent={'person':1})
            with (patch.object(vision_api,'STORE',store),patch.object(vision_api,'INDEX',store/'index.sqlite3')):
                with vision_api.report_index() as db:
                    db.execute('CREATE TABLE jobs(id PRIMARY KEY,filename,created,scene,state,review)')
                vision_api.save(folder,report)
                with patrol.connection() as db:
                    db.execute('INSERT INTO attempts(id,job,camera,name,started,ended) VALUES(?,?,?,?,?,?)',
                               (job,job,'camera','测试前台',wrong,saved+1))
                for _ in range(2):
                    vision_api.repair_gesture_times(patrol)
                    fixed = vision_api.read(folder)
                    self.assertEqual(fixed['review'],'已确认')
                    self.assertEqual(fixed['timeCorrection']['originalCreated'],report['created'])
                    self.assertAlmostEqual(datetime.fromisoformat(fixed['created']).timestamp(),saved,places=5)
                    self.assertTrue(fixed['filename'].endswith(datetime.fromtimestamp(saved).strftime('%H:%M:%S')))
                    self.assertEqual(vision_api.jobs()[0]['created'],fixed['created'])
                    self.assertAlmostEqual(patrol.overview()['history'][0]['started'],saved,places=5)

    def test_half_body_with_lowered_hand_out_of_view(self):
        self.assertEqual(person(cropped=True).gesture,'unknown')
        self.assertEqual(person('down',cropped=True).gesture,'unknown')
        for pose in ('feet_hidden','clipped','side'):
            self.assertEqual(person(pose).gesture,'unknown')
        self.assertEqual(person('back',cropped=True).gesture,'unknown')
        self.assertEqual(person('hidden').gesture,'unknown')

    def test_full_video_and_rearm(self):
        class FakeDetector:
            def observe(self, image, millis):
                start=millis-2000 if millis<5500 else millis-5500
                if not 0 <= start < 2500:
                    return [person('down')]
                if start < 750:
                    return [person('left' if millis<5500 else 'right')]
                angle=[90,180,-90,0][(start-750)//500]
                return [person('down',yaw=angle if millis<5500 else -angle)]
            def close(self):
                pass
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp,'synthetic.mp4')
            test_video(path)
            events = list(scan(path, detector_factory=FakeDetector))
        self.assertEqual([(e.person,e.stamp) for e in events], [(1,2.5),(1,6)])
        for event,first,second in ((events[0],'右侧','左侧'),(events[1],'左侧','右侧')):
            roles=[f[0] for f in event.frames]
            self.assertEqual([r for r in roles if r.endswith('全身')],[first+'全身','背面全身',second+'全身','正面全身'])
            self.assertIn('背面头部',roles)
            self.assertIn('左侧鞋履',roles)
            self.assertIn('右侧鞋履',roles)

    def test_incomplete_turns_are_discarded(self):
        image=Image.new('RGB',(320,240))
        start=[[person()]]*3
        def poses(angles):
            return [[person('down',yaw=a)] for a in angles for _ in range(2)]
        rejected=[
            start,
            start+poses([90,180,90,0,-90,0]),
            start+poses([90,180])+[[]]+poses([-90,0]),
            start+poses([90])+[[person('down',x=.48),person('down',x=.52)]]+poses([180,-90,0]),
            start+poses([90])*25+poses([180,-90,0]),
            start+poses([90])+[[person('clipped')]]+poses([180,-90,0]),
            [[person(cropped=True)]]*3+poses([90,180,-90,0]),
        ]
        for k,sequence in enumerate(rejected):
            tracker=Gestures()
            events=[e for i,obs in enumerate(sequence) for e in tracker.step(i/4,image,obs)]
            self.assertEqual(events,[],k)
        self.assertEqual(person('down',yaw=90,hide_far_leg=True).view,'右侧')

    def test_waist_up_raise_cannot_start_or_finish_an_inspection(self):
        image=Image.new('RGB',(320,240))
        hidden=person('upper_only')
        self.assertEqual((hidden.gesture,hidden.view),('unknown','clipped'))
        rotate=[person('down',yaw=a) for a in (90,180,-90,0) for _ in range(2)]
        for sequence in ([hidden]*5+rotate, [person()]*3+rotate[:2]+[hidden]+rotate[2:]):
            tracker=Gestures()
            self.assertFalse([event for i,obs in enumerate(sequence)
                              for event in tracker.step(i/4,image,[obs])])
            self.assertTrue(all(track.pending is None for track in tracker.tracks.values()))

    def test_complete_turn_evidence_routed_on_first_check_and_retest(self):
        from local import vision_api as api
        image=Image.new('RGB',(480,720),'navy')
        tracker=Gestures()
        sequence=[person()]*3+[person('down',yaw=angle) for angle in (90,180,-90,0) for _ in range(2)]
        event,=[e for i,obs in enumerate(sequence) for e in tracker.step(i/4,image,[obs])]
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            stored=api.store_event(folder,event,'sample',live=True)
            report=dict(state='排队中',scene='前台接待',standard='发网',checks=['仪容仪表'],hairStyle='收腰西装',
                        tieStyle='藏蓝西装',gestureVersion=2,captureEvent=stored,groups=[])
            with (patch.object(api,'ensure_model'), patch.object(api,'save'),patch.object(api,'SLOTS'),
                  patch.object(api,'check_person',return_value=('扎发款',[])) as upper,
                  patch.object(api,'check_lower_body',return_value={'status':'发现异常','level':'取证缺失'}) as lower):
                api.run(folder,report,False)
                api.retest_saved(folder,report,{})
            self.assertEqual(report['state'],'已完成')
            self.assertEqual(report['gestureCount'],1)
            self.assertEqual(len(report['groups']),1)
            self.assertEqual(upper.call_count,2)
            for call in upper.call_args_list:
                self.assertEqual(len(call.args[0]),2)
                self.assertEqual(len(call.args[-1]),3)
            for call in lower.call_args_list:
                if call.args[1]=='皮鞋':
                    self.assertEqual(call.args[-1],['鞋履细节','右侧鞋履','背面鞋履','左侧鞋履','正面鞋履'])
            self.assertTrue(all((folder/f['file']).is_file() for f in stored['frames']))

    def test_separate_people_and_negative_poses(self):
        image = Image.new('RGB',(500,300))
        tracker = Gestures()
        for i in range(9):
            self.assertEqual(tracker.step(i/4,image,[person(g,x) for g,x in
                zip(('back','both','hidden','down'),(.15,.4,.65,.85))]), [])
        events = []
        for i in range(9,25):
            angle=([0]*3+[90]*2+[180]*2+[-90]*2+[0]*7)[i-9]
            observations = [person('left',.15,yaw=angle),person('right',.65,yaw=-angle)]
            events.extend(tracker.step(i/4,image,observations if i%2 else observations[::-1]))
        self.assertEqual(len(events),2)
        self.assertEqual(len({e.person for e in events}),2)

    def test_missing_frames_do_not_confirm_a_hold(self):
        tracker = Gestures()
        image = Image.new('RGB',(100,100))
        for t in (0, .6, 1.2):
            self.assertFalse(tracker.step(t,image,[person()]))
        self.assertTrue(all(t.pending is None for t in tracker.tracks.values()))

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
                with (patch('local.frontdesk_review.ask_vision', side_effect=replies),
                      patch('local.frontdesk_review.visible_badge_area',return_value=True)):
                    style, rows = check_person([path],tie,hair,'')
                return {row['name']:row for row in rows}
            tie = results('BACB')
            self.assertEqual(tie['领带']['status'],'发现异常')
            self.assertEqual((tie['胸牌']['status'],tie['胸牌']['level']),('发现异常','取证缺失'))
            self.assertNotIn('扎发', tie)
            self.assertEqual(results('BC','','')['工服']['status'],'未见异常')
            self.assertEqual(results('CC','','')['领带或扎发']['level'],'取证缺失')
            self.assertIn('短发免扎',results('BBABB')['扎发']['detail'])
            hair = results('BBBC')
            self.assertEqual(hair['胸牌']['status'],'发现异常')
            self.assertEqual(hair['扎发']['status'],'发现异常')
            self.assertEqual(results('AC')['工服']['status'],'发现异常')

    def test_blazer_passes_independently_of_undershirt_and_style(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp,'crop.jpg')
            image = Image.new('RGB',(120,160),'white')
            image.paste('navy',(20,20,100,140))
            image.save(path)
            # Legacy white-shirt descriptions must not introduce an inner-layer check.
            with (patch('local.frontdesk_review.visible_badge_area',return_value=False),
                  patch('local.frontdesk_review.choose',side_effect=list('BBBBD'))):
                style,rows = check_person([path],'藏蓝西装、白衬衫','黑色收腰西装、白衬衫','发网盘起',['full'])
            self.assertEqual(style,'扎发款')
            self.assertEqual([r['name'] for r in rows],['工服','胸牌','扎发'])
            self.assertEqual(rows[0]['status'],'未见异常')
            self.assertEqual(rows[-1]['status'],'未见异常')
            self.assertIn('未显示发网',rows[-1]['detail'])
            with (patch('local.frontdesk_review.visible_badge_area',return_value=False),
                  patch('local.frontdesk_review.choose',side_effect=list('BBCB'))):
                style,rows = check_person([path],'藏蓝西装、白衬衫','黑色收腰西装、白衬衫','发网盘起',['full'])
            self.assertEqual(style,'款式不明')
            self.assertEqual(rows[0]['status'],'未见异常')

    def test_pants_compare_only_configured_lower_garment(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp,'legs.jpg')
            image=Image.new('RGB',(90,130),'white')
            image.paste('navy',(20,0,70,120))
            image.save(path)
            with patch('local.frontdesk_review.choose',return_value='A') as model:
                row=check_lower_body([path],'下装','黑色收腰西装、白衬衫、配套深色西裤。发网盘起。',['full'])
            self.assertEqual(row['status'],'未见异常')
            pictures,prompt,_,_=model.call_args.args
            self.assertIn('full',pictures)
            self.assertIn('深色西裤',prompt)
            self.assertNotIn('发网',prompt)
            self.assertEqual(check_lower_body([path],'下装','白衬衫')['level'],'标准缺失')

    def test_partial_shoe_is_assessed_and_missing_capture_is_identified(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp,'toe.jpg')
            image = Image.new('RGB',(72,28),'navy')
            image.paste('silver',(24,20,50,28))
            image.save(path)
            for choice,expected in [('A','未见异常'),('B','发现异常'),('C','发现异常'),('D','发现异常'),('E','发现异常')]:
                with patch('local.frontdesk_review.choose',return_value=choice) as model:
                    row=check_lower_body([path],'皮鞋','',roles=['左侧鞋履'])
                self.assertEqual(row['status'],expected)
                self.assertEqual(row['level']=='取证缺失',choice=='E')
                self.assertEqual(model.call_count,1)
                self.assertIn('左侧鞋履',model.call_args.args[1])
            with patch('local.frontdesk_review.choose',return_value='A') as model:
                self.assertEqual(check_lower_body([],'皮鞋','',['full'])['status'],'未见异常')
                self.assertEqual(model.call_args.args[0],['full'])
            with patch('local.frontdesk_review.choose') as model:
                missing=check_lower_body([],'皮鞋','')
                self.assertEqual((missing['status'],missing['level']),('发现异常','取证缺失'))
                model.assert_not_called()

    def test_legacy_overview_uses_only_a_uniquely_matched_target(self):
        from local.frontdesk_review import archived_lower_views
        with tempfile.TemporaryDirectory() as temp:
            scene,upper=(Path(temp)/name for name in ('scene.jpg','upper.jpg'))
            Image.new('RGB',(320,300),'navy').save(scene)
            Image.new('RGB',(80,100),'navy').save(upper)
            obs=person()
            with (patch('local.frontdesk_pose.pose_ready',return_value=True),
                  patch('local.frontdesk_pose.Detector') as detector):
                detector.return_value.observe.return_value=[obs]
                self.assertIn('皮鞋',archived_lower_views(scene,upper))
                detector.return_value.observe.return_value=[obs,obs]
                self.assertEqual(archived_lower_views(scene,upper),{})
                Image.new('RGB',(80,100),'white').save(upper)
                detector.return_value.observe.return_value=[obs]
                self.assertEqual(archived_lower_views(scene,upper),{})

    def test_jacket_and_obscured_badge(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp,'jacket.jpg')
            image = Image.new('RGB',(100,150),'white')
            image.paste('blue',(10,30,90,130))
            image.save(path)
            with (patch('local.frontdesk_review.visible_badge_area',return_value=False),
                  patch('local.frontdesk_review.choose',side_effect=['A','B'])):
                _, rows=check_person([path],'深蓝西装白衬衫','黑色西装白衬衫','')
            self.assertEqual([x['status'] for x in rows[:2]],['发现异常','发现异常'])
            self.assertEqual([x['level'] for x in rows[:2]],['需要复核','取证缺失'])

    def test_retest_replaces_results_and_preserves_review_on_failure(self):
        from local import vision_api as api
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/('a'*32)
            folder.mkdir()
            with (patch.object(api,'STORE',folder.parent), patch.object(api,'INDEX',folder.parent/'index.sqlite'),
                  patch.object(api,'ensure_model'), patch.object(api.POOL,'submit') as submit):
                with api.report_index() as db:
                    db.execute('CREATE TABLE jobs(id PRIMARY KEY, filename,created,scene,state,review)')
                group={'frames':[{'file':'sample.jpg','seconds':None}],'findings':[]}
                report=dict(id=folder.name,filename='测试',created='2026-01-01',scene='前台接待',state='已完成',
                            review='已确认',reviewNote='现场复核',groups=[group],checks=['地面垃圾'],standard='测试要求')
                api.save(folder,report)
                for fail in (False,True):
                    api.retest(folder.name,None)
                    with self.assertRaises(api.HTTPException) as ctx:
                        api.retest(folder.name,None)
                    self.assertEqual(ctx.exception.status_code,409)
                    fn,*args=submit.call_args.args
                    output=[{'name':'地面垃圾','status':'未见异常'}]
                    with patch.object(api,'inspect_group',return_value=output,side_effect=ValueError('测试中断') if fail else None) as model:
                        if fail:
                            with self.assertLogs('local.vision_api',level='ERROR'):
                                fn(*args)
                        else:
                            fn(*args)
                    model.assert_called_once_with(['地面垃圾'],[folder/'sample.jpg'],[None],'测试要求')
                    saved=api.read(folder)
                    self.assertEqual(saved['groups'][0]['findings'],output)
                    self.assertEqual((saved['reviewNote'],saved['state']),('现场复核','已完成'))
                    self.assertEqual(bool(saved['retestError']),fail)

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
