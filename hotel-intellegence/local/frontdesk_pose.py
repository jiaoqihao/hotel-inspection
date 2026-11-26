"""Local front-desk gesture detection, shared by RTSP and uploaded video."""
from collections import Counter
from dataclasses import dataclass, field
from math import hypot
import time

import av
from PIL import ImageDraw, ImageFilter, ImageStat

from local.setup_vision import POSE_FILE, ROOT
from local.vision_model import thumbnail

SCENE = '前台接待'
CHECK = '仪容仪表'
NO_EVENT = '未检测到符合条件的举手'
TURN_NO_EVENT = '未检测到完整转身'
SAMPLE_INTERVAL = .25
MAX_TRACK_GAP = 1.0
TURN_SECONDS = 12


@dataclass
class Observation:
    center: tuple
    width: float
    shoulder_delta: tuple
    nose_y: float
    gesture: str
    body: tuple | None
    legs: tuple | None
    shoes: tuple | None
    view: str
    head: tuple | None


def observation(points):
    def visible(i, threshold=.6):
        p = points[i]
        return min(p.visibility, p.presence) >= threshold and 0 <= p.x <= 1 and 0 <= p.y <= 1

    def torso_pair(a, b):
        return (all(points[i].presence >= .6 and 0 <= points[i].x <= 1 and 0 <= points[i].y <= 1
                    for i in (a,b)) and (visible(a) or visible(b)))

    def bounds(indices, padding):
        xs = [points[i].x for i in indices]
        ys = [points[i].y for i in indices]
        return (max(0,min(xs)-padding), max(0,min(ys)-padding),
                min(1,max(xs)+padding), min(1,max(ys)+padding))

    def direction(a, b):
        dx, dz = points[a].x-points[b].x, points[a].z-points[b].z
        if abs(dx) > 1.2 * abs(dz):
            return '正面' if dx > 0 else '背面'
        if abs(dz) > 1.2 * abs(dx):
            return '右侧' if dz > 0 else '左侧'
        return ''

    # On profile views the far shoulder/hip may have low visibility but must still be localized.
    if not torso_pair(11,12):
        return None
    left,right = points[11],points[12]
    dx,dy,dz = left.x-right.x,left.y-right.y,left.z-right.z
    width = hypot(dx,dy,dz)
    if width < .015:
        return None
    cx,cy = (left.x+right.x)/2,(left.y+right.y)/2
    gesture,view = 'unknown',''
    body = legs = shoes = head = None
    nose = points[0]
    landmarks = (0,2,5,11,12,13,14,15,16,23,24,25,26,27,28,29,30,31,32)
    if torso_pair(23,24):
        shoulder_view = direction(11,12)
        hips_dx = points[23].x-points[24].x
        hips_dz = points[23].z-points[24].z
        # Use shoulder span for both depth comparisons: hips are narrower, especially
        # in the overhead reception view. Preserve the verified frontal gesture gate.
        front_depth = .8 * hypot(dx,dy)
        if dx*hips_dx > 0 and max(abs(dz),abs(hips_dz)) < front_depth:
            view = '正面' if dx>0 else '背面'
        elif shoulder_view in ('左侧','右侧') and shoulder_view == direction(23,24):
            view = shoulder_view
        hip_y = (points[23].y+points[24].y)/2
        torso = hip_y-cy
        crown = cy-.8*torso
        if visible(0) and visible(2) and visible(5):
            eye_y = (points[2].y+points[5].y)/2
            crown = min(crown,eye_y-2*max(0,nose.y-eye_y))
        near_legs = [i for i in (23,24) if all(visible(j,.50) for j in (i,i+2,i+4))
                     and points[i].y < points[i+2].y < points[i+4].y
                     and any(visible(j,.50) for j in (i+6,i+8))]
        feet = [i for i in range(27,33) if visible(i,.50)]
        feet_inside = feet and all(.005 < points[i].x < .995 and points[i].y < .995 for i in feet)
        if torso <= .015 or crown <= 0 or not feet_inside or not near_legs:
            view = 'clipped'
        elif view:
            visible_parts = [i for i in landmarks if visible(i,.5)]
            l,t,r,b = bounds(visible_parts,width*.18)
            body = (l,min(t,crown),r,b)
            head = (max(0,cx-width*.70),crown,min(1,cx+width*.70),min(1,cy+torso*.12))
            legs = bounds([i for i in range(23,33) if visible(i,.5)],width*.23)
            shoes = bounds(feet,width*.22)
            if view == '正面':
                front = (len(near_legs)==2 and all(visible(i) for i in (0,2,5))
                         and points[2].x > nose.x > points[5].x
                         and abs(nose.x-cx) < .4*width)
                if not front:
                    view = ''
                elif all(visible(i) for i in landmarks):
                    up = points[15].y < left.y, points[16].y < right.y
                    gesture = {(True,False):'left',(False,True):'right',
                               (True,True):'both',(False,False):'down'}[up]
    else:
        # A missing lower torso is an occlusion, not a transitional turn angle.
        view = 'clipped'
    return Observation((cx,cy),width,(abs(dx),abs(dy)),nose.y if visible(0) else cy,
                       gesture,body,legs,shoes,view,head)


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


def crop_region(image, region):
    box = tuple(round(value * scale) for value,scale in zip(region,image.size*2))
    return image.crop(box)


@dataclass
class Turn:
    event: Event
    angles: list = field(default_factory=lambda: ['正面'])
    samples: dict = field(default_factory=dict)
    candidate: str = ''
    consecutive: int = 0

    def advance(self, stamp, image, obs):
        view = obs.view
        if stamp-self.event.stamp > TURN_SECONDS or view == 'clipped':
            return 'cancel'
        self.consecutive = self.consecutive+1 if view == self.candidate else 1
        self.candidate = view
        if not view or self.consecutive < 2:
            return ''
        if view != self.angles[-1]:
            if len(self.angles) == 1:
                expected = ('左侧','右侧')
            else:
                other = '左侧' if self.angles[1]=='右侧' else '右侧'
                expected = (('背面',other,'正面')[len(self.angles)-2],)
            if view not in expected:
                return 'cancel'
            self.angles.append(view)
        if len(self.angles) == 1:
            return ''
        body = crop_region(image,obs.body)
        small = body.convert('L')
        small.thumbnail((96,128))
        sharpness = ImageStat.Stat(small.filter(ImageFilter.FIND_EDGES)).mean[0]
        if view not in self.samples or sharpness > self.samples[view][0]:
            frames = [(view+'全身',stamp,body)]
            if view == '正面':
                frames.append(('转回正面上身',stamp,image.crop(target_box(obs,image.size))))
            else:
                frames.append((view+'头部',stamp,crop_region(image,obs.head)))
            frames.extend([(view+'下装',stamp,crop_region(image,obs.legs)),
                           (view+'鞋履',stamp,crop_region(image,obs.shoes))])
            self.samples[view] = sharpness,frames
        if len(self.angles) == 5:
            for angle in self.angles[1:]:
                self.event.frames.extend(self.samples[angle][1])
            return 'complete'
        return ''


@dataclass
class Track:
    obs: Observation
    seen: float
    pose: str = ''
    since: float = 0
    latched: bool = False
    pending: Turn | None = None


def track_distance(previous, obs, size):
    dx = abs(obs.center[0] - previous.center[0])
    dy = abs(obs.center[1] - previous.center[1])
    if obs.shoes and previous.shoes:
        # Shoulder-to-foot height remains comparable through a rotation.
        # MediaPipe shoulder depth (and hence 3-D width) jumps in profile.
        h1 = obs.shoes[3] - obs.center[1]
        h2 = previous.shoes[3] - previous.center[1]
        height = min(h1, h2)
        # A profile can shift the estimated shoulder centre as the far arm disappears.
        profile = previous.view in ('左侧', '右侧') or obs.view in ('左侧', '右侧')
        max_vertical = .22 if profile else .10
        if height <= .55 * max(h1, h2) or dy > max_vertical * height:
            return None
        distance = hypot(dx * size[0] / size[1], dy)
        return distance / height if distance < .32 * height else None
    # Keep partial observations associated so clipping cancels a turn.
    distance = hypot(dx, dy)
    scale = max(obs.width, previous.width)
    if distance < scale * .8 and min(obs.width, previous.width) > scale * .55:
        return distance / scale
    return None


class Gestures:
    def __init__(self, progress=None):
        self.progress = progress
        self.tracks = {}
        self.serial = 0

    def step(self, stamp, image, observations):
        def report(message):
            if self.progress:
                self.progress(stamp, message)

        def pixels(region):
            return tuple(round(value*scale) for value,scale in zip(region,image.size*2))

        completed = []
        candidates = []
        for key, track in self.tracks.items():
            if stamp - track.seen > MAX_TRACK_GAP:
                continue
            for i, obs in enumerate(observations):
                distance = track_distance(track.obs, obs, image.size)
                if distance is not None:
                    candidates.append((distance, key, i))
        # If multiple people can match the same observation (or vice versa), discard
        # their partial rotations before association so no one can finish another turn.
        per_track = Counter(key for _,key,i in candidates)
        per_pose = Counter(i for _,key,i in candidates)
        ambiguous = {key for _,key,i in candidates if per_track[key]>1 or per_pose[i]>1}
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
            if key in ambiguous:
                if track.pending:
                    report('转身中断，多个人体位置重叠')
                track.pending = None
                track.pose = ''
            if track.pose != obs.gesture or stamp - track.seen > .55:
                track.pose, track.since = obs.gesture, stamp
            track.seen, track.obs = stamp, obs
            if track.pose in ('left', 'right') and key not in ambiguous and not track.latched and stamp - track.since >= .5:
                box = target_box(obs, image.size)
                full = image.copy()
                body = pixels(obs.body)
                ImageDraw.Draw(full).rectangle(body, outline='#ffbd24', width=max(3, image.width//300))
                frames = [('举手全景', stamp, thumbnail(full)), ('上身细节', stamp, image.crop(box))]
                frames.extend((role,stamp,image.crop(pixels(region))) for role,region in
                              [('全身细节',obs.body),('下装细节',obs.legs),('鞋履细节',obs.shoes)])
                track.pending = Turn(Event(key, stamp, frames))
                track.latched = True
                report('已识别全身举手，等待转身')
            if track.pending:
                previous_angle = track.pending.angles[-1]
                result = track.pending.advance(stamp,image,obs)
                if result == 'cancel':
                    if stamp - track.pending.event.stamp > TURN_SECONDS:
                        report('转身中断，超过 12 秒')
                    elif obs.view == 'clipped':
                        report('转身中断，全身取景丢失')
                    else:
                        report('转身中断，朝向顺序不连续')
                elif result == 'complete':
                    report('完整转身已识别，正在生成记录')
                elif track.pending.angles[-1] != previous_angle:
                    report('转身已识别至' + obs.view)
                if result == 'complete':
                    completed.append(track.pending.event)
                if result:
                    track.pending = None
            elif track.pose == 'down' and stamp-track.since >= 1:
                track.latched = False
        for key, track in list(self.tracks.items()):
            if track.seen != stamp:
                if track.pending:
                    report('转身中断，未能连续跟踪举手者')
                track.pose = 'unknown'
                track.pending = None
            if stamp-track.seen > MAX_TRACK_GAP or (track.seen != stamp and not track.latched):
                del self.tracks[key]
        return completed


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
        self.detail_model = None
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
        observed = [obs for poses in result.pose_landmarks if (obs := observation(poses)) is not None]
        if image.width < 1.5 * image.height:
            return observed

        # Wide reception views can lose a small person during the model's input resize.
        # Evaluate each half at its own scale; the full view covers people on the seam.
        if self.detail_model is None:
            vision = self.mp.tasks.vision
            options = vision.PoseLandmarkerOptions(
                base_options=self.mp.tasks.BaseOptions(model_asset_buffer=(ROOT / POSE_FILE).read_bytes()),
                running_mode=vision.RunningMode.IMAGE, num_poses=8)
            self.detail_model = vision.PoseLandmarker.create_from_options(options)
        mid = image.width // 2
        for left, right in ((0, mid), (mid, image.width)):
            tile = image.crop((left, 0, right, image.height))
            tile.thumbnail((960, 960))
            pixels = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=np.asarray(tile))
            for points in self.detail_model.detect(pixels).pose_landmarks:
                local_pose = observation(points)
                if local_pose is None or local_pose.view in ('','clipped'):
                    continue
                for p in points:
                    p.x = (left + p.x * (right-left)) / image.width
                    p.z *= (right-left) / image.width
                obs = observation(points)
                if obs is None or obs.view in ('','clipped'):
                    continue
                match = next((i for i, old in enumerate(observed)
                              if hypot(old.center[0]-obs.center[0], old.center[1]-obs.center[1])
                              < .5 * max(old.width, obs.width)), None)
                if match is None:
                    observed.append(obs)
                elif observed[match].view in ('','clipped') or (observed[match].gesture == 'unknown' and obs.gesture != 'unknown'):
                    observed[match] = obs
        return observed

    def close(self):
        self.model.close()
        if self.detail_model is not None:
            self.detail_model.close()


def scan(source, *, stop=None, connected=None, progress=None, detector_factory=Detector):
    live = stop is not None
    options = dict(format='rtsp', options={'rtsp_transport':'tcp', 'allowed_media_types':'video'},
                   timeout=(5, 7)) if live else {}
    gestures = Gestures(progress)
    heartbeat = None
    start = None
    last = None
    live_anchor = None
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
                # RTSP PTS can jump forward or disappear mid-stream. Use a single
                # local clock for live intervals; file analysis retains media time.
                if live:
                    now = time.monotonic()
                elif frame.time is not None:
                    now = float(frame.time)
                else:
                    continue
                if start is None:
                    start = now
                    if live:
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
                observed_at = live_anchor + stamp if live else stamp
                if progress and (heartbeat is None or stamp - heartbeat >= 2):
                    progress(observed_at, None)
                    heartbeat = stamp
                yield from gestures.step(observed_at, image, obs)
            if last is None:
                raise ValueError('没有解码到有效画面')
            if not live and duration - last > max(1, 2 / float(stream.average_rate or 4)):
                raise ValueError('视频提前结束，举手扫描未完成')
    finally:
        detector.close()
