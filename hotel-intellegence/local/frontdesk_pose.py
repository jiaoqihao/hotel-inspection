"""Local front-desk gesture detection, shared by RTSP and uploaded video."""
from dataclasses import dataclass
from math import hypot
import time

import av
from PIL import ImageDraw

from local.setup_vision import POSE_FILE, ROOT
from local.vision_model import thumbnail

SCENE = '前台接待'
CHECK = '仪容仪表'
NO_EVENT = '未检测到符合条件的举手'
SAMPLE_INTERVAL = .25


@dataclass
class Observation:
    center: tuple
    width: float
    shoulder_delta: tuple
    nose_y: float
    gesture: str


def observation(points):
    def visible(index):
        p = points[index]
        return (p.visibility >= .6 and p.presence >= .6 and
                0 <= p.x <= 1 and 0 <= p.y <= 1)

    if not all(visible(i) for i in (11, 12)):
        return None
    left, right = points[11], points[12]
    width = hypot(left.x - right.x, left.y - right.y)
    if width < .015:
        return None
    cx, cy = (left.x + right.x) / 2, (left.y + right.y) / 2
    nose = points[0]
    # At a laptop camera the eyes can leave the top of the image before the nose and mouth.
    face_pair = any(visible(a) and visible(b) and points[a].x > points[b].x and
                    points[b].x - .025 * width <= nose.x <= points[a].x + .025 * width
                    for a, b in ((2, 5), (9, 10)))
    front = (visible(0) and face_pair and left.x > right.x and
             abs(nose.x - cx) < .52 * width and abs(left.z - right.z) < width)

    def arm(shoulder, elbow, wrist):
        joint, hand = points[elbow], points[wrist]
        if visible(wrist):
            return 'up' if hand.y < shoulder.y else 'down'
        # A clearly raised elbow also proves an arm is raised if the hand exits at the top.
        if visible(elbow) and joint.y < shoulder.y - .08 * width and hand.y < 0:
            return 'up'
        # Use the upper arm to confirm a lowered arm at the lower image boundary.
        # A hidden hand in the interior or above the picture remains ambiguous.
        if (joint.visibility >= .55 and 0 <= joint.x <= 1 and
                shoulder.y + .3 * width < joint.y <= 1.1 and hand.y >= .95 and hand.y > joint.y):
            return 'down'
        return 'unknown'

    gesture = 'unknown'
    if front:
        arms = arm(left,13,15), arm(right,14,16)
        gesture = {('up','down'):'left', ('down','up'):'right', ('up','up'):'both',
                   ('down','down'):'down'}.get(arms, 'unknown')
    return Observation((cx, cy), width, (abs(left.x-right.x), abs(left.y-right.y)),
                       nose.y if visible(0) else cy, gesture)


def target_box(obs, size):
    w, h = size
    cx, cy = obs.center
    # Compute vertical margins in pixels so portrait and landscape crops are comparable.
    span = max(obs.shoulder_delta[0] * w, obs.shoulder_delta[1] * h, 8)
    top = min(cy * h - span * .95, obs.nose_y * h - span * .55)
    return (max(0, int(cx*w - .86*span)), max(0, int(top)),
            min(w, int(cx*w + .86*span)), min(h, int(cy*h + 1.6*span)))


@dataclass
class Event:
    person: int
    stamp: float
    frames: list


@dataclass
class Track:
    obs: Observation
    seen: float
    pose: str = ''
    since: float = 0
    latched: bool = False
    pending: Event | None = None


class Gestures:
    def __init__(self):
        self.tracks = {}
        self.serial = 0

    def step(self, stamp, image, observations):
        completed = []
        candidates = []
        for key, track in self.tracks.items():
            if stamp - track.seen > .75:
                continue
            for i, obs in enumerate(observations):
                distance = hypot(*(a-b for a,b in zip(obs.center, track.obs.center)))
                scale = max(obs.width, track.obs.width)
                if distance < scale * .8 and min(obs.width, track.obs.width) > scale * .55:
                    candidates.append((distance / scale, key, i))
        matched = {}
        used = set()
        for _, key, i in sorted(candidates):
            if key not in used and i not in matched:
                used.add(key)
                matched[i] = key

        for i, obs in enumerate(observations):
            if i not in matched:
                self.serial += 1
                key = self.serial
                self.tracks[key] = Track(obs, stamp)
            else:
                key = matched[i]
            track = self.tracks[key]
            if track.pose != obs.gesture or stamp - track.seen > .55:
                track.pose, track.since = obs.gesture, stamp
            track.seen, track.obs = stamp, obs
            if track.pose in ('left', 'right') and not track.latched and stamp - track.since >= .5:
                box = target_box(obs, image.size)
                full = image.copy()
                ImageDraw.Draw(full).rectangle(box, outline='#ffbd24', width=max(3, image.width//300))
                frames = [('举手全景', stamp, thumbnail(full)), ('上身细节', stamp, image.crop(box))]
                track.pending = Event(key, stamp, frames)
                track.latched = True
            if track.pending and track.pose == 'down':
                track.pending.frames.append(('放手后细节', stamp, image.crop(target_box(obs, image.size))))
                completed.append(track.pending)
                track.pending = None
            if track.pose == 'down' and stamp - track.since >= 1:
                track.latched = False
        for key, track in list(self.tracks.items()):
            lost = stamp - track.seen > .75
            if track.pending and (lost or stamp - track.pending.stamp >= 2):
                completed.append(track.pending)
                track.pending = None
            if lost:
                del self.tracks[key]
        return completed

    def finish(self):
        return [t.pending for t in self.tracks.values() if t.pending]


def pose_ready():
    path = ROOT / POSE_FILE
    return path.is_file() and path.stat().st_size == 5777746


class Detector:
    def __init__(self):
        if not pose_ready():
            raise ValueError('姿态模型未安装，请运行 python -m local.setup_vision')
        try:
            import mediapipe as mp
        except (ImportError, OSError) as exc:
            raise ValueError('姿态运行库未就绪，请安装 local/requirements.txt') from exc
        self.mp = mp
        vision = mp.tasks.vision
        options = vision.PoseLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=(ROOT / POSE_FILE).read_bytes()),
            running_mode=vision.RunningMode.VIDEO, num_poses=8)
        try:
            self.model = vision.PoseLandmarker.create_from_options(options)
        except (RuntimeError, ValueError) as exc:
            raise ValueError('姿态模型加载失败，请运行 python -m local.setup_vision 校验模型') from exc

    def observe(self, image, millis):
        import numpy as np
        small = image.copy()
        small.thumbnail((960, 960))
        data = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.asarray(small))
        result = self.model.detect_for_video(data, millis)
        return [obs for poses in result.pose_landmarks if (obs := observation(poses)) is not None]

    def close(self):
        self.model.close()


def scan(source, *, stop=None, connected=None, detector_factory=Detector):
    live = stop is not None
    options = dict(format='rtsp', options={'rtsp_transport':'tcp', 'allowed_media_types':'video'},
                   timeout=(5, 7)) if live else {}
    gestures = Gestures()
    start = None
    last = None
    live_anchor = time.time()
    detector = detector_factory()
    try:
        with av.open(str(source), **options) as container:
            if not container.streams.video:
                raise ValueError('文件没有视频画面')
            stream = container.streams.video[0]
            duration = (float(stream.duration * stream.time_base) if stream.duration
                        else (container.duration or 0) / av.time_base)
            if not live and not 0 < duration <= 1800:
                raise ValueError('无法读取时长或视频超过 30 分钟')
            for frame in container.decode(stream):
                if live and stop.is_set():
                    return
                if frame.time is not None:
                    now = float(frame.time)
                elif live:
                    now = time.monotonic()
                else:
                    continue
                if start is None:
                    start = now
                    live_anchor = time.time()
                stamp = round(now - start, 3)
                if last is not None and stamp < last - 1:
                    raise ValueError('视频时间戳中断，请重新连接或检查文件')
                if last is not None and stamp - last < SAMPLE_INTERVAL - .001:
                    continue
                if not live and stamp > 1800:
                    raise ValueError('视频超过 30 分钟')
                image = frame.to_image().convert('RGB')
                obs = detector.observe(image, round((now - start) * 1000))
                if last is None and connected:
                    connected()
                last = stamp
                yield from gestures.step(live_anchor + stamp if live else stamp, image, obs)
            if last is None:
                raise ValueError('没有解码到有效画面')
            if not live and duration - last > max(1, 2 / float(stream.average_rate or 4)):
                raise ValueError('视频提前结束，举手扫描未完成')
            yield from gestures.finish()
    except av.FFmpegError:
        if live and not stop.is_set():
            yield from gestures.finish()
        raise
    finally:
        detector.close()
