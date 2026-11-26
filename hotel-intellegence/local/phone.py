"""Front-desk calls, evidence review and departmental work orders."""

import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
import av
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from local.phone_audio import inspect_levels, quality, transcribe_channels
from local.audio import decode, decode_blocks, SAMPLE_RATE
from local.platform_db import database, ROOT
from local.access import PHONE, identity
from local.phone_analysis import analyze, save_analysis
from local.phone_insights import insight_detail, redact

router = APIRouter(prefix="/api/phone")
MEDIA = ROOT / "phone-recordings"
MEDIA.mkdir(exist_ok=True)


def mentioned_rooms(segments):
    digits = str.maketrans("零〇一二两三四五六七八九", "001223456789")
    rooms = set()
    for segment in segments:
        text = segment["text"].translate(digits)
        rooms.update(re.findall(r"(?<![0-9])([0-9]{3,5})\s*(?:号房|房间|室|房)", text))
        rooms.update(
            re.findall(r"(?:房号|房间号)(?:是|为)?\s*([0-9]{3,5})(?![0-9])", text)
        )
    return sorted(rooms)


class Extension(BaseModel):
    number: str = Field(min_length=1, max_length=32)
    kind: str = Field(pattern="^(前台|客房)$")
    label: str = Field(min_length=1, max_length=80)


@router.get("/extensions")
def extensions():
    with database() as db:
        return [dict(r) for r in db.execute("SELECT * FROM extensions ORDER BY number")]


@router.put("/extensions")
def set_extension(body: Extension):
    with database() as db:
        db.execute(
            "INSERT OR REPLACE INTO extensions VALUES(?,?,?)",
            (body.number.strip(), body.kind, body.label.strip()),
        )
    return {"ok": True}


def routing(db, caller, callee):
    ends = {
        r["number"]: r
        for r in db.execute(
            "SELECT * FROM extensions WHERE number IN (?,?)", (caller, callee)
        )
    }
    a, b = ends.get(caller), ends.get(callee)
    if a and b and a["kind"] != b["kind"]:
        room = a if a["kind"] == "客房" else b
        return room["label"], a["kind"] + " → " + b["kind"]
    return "", "待确认"


def ingest(
    stream,
    name,
    caller,
    callee,
    started,
    test,
    source="手工导入",
    external="",
    meta=None,
):
    meta = dict(meta or {})
    meta["received_at"] = datetime.now().astimezone().isoformat()
    try:
        when = datetime.fromisoformat(started)
        if when.tzinfo is not None:
            when = when.astimezone()
    except ValueError:
        raise HTTPException(400, "通话时间无效")
    if not caller.strip() or not callee.strip() or max(len(caller), len(callee)) > 32:
        raise HTTPException(400, "请核实主被叫分机")
    ext = Path(name).suffix.lower()
    if ext not in {".wav", ".mp3", ".ogg", ".m4a", ".aac", ".flac", ".mp4", ".webm"}:
        raise HTTPException(400, "请选择解压后的音频文件")
    key = uuid.uuid4().hex
    path = MEDIA / (key + ext)
    sha = hashlib.sha256()
    try:
        with path.open("xb") as dst:
            while chunk := stream.read(1024 * 1024):
                dst.write(chunk)
                sha.update(chunk)
                if source == "手工导入" and dst.tell() > 150 * 1024**2:
                    raise HTTPException(413, "文件超过 150 MB")
        try:
            with av.open(str(path)) as src:
                if not src.streams.audio:
                    raise ValueError()
                duration = src.duration / av.time_base if src.duration else None
                if source == "手工导入" and duration and duration > 1800:
                    raise HTTPException(400, "录音超过 30 分钟")
        except (ValueError, av.FFmpegError):
            raise HTTPException(400, "没有可读音轨，请检查下载文件")
        external = external or (("test:" if test else "live:") + sha.hexdigest())
        if source == "手工导入":
            # Preserve identification of uploads made before scope-specific keys.
            with database() as db:
                legacy = db.execute(
                    "SELECT id FROM calls WHERE source=? AND external_id=? AND test=?",
                    (source, sha.hexdigest(), int(test)),
                ).fetchone()
                if legacy:
                    path.unlink()
                    return {"id": legacy[0], "duplicate": True}
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM calls WHERE source=? AND external_id=?",
                (source, external),
            ).fetchone()
            if existing:
                path.unlink()
                return {"id": existing[0], "duplicate": True}
            room, direction = routing(db, caller.strip(), callee.strip())
            db.execute(
                "INSERT INTO calls(id,source,external_id,caller,callee,started,room,direction,filename,stored,duration,test,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    source,
                    external,
                    caller.strip(),
                    callee.strip(),
                    when.isoformat(),
                    room,
                    direction,
                    Path(name).name,
                    path.name,
                    duration,
                    int(test),
                    "排队中",
                ),
            )
            db.execute(
                "UPDATE calls SET meta=?,direction=?,room=? WHERE id=?",
                (
                    json.dumps(meta, ensure_ascii=False),
                    meta.get("direction") or direction,
                    meta.get("room") or room,
                    key,
                ),
            )
        return {"id": key, "duplicate": False}
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.post("/calls")
def upload(
    file: UploadFile = File(...),
    caller: str = Form(...),
    callee: str = Form(...),
    started: str = Form(...),
    test: bool = Form(True),
    room: str = Form("", max_length=80),
    direction: str = Form("", max_length=40),
):
    return ingest(
        file.file,
        file.filename or "",
        caller,
        callee,
        started,
        test,
        meta={"room": room, "direction": direction},
    )


def process_next():
    models = ROOT / "models"
    from local.speech import recognize_turns, speakers_ready

    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT id,stored,source,duration,meta FROM calls WHERE status='排队中' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if not row:
            return False
        db.execute("UPDATE calls SET status='转写中' WHERE id=?", (row["id"],))
    try:
        if not (models / "sensevoice/model.int8.onnx").is_file():
            raise ValueError("中文转写模型尚未安装，准备后可重试")
        media = MEDIA / row["stored"]
        automatic = row["source"] in ("交换机目录", "模拟交换机")
        # Voice identities from independent diarization runs cannot be joined safely.
        diarize = speakers_ready(models) and (
            not automatic or 0 < (row["duration"] or 0) <= 1800
        )
        segments = []
        offset = 0
        metadata = json.loads(row["meta"])
        warnings = quality(media)
        signal_present = signal_noisy = False
        if metadata.get("channel_map"):
            segments, overlap = transcribe_channels(
                media, metadata["channel_map"], recognize_turns, models
            )
            if overlap:
                warnings.append("两个通道存在同时说话的片段，请分别回听")
        else:
            blocks = decode_blocks(media) if automatic else (decode(media),)
            for block in blocks:
                audible, noisy = inspect_levels(block)
                signal_present |= audible
                signal_noisy |= noisy
                if not block.any():
                    offset += len(block) / SAMPLE_RATE
                    continue
                result = recognize_turns(block, models, "zh", 2, diarize)
                for segment in result["segments"]:
                    segments.append(
                        {
                            **segment,
                            "id": len(segments),
                            "start": segment["start"] + offset,
                            "end": segment["end"] + offset,
                        }
                    )
                offset += len(block) / SAMPLE_RATE
            if not signal_present:
                warnings.append("录音接近静音，请检查话机两端收音")
            if signal_noisy:
                warnings.append("持续背景声较强，请回听核实原话")
        metadata["quality"] = warnings
        with database() as db:
            db.execute(
                "UPDATE calls SET meta=? WHERE id=?",
                (json.dumps(metadata, ensure_ascii=False), row["id"]),
            )
        if not segments:
            raise ValueError(
                ("；".join(warnings) + "。" if warnings else "")
                + "未识别出语音，请回听确认"
            )
        with database() as db:
            db.execute("UPDATE calls SET status='分析中' WHERE id=?", (row["id"],))
        result = analyze(segments)
        with database() as db:
            db.execute("BEGIN IMMEDIATE")
            save_analysis(db, row["id"], segments, result)
            db.execute(
                "UPDATE calls SET status='已转写',error='',segments=? WHERE id=?",
                (json.dumps(segments, ensure_ascii=False), row["id"]),
            )
    except Exception as exc:
        with database() as db:
            db.execute(
                "UPDATE calls SET status='失败',error=? WHERE id=?",
                (str(exc)[:240], row["id"]),
            )
    return True


class PhoneWorker:
    def __init__(self):
        self.folder = os.environ.get("HOTEL_PHONE_INBOX") or str(ROOT / "phone-inbox")
        if not os.environ.get("HOTEL_PHONE_INBOX"):
            Path(self.folder).mkdir(exist_ok=True)
        self.test_folder = ROOT / "phone-test-inbox"
        if Path(self.folder).resolve() == self.test_folder.resolve():
            raise ValueError("营业接收目录和测试目录须分别配置")
        self.test_folder.mkdir(exist_ok=True)
        self.last_scan = 0
        self.feed_error = ""
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self.run, daemon=True, name="phone-worker"
        )

    def start(self):
        with database() as db:
            db.execute(
                "UPDATE calls SET status='排队中' WHERE status IN ('转写中','分析中')"
            )
        self.thread.start()

    def run(self):
        while not self.stop_event.is_set():
            if self.folder and time.monotonic() - self.last_scan > 5:
                from local.phone_feed import receive

                self.last_scan = time.monotonic()
                errors = []
                for folder, test, label in (
                    (self.test_folder, True, "模拟"),
                    (self.folder, False, "营业"),
                ):
                    try:
                        receive(folder, test=test)
                    except OSError:
                        errors.append(label + "录音来源目录暂不可用")
                self.feed_error = "；".join(errors)
            if not process_next():
                self.stop_event.wait(2)

    def stop(self):
        self.stop_event.set()
        self.thread.join()


@router.post("/reconcile")
def reconcile_file(
    request: Request, file: UploadFile = File(...), test: bool = Form(True)
):
    from io import StringIO
    from local.phone_feed import compare_cdr

    content = file.file.read(4 * 1024 * 1024 + 1)
    if len(content) > 4 * 1024 * 1024:
        raise HTTPException(413, "话单超过 4 MB")
    try:
        details=[]
        result = compare_cdr(StringIO(content.decode("utf-8-sig")), test,details=details)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with database() as db:
        batch=db.execute(
            "INSERT INTO phone_reconciliations(test,actor,at,result) VALUES(?,?,?,?)",
            (
                int(test),
                identity(request)["name"],
                time.time(),
                json.dumps(result, ensure_ascii=False),
            ),
        ).lastrowid
        db.executemany("INSERT INTO phone_cdr_rows(batch,external_key,leg,caller,callee,started,original) VALUES(?,?,?,?,?,?,?)",((batch,*row) for row in details))
    return result


@router.get("/health")
def health(request: Request):
    worker = request.app.state.phones
    with database() as db:
        last = db.execute(
            "SELECT max(rowid),started FROM calls WHERE source='交换机目录'"
        ).fetchone()
        queue = db.execute(
            "SELECT count(*) FROM calls WHERE status IN ('排队中','转写中','分析中')"
        ).fetchone()[0]
        failed = db.execute(
            "SELECT count(*) FROM calls WHERE status='失败'"
        ).fetchone()[0]
    rejected = 0
    if worker.folder:
        rejected = sum(1 for _ in Path(worker.folder).glob("*.ready.rejected"))
    with database() as db:
        simulated = db.execute(
            "SELECT max(json_extract(meta,'$.received_at')) FROM calls WHERE source='模拟交换机'"
        ).fetchone()[0]
        received = db.execute(
            "SELECT max(json_extract(meta,'$.received_at')) FROM calls WHERE source='交换机目录'"
        ).fetchone()[0]
        latest = []
        issues = []
        if identity(request)["role"] == "管理员":
            for scope in (0, 1):
                rec = db.execute(
                    "SELECT at,result FROM phone_reconciliations WHERE test=? ORDER BY id DESC LIMIT 1",
                    (scope,),
                ).fetchone()
                if rec:
                    latest.append(
                        dict(
                            test=bool(scope),
                            at=rec["at"],
                            counts=json.loads(rec["result"]),
                        )
                    )
            for label, folder in (
                ("营业", worker.folder),
                ("测试", worker.test_folder),
            ):
                path = Path(folder)
                try:
                    waiting = sum(1 for _ in path.glob("*.ready.json"))
                    if waiting:
                        issues.append(f"{label}有 {waiting} 份清单等待收件或匹配音频")
                    for error in sorted(path.glob("*.ready.error.txt"))[-5:]:
                        issues.append(
                            label
                            + "拒收："
                            + redact(error.read_text(encoding="utf-8")[:160])
                        )
                except OSError:
                    issues.append(label + "目录暂不可读")
    return {
        "reconciliation": latest,
        "issues": issues,
        "source_configured": bool(os.environ.get("HOTEL_PHONE_INBOX")),
        "last_simulated": simulated,
        "last_received": received,
        "test_inbox": (
            str(worker.test_folder) if identity(request)["role"] == "管理员" else ""
        ),
        "configured": bool(worker.folder),
        "error": worker.feed_error,
        "rejected": rejected,
        "last": last[1],
        "queue": queue,
        "failed": failed,
    }


@router.get("/calls")
def calls(offset: int = 0, pending: bool = False):
    where = (
        "WHERE test=0 AND EXISTS(SELECT 1 FROM items WHERE call_id=calls.id AND state='待确认')"
        if pending
        else ""
    )
    with database() as db:
        return [
            redact(dict(r))
            for r in db.execute(
                "SELECT id,caller,callee,started,room,direction,test,status,filename FROM calls "
                + where
                + " ORDER BY started DESC,id DESC LIMIT 50 OFFSET ?",
                (max(0, offset),),
            )
        ]


def detail(key):
    with database() as db:
        r = db.execute("SELECT * FROM calls WHERE id=?", (key,)).fetchone()
        if not r:
            raise HTTPException(404, "通话不存在")
        data = dict(r)
        data.pop("stored")
        data["segments"] = json.loads(data["segments"])
        data["meta"] = json.loads(data["meta"])
        data["complaint"] = bool(data["meta"].get("confirmed_complaint"))
        data["quality"] = data["meta"].get("quality", [])
        data.update(insight_detail(db, key))
        data["mentionedRooms"] = mentioned_rooms(data["segments"])
        data["roomConflict"] = bool(
            data["room"] and any(n != data["room"] for n in data["mentionedRooms"])
        )
        data["items"] = [
            dict(i)
            for i in db.execute(
                "SELECT i.*,o.id AS orderId,o.state AS orderState,o.room AS confirmedRoom FROM items i LEFT JOIN phone_order_links l ON l.item=i.id LEFT JOIN orders o ON o.item=i.id OR o.id=l.order_id WHERE i.call_id=? ORDER BY i.id",
                (key,),
            )
        ]
        return data


@router.get("/calls/{key}")
def call(key: str, request: Request):
    data = detail(key)
    with database() as db:
        data["favorite"] = bool(
            db.execute(
                "SELECT 1 FROM phone_favorites WHERE call_id=? AND actor=?",
                (key, identity(request)["name"]),
            ).fetchone()
        )
    if not identity(request)["phone"]["numbers"]:
        data = redact(data)
        data.pop("meta", None)
        data.pop("external_id", None)
    return data


@router.get("/calls/{key}/export")
def export_call(key: str, request: Request):
    data = detail(key)
    with database() as db:
        db.execute(
            "INSERT INTO phone_exports VALUES(?,?,?)",
            (identity(request)["name"], time.time(), "通话 " + key),
        )
    data.pop("external_id", None)
    data.pop("meta", None)
    return JSONResponse(
        redact(data),
        headers={"Content-Disposition": 'attachment; filename="call-report.json"'},
    )


@router.get("/calls/{key}/audio")
def audio(key: str):
    with database() as db:
        row = db.execute("SELECT stored FROM calls WHERE id=?", (key,)).fetchone()
    if not row or not (MEDIA / row[0]).is_file():
        raise HTTPException(404, "录音不存在")
    return FileResponse(MEDIA / row[0])


@router.post("/calls/{key}/retry")
def retry(key: str):
    with database() as db:
        if not db.execute(
            "UPDATE calls SET status='排队中',error='' WHERE id=? AND status='失败'",
            (key,),
        ).rowcount:
            raise HTTPException(409, "只能重试失败通话")
    return {"ok": True}


class Candidate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=1, max_length=1000)
    start: float = Field(ge=0)


@router.post("/calls/{key}/items")
def add_candidate(key: str, body: Candidate):
    with database() as db:
        row = db.execute("SELECT duration FROM calls WHERE id=?", (key,)).fetchone()
        if not row or (row[0] and body.start > row[0]):
            raise HTTPException(400, "通话或证据时间无效")
        db.execute(
            "INSERT INTO items(call_id,title,department,quote,start) VALUES(?,?,?,?,?)",
            (key, body.title, body.department, body.quote, body.start),
        )
    return {"ok": True}


class Dispatch(BaseModel):
    room: str = Field(default="", max_length=80)
    title: str = Field(min_length=1, max_length=200)
    department: str = Field(min_length=1, max_length=80)
    minutes: int = Field(default=30, ge=1, le=10080)


@router.post("/items/{item}/dispatch")
def dispatch(item: int, body: Dispatch, request: Request):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        item_row = db.execute("SELECT title FROM items WHERE id=?", (item,)).fetchone()
        if (
            item_row
            and (
                body.department in ("客房部", "工程部")
                or item_row[0] in ("设施报修", "送物", "客房清洁")
            )
            and not body.room.strip()
        ):
            raise HTTPException(400, "上门服务请先核实房号")
        if not db.execute(
            "UPDATE items SET state='已派单' WHERE id=? AND state='待确认'", (item,)
        ).rowcount:
            raise HTTPException(409, "事项已处理，请刷新")
        actor = identity(request)["name"]
        oid = db.execute(
            "INSERT INTO orders(item,title,room,department,state,due) VALUES(?,?,?,?,?,?)",
            (
                item,
                body.title,
                body.room,
                body.department,
                "待接单",
                time.time() + 60 * body.minutes,
            ),
        ).lastrowid
        db.execute(
            "INSERT INTO history(order_id,actor,state,note,at) VALUES(?,?,?,?,?)",
            (oid, actor, "待接单", "", time.time()),
        )
    return {"id": oid}


@router.post("/items/{item}/dismiss")
def dismiss(item: int):
    with database() as db:
        if not db.execute(
            "UPDATE items SET state='已忽略' WHERE id=? AND state='待确认'", (item,)
        ).rowcount:
            raise HTTPException(409, "事项已处理")
    return {"ok": True}


@router.get("/orders")
def orders(request: Request):
    u = identity(request)
    where, args = (
        ("", ())
        if u["role"] in PHONE
        else (" WHERE o.department=?", (u["department"],))
    )
    with database() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT o.*,i.call_id,c.test,(SELECT max(l.at) FROM phone_order_links l WHERE l.order_id=o.id) AS last_repeat,(SELECT max(h.at) FROM history h WHERE h.order_id=o.id AND h.state='交班跟进') AS last_follow FROM orders o JOIN items i ON i.id=o.item JOIN calls c ON c.id=i.call_id"
                + where
                + " ORDER BY o.id DESC",
                args,
            )
        ]
        for r in rows:
            r["history"] = [
                dict(h)
                for h in db.execute(
                    "SELECT actor,state,note,at FROM history WHERE order_id=? ORDER BY id",
                    (r["id"],),
                )
            ]
        return rows


class Transition(BaseModel):
    state: str
    note: str = Field(default="", max_length=1000)


@router.post("/orders/{oid}")
def transition(oid: int, body: Transition, request: Request):
    u = identity(request)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        order = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not order or (
            u["role"] not in PHONE and order["department"] != u["department"]
        ):
            raise HTTPException(404, "工单不存在")
        transitions = {
            "待接单": ["处理中"],
            "处理中": ["待复核"],
            "待复核": ["已关闭", "处理中"],
        }
        old = order["state"]
        if body.state not in transitions.get(old, []):
            raise HTTPException(409, "工单状态已变更")
        if old == "待复核" and u["role"] not in PHONE:
            raise HTTPException(403, "请由前台或经理复核")
        if (
            body.state == "待复核" or old == "待复核" and body.state == "处理中"
        ) and not body.note.strip():
            raise HTTPException(400, "请填写处理结果或退回原因")
        db.execute("UPDATE orders SET state=?,desk_rev=desk_rev+1,owner=CASE WHEN ?='待接单' THEN ? ELSE owner END WHERE id=?", (body.state,old,u["name"],oid))
        db.execute(
            "INSERT INTO history(order_id,actor,state,note,at) VALUES(?,?,?,?,?)",
            (oid, u["name"], body.state, body.note, time.time()),
        )
    return {"ok": True}
