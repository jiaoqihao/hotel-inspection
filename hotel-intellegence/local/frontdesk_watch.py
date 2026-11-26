"""Reconcile independent gesture listeners against enabled front-desk cameras."""
import logging
import threading
import shutil
import time

import av

from local.frontdesk_pose import SCENE, scan


class Watchers:
    def __init__(self, patrol, submit):
        self.patrol = patrol
        self.submit = submit
        self.listeners = {}
        self.status = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.thread = threading.Thread(target=self.loop, name='gesture-supervisor', daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.wake.set()
        self.thread.join()

    def snapshot(self):
        with self.lock:
            return dict(self.status)

    def show(self, key, message):
        with self.lock:
            self.status[key] = message

    def listen(self, camera, stop):
        from local.patrol import MIN_FREE, protect
        key = camera['id']
        while not stop.is_set():
            self.show(key, '正在连接举手监听')
            try:
                url = protect(camera['secret'], decrypt=True).decode()
                last_action = ''
                def progress(stamp, message):
                    nonlocal last_action
                    clock = time.strftime('%H:%M:%S', time.localtime(stamp))
                    if message:
                        last_action = f' · {clock} {message}'
                    self.show(key, f'举手监听中 · 最近分析帧 {clock}{last_action}')

                with av.logging.Capture(local=True):
                    for event in scan(url, stop=stop, connected=lambda: self.show(key, '举手监听中'),
                                      progress=progress):
                        if stop.is_set():
                            return
                        with self.patrol.connection() as db:
                            current = db.execute('SELECT id,name,secret,standard,tieStyle,hairStyle FROM cameras '
                                                 'WHERE id=? AND enabled=1 AND scene=?', (key,SCENE)).fetchone()
                        if current is None or dict(current) != camera:
                            return
                        if shutil.disk_usage(self.patrol.root).free < MIN_FREE:
                            raise ValueError('剩余磁盘不足 2 GB，举手监听暂停')
                        job, future = self.submit(camera, event)
                        with self.patrol.connection() as db:
                            db.execute('UPDATE cameras SET gesture_job=? WHERE id=?', (job, key))
                            db.execute('INSERT INTO attempts(id,camera,name,started,outcome,job) VALUES(?,?,?,?,?,?)',
                                       (job,key,camera['name'],event.stamp,'举手仪容检查排队中',job))
                        progress(time.time(), '巡检记录已写入')
                        future.add_done_callback(lambda result, job=job: self.finished(job, result))
                if stop.is_set():
                    return
                message = '视频流已结束，等待重连'
            except ValueError as exc:
                # Stream and decoder errors can include the authenticated URI.
                message = str(exc) if str(exc).startswith(('姿态模型','姿态运行库未就绪','剩余磁盘不足')) else '举手监听中断，等待重连'
            except Exception:
                message = '举手监听中断，等待重连'
            self.show(key, message)
            stop.wait(5)

    def finished(self, job, future):
        try:
            outcome = future.result()
        except Exception:
            outcome = '举手仪容检查失败'
        with self.patrol.connection() as db:
            db.execute('UPDATE attempts SET ended=?,outcome=? WHERE id=?', (time.time(),outcome,job))

    def reconcile(self):
        with self.patrol.connection() as db:
            rows = db.execute('SELECT id,name,secret,standard,tieStyle,hairStyle FROM cameras WHERE enabled=1 AND scene=?',
                              (SCENE,)).fetchall()
        wanted = {row['id']: dict(row) for row in rows}
        for key, (camera, stop, thread) in list(self.listeners.items()):
            if wanted.get(key) != camera or not thread.is_alive():
                stop.set()
                if thread.is_alive():
                    continue
                del self.listeners[key]
                with self.lock:
                    self.status.pop(key, None)
        for key, camera in wanted.items():
            if key in self.listeners:
                continue
            stop = threading.Event()
            thread = threading.Thread(target=self.listen, args=(camera,stop), name='gesture-camera', daemon=True)
            self.listeners[key] = (camera,stop,thread)
            thread.start()

    def loop(self):
        try:
            while not self.patrol.stop_event.is_set():
                try:
                    self.reconcile()
                except Exception:
                    logging.getLogger(__name__).exception('举手监听调度失败')
                self.wake.wait(1)
                self.wake.clear()
        finally:
            for _, stop, _ in self.listeners.values():
                stop.set()
            for _, _, thread in self.listeners.values():
                thread.join()
