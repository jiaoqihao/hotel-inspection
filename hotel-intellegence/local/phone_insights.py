"""Telephone register, versioned human review, favorites and complaint events."""

import json
import re
import time
from datetime import date
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from local.access import identity
from local.phone_analysis import analyze, save_analysis, CATEGORIES
from local.platform_db import database

router = APIRouter(prefix="/api/phone")


def mask_number(value):
    return re.sub(r"(?<!\d)(\+?\d{2,4})\d{3,}(\d{3})(?!\d)", r"\1****\2", value)


def redact(value):
    if isinstance(value, str):
        return mask_number(value)
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {
            k: (
                v
                if k in {"id", "call_id", "external_id", "date", "started", "at"}
                else redact(v)
            )
            for k, v in value.items()
        }
    return value


def insight_detail(db, key):
    rows = db.execute(
        "SELECT version,at,result FROM phone_analyses WHERE call_id=? ORDER BY version DESC",
        (key,),
    ).fetchall()
    alert = db.execute("SELECT * FROM phone_alerts WHERE call_id=?", (key,)).fetchone()
    events = db.execute(
        "SELECT actor,action,note,changes,at FROM phone_reviews WHERE call_id=? ORDER BY id DESC",
        (key,),
    ).fetchall()
    return dict(
        analysis=json.loads(rows[0]["result"]) if rows else None,
        versions=[
            dict(
                version=r["version"],
                at=r["at"],
                category=json.loads(r["result"])["category"],
            )
            for r in rows
        ],
        alert=dict(alert) if alert else None,
        reviews=[dict(r) for r in events],
    )


@router.get("/register")
def register(
    request: Request,
    q: str = Query("", max_length=180),
    scope: str = "营业",
    category: str = "",
    since: date | None = None,
    until: date | None = None,
    source: str = "",
    state: str = "",
    department: str = "",
    level: str = "",
    alerts: bool = False,
    unread: bool = False,
    favorites: bool = False,
    pending: bool = False,
    offset: int = Query(0, ge=0),
    repeat: bool = False,
):
    clauses, args = ["c.test=?"], [int(scope == "测试")]
    if scope not in ("测试", "营业"):
        raise HTTPException(400, "数据范围无效")
    for field, value in (
        ("c.category", category),
        ("c.source", source),
        ("c.review_state", state),
        ("a.level", level),
    ):
        if value:
            clauses.append(field + "=?")
            args.append(value)
    if since:
        clauses.append("substr(c.started,1,10)>=?")
        args.append(str(since))
    if until:
        clauses.append("substr(c.started,1,10)<=?")
        args.append(str(until))
    if q.strip():
        clauses.append(
            """(instr(c.caller,?) OR instr(c.callee,?) OR instr(c.room,?) OR instr(c.filename,?)
                    OR instr(c.summary,?) OR EXISTS(SELECT 1 FROM json_each(c.segments) s
                    WHERE instr(json_extract(s.value,'$.text'),?)) OR EXISTS(SELECT 1 FROM items i
                    WHERE i.call_id=c.id AND instr(i.title,?)))"""
        )
        args.extend([q.strip()] * 7)
    if alerts:
        clauses.append("a.call_id IS NOT NULL")
    if unread:
        clauses.append("a.first_read IS NULL AND a.state<>'已排除'")
    if pending:
        clauses.append(
            "EXISTS(SELECT 1 FROM items i WHERE i.call_id=c.id AND i.state='待确认')"
        )
    if department:
        clauses.append(
            "EXISTS(SELECT 1 FROM items i WHERE i.call_id=c.id AND i.department=?)"
        )
        args.append(department)
    if repeat:
        clauses.append(
            "EXISTS(SELECT 1 FROM items i JOIN phone_order_links l ON i.id=l.item WHERE i.call_id=c.id)"
        )
    if favorites:
        clauses.append("f.call_id IS NOT NULL")
    sql = """ FROM calls c LEFT JOIN phone_alerts a ON a.call_id=c.id
             LEFT JOIN phone_favorites f ON f.call_id=c.id AND f.actor=? WHERE """ + " AND ".join(
        clauses
    )
    args.insert(0, identity(request)["name"])
    with database() as db:
        total = db.execute("SELECT count(*)" + sql, args).fetchone()[0]
        rows = db.execute(
            """SELECT c.id,c.started,c.status,c.caller,c.callee,c.room,c.direction,c.test,c.filename,
            c.category,c.review_state,c.summary,c.source,a.level,a.state AS alert_state,a.first_read,
            f.call_id IS NOT NULL AS favorite"""
            + sql
            + " ORDER BY c.started DESC,c.id DESC LIMIT 50 OFFSET ?",
            (*args, offset),
        )
        return dict(total=total, rows=[redact(dict(r)) for r in rows])


def lock_call(db, key, revision=None):
    db.execute("BEGIN IMMEDIATE")
    row = db.execute("SELECT * FROM calls WHERE id=?", (key,)).fetchone()
    if not row:
        raise HTTPException(404, "通话不存在")
    if revision is not None and row["revision"] != revision:
        raise HTTPException(409, "记录已有新版本，请刷新后再复核")
    return row


def log(db, key, who, action, note, changes):
    db.execute(
        "INSERT INTO phone_reviews(call_id,actor,action,note,changes,at) VALUES(?,?,?,?,?,?)",
        (key, who, action, note, json.dumps(changes, ensure_ascii=False), time.time()),
    )


class SegmentEdit(BaseModel):
    id: int
    text: str = Field(min_length=1, max_length=16000)
    speaker: str = Field(default="", max_length=40)


class Review(BaseModel):
    complaint: bool | None = None
    revision: int
    category: str
    room: str = Field(default="", max_length=80)
    summary: str = Field(default="", max_length=1000)
    level: str = ""
    state: str
    note: str = Field(min_length=1, max_length=500)
    segments: list[SegmentEdit] = Field(default_factory=list, max_length=3000)


@router.post("/calls/{key}/review")
def review(key: str, body: Review, request: Request):
    if (
        body.category not in CATEGORIES
        or body.state not in ("已确认", "已排除", "需跟进")
        or body.level not in ("", "S", "A", "B", "C")
    ):
        raise HTTPException(400, "请核对分类、等级和处理状态")
    with database() as db:
        row = lock_call(db, key, body.revision)
        if row["status"] != "已转写":
            raise HTTPException(409, "请等待转写完成")
        old_alert = db.execute(
            "SELECT level,state FROM phone_alerts WHERE call_id=?", (key,)
        ).fetchone()
        changes = {
            "预警": [
                dict(old_alert) if old_alert else None,
                {"level": body.level, "state": body.state},
            ]
        }
        segments = json.loads(row["segments"])
        by_id = {s["id"]: s for s in segments}
        for edit in body.segments:
            seg = by_id.get(edit.id)
            if seg and "****" in edit.text and edit.text != seg["text"]:
                if mask_number(seg["text"]) == edit.text:
                    edit.text = seg["text"]
                else:
                    raise HTTPException(400, "含脱敏号码的片段请由有明文权限的同事修改")
            if not seg or not edit.text.strip():
                raise HTTPException(400, "无效转写片段")
            changes[str(edit.id)] = {
                "原文": seg["text"],
                "修订": edit.text,
                "原角色": seg.get("speaker"),
                "新角色": edit.speaker,
            }
            seg["text"] = edit.text.strip()
            if edit.speaker:
                seg["speaker"] = edit.speaker
        if (
            body.segments
            or not db.execute(
                "SELECT 1 FROM phone_analyses WHERE call_id=?", (key,)
            ).fetchone()
        ):
            if not db.execute(
                "SELECT 1 FROM phone_analyses WHERE call_id=?", (key,)
            ).fetchone():
                original = json.loads(row["segments"])
                save_analysis(db, key, original, analyze(original, False))
            if body.segments:
                save_analysis(db, key, segments, analyze(segments, False))
        if "****" in body.summary and mask_number(row["summary"]) == body.summary:
            body.summary = row["summary"]
        complaint = (
            body.category == "客诉" or body.complaint is True
        ) and body.state != "已排除"
        changes["确认客诉"] = complaint
        db.execute(
            "UPDATE calls SET meta=json_set(meta,'$.confirmed_complaint',?) WHERE id=?",
            (int(complaint), key),
        )
        for field in ("summary", "category", "room", "review_state"):
            new = body.state if field == "review_state" else getattr(body, field)
            if new != row[field]:
                changes[field] = [row[field], new]
        db.execute(
            """UPDATE calls SET category=?,room=?,summary=?,review_state=?,segments=?,revision=revision+1 WHERE id=?""",
            (
                body.category,
                body.room,
                body.summary,
                body.state,
                json.dumps(segments, ensure_ascii=False),
                key,
            ),
        )
        if body.level:
            db.execute(
                """INSERT INTO phone_alerts(call_id,level,state,created) VALUES(?,?,?,?)
                       ON CONFLICT(call_id) DO UPDATE SET level=excluded.level,state=excluded.state""",
                (key, body.level, body.state, time.time()),
            )
        else:
            db.execute("UPDATE phone_alerts SET state='已排除' WHERE call_id=?", (key,))
        log(db, key, identity(request)["name"], "人工复核", body.note, changes)
    return {"ok": True}


class Version(BaseModel):
    revision: int


@router.post("/calls/{key}/reanalyze")
def reanalyze(key: str, body: Version, request: Request):
    with database() as db:
        row = db.execute(
            "SELECT segments,status FROM calls WHERE id=?", (key,)
        ).fetchone()
        if not row or row["status"] != "已转写":
            raise HTTPException(409, "需要已转写的通话")
        segments = json.loads(row["segments"])
    result = analyze(segments)
    with database() as db:
        lock_call(db, key, body.revision)
        save_analysis(db, key, segments, result)
        log(
            db,
            key,
            identity(request)["name"],
            "重新分析",
            "",
            {"分类建议": result["category"]},
        )
    return {"ok": True}


@router.get("/calls/{key}/versions/{version}")
def historic(key: str, version: int):
    with database() as db:
        row = db.execute(
            "SELECT result,segments FROM phone_analyses WHERE call_id=? AND version=?",
            (key, version),
        ).fetchone()
        if not row:
            raise HTTPException(404, "版本不存在")
        return redact({k: json.loads(row[k]) for k in row.keys()})


@router.post("/calls/{key}/read")
def read_alert(key: str, request: Request):
    with database() as db:
        updated = db.execute(
            "UPDATE phone_alerts SET first_read=?,reader=? WHERE call_id=? AND first_read IS NULL AND state<>'已排除'",
            (time.time(), identity(request)["name"], key),
        )
        if updated.rowcount:
            log(db, key, identity(request)["name"], "首次查看预警", "", {})
    return {"ok": True}


@router.put("/calls/{key}/favorite")
def favorite(key: str, request: Request, enabled: bool):
    with database() as db:
        lock_call(db, key)
        actor = identity(request)["name"]
        if enabled:
            db.execute(
                "INSERT OR IGNORE INTO phone_favorites VALUES(?,?)", (actor, key)
            )
        else:
            db.execute(
                "DELETE FROM phone_favorites WHERE actor=? AND call_id=?", (actor, key)
            )
    return {"ok": True}


class Link(BaseModel):
    reopen: bool = False
    order: int
    note: str = Field(min_length=1, max_length=500)


@router.post("/items/{item}/link")
def link_order(item: int, body: Link, request: Request):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            """SELECT i.call_id,c.test,i.state FROM items i JOIN calls c ON c.id=i.call_id WHERE i.id=?""",
            (item,),
        ).fetchone()
        target = db.execute(
            """SELECT c.test,i.call_id,o.state FROM orders o JOIN items i ON i.id=o.item
                            JOIN calls c ON c.id=i.call_id WHERE o.id=?""",
            (body.order,),
        ).fetchone()
        if (
            not row
            or not target
            or row["test"] != target["test"]
            or row["call_id"] == target["call_id"]
        ):
            raise HTTPException(400, "请选择同一数据范围内另一通电话的工单")
        if row["state"] != "待确认":
            raise HTTPException(409, "事项已处理")
        who = identity(request)["name"]
        db.execute(
            "INSERT INTO phone_order_links VALUES(?,?,?,?)",
            (item, body.order, time.time(), who),
        )
        db.execute("UPDATE items SET state='已关联' WHERE id=?", (item,))
        if body.reopen:
            if target["state"] != "已关闭":
                raise HTTPException(409, "原单状态已变化")
            db.execute(
                "UPDATE orders SET state='处理中',owner=?,desk_rev=desk_rev+1 WHERE id=?",
                (who, body.order),
            )
            db.execute(
                "INSERT INTO history(order_id,actor,state,note,at) VALUES(?,?,?,?,?)",
                (body.order, who, "处理中", "复发续办 · " + body.note, time.time()),
            )
        log(db, row["call_id"], who, "关联已有工单", body.note, {"工单": body.order})
    return {"ok": True}
