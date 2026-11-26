"""Persistent desk assignments, escalation and reconciliation casework."""

import asyncio
import json
import logging
import time
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from local import access
from local.phone_insights import log, mask_number
from local.platform_db import database

router = APIRouter(prefix="/api/phone")
SUPERVISORS = access.SUPERVISORS
DUTY_ROLES = ("值班经理", "替班负责人")


def event(db, key, action, actor, recipient, note, now):
    db.execute(
        "INSERT INTO phone_duty_events(call_id,action,actor,recipient,note,at) VALUES(?,?,?,?,?,?)",
        (key, action, actor, recipient, note, now),
    )


def sync_alerts(now=None):
    now = time.time() if now is None else now
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        duty = db.execute("SELECT * FROM phone_duty").fetchone()
        for a in db.execute(
            """SELECT a.call_id,a.created FROM phone_alerts a LEFT JOIN phone_alert_tasks t ON t.call_id=a.call_id
                             WHERE a.level IN ('S','A') AND a.state<>'已排除' AND t.call_id IS NULL"""
        ).fetchall():
            deadline = now + duty["minutes"] * 60
            db.execute(
                "INSERT INTO phone_alert_tasks(call_id,recipient,backup,due) VALUES(?,?,?,?)",
                (a["call_id"], duty["primary_name"], duty["backup_name"], deadline),
            )
            event(db, a["call_id"], "站内通知", "系统", duty["primary_name"], "", now)
            log(db, a["call_id"], "系统", "站内通知", duty["primary_name"], {})
        for t in db.execute(
            """SELECT t.* FROM phone_alert_tasks t JOIN phone_alerts a ON a.call_id=t.call_id
                            WHERE t.state='待接手' AND t.escalated IS NULL AND t.due<=?
                            AND a.level IN ('S','A') AND a.state<>'已排除'""",
            (now,),
        ).fetchall():
            db.execute(
                "UPDATE phone_alert_tasks SET escalated=?,revision=revision+1 WHERE call_id=?",
                (now, t["call_id"]),
            )
            event(db, t["call_id"], "升级提醒", "系统", t["backup"], "", now)
            log(db, t["call_id"], "系统", "升级提醒", t["backup"], {})


async def duty_clock(app):
    while True:
        try:
            await asyncio.to_thread(sync_alerts)
            app.state.duty_error = ""
        except Exception:
            logging.exception("电话值班提醒暂不可用")
            app.state.duty_error = "值班提醒暂不可用，系统将在五秒后重试"
        await asyncio.sleep(5)


def roster(db):
    return list(DUTY_ROLES) + [
        r[0]
        for r in db.execute(
            "SELECT name FROM users WHERE role IN ('管理员','经理') ORDER BY name"
        )
    ]


class Duty(BaseModel):
    primary_name: str = Field(min_length=1, max_length=100)
    backup_name: str = Field(min_length=1, max_length=100)
    minutes: int = Field(ge=1, le=240)


@router.put("/duty")
def save_duty(body: Duty):
    with database() as db:
        names = roster(db)
        if (
            body.primary_name not in names
            or body.backup_name not in names
            or body.primary_name == body.backup_name
        ):
            raise HTTPException(400, "请选择不同的主值班与替班接收人")
        db.execute(
            "UPDATE phone_duty SET primary_name=?,backup_name=?,minutes=? WHERE id=1",
            (body.primary_name, body.backup_name, body.minutes),
        )
    return {"ok": True}


@router.get("/duty")
def duty_board(request: Request, test: bool = False):
    with database() as db:
        rows = [
            dict(r)
            for r in db.execute(
                """SELECT t.*,a.level,c.room,c.started,c.summary,a.first_read FROM phone_alert_tasks t
           JOIN phone_alerts a ON a.call_id=t.call_id JOIN calls c ON c.id=t.call_id WHERE c.test=?
           AND a.level IN ('S','A') AND a.state<>'已排除' AND t.state IN ('待接手','跟进中')
           ORDER BY t.escalated IS NULL,t.state!='待接手',a.level!='S',t.due""",
                (int(test),),
            )
        ]
        for r in rows:
            r["summary"] = mask_number(r["summary"])
            r["events"] = [
                dict(e)
                for e in db.execute(
                    "SELECT action,actor,recipient,note,at FROM phone_duty_events WHERE call_id=? ORDER BY id DESC",
                    (r["call_id"],),
                )
            ]
        return dict(
            rows=rows,
            settings=dict(db.execute("SELECT * FROM phone_duty").fetchone()),
            roster=roster(db),
            error=getattr(request.app.state, "duty_error", ""),
        )


class DutyAction(BaseModel):
    revision: int
    action: str
    recipient: str = Field(default="", max_length=100)
    note: str = Field(default="", max_length=500)


@router.post("/duty/{key}")
def duty_action(key: str, body: DutyAction, request: Request):
    u = access.identity(request)
    if u["role"] not in SUPERVISORS:
        raise HTTPException(403, "请由经理或管理员处理高风险预警")
    now = time.time()
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        t = db.execute(
            """SELECT t.* FROM phone_alert_tasks t JOIN phone_alerts a ON a.call_id=t.call_id
                     WHERE t.call_id=? AND a.level IN ('S','A') AND a.state<>'已排除'""",
            (key,),
        ).fetchone()
        if (
            not t
            or t["revision"] != body.revision
            or t["state"] not in ("跟进中", "待接手")
        ):
            raise HTTPException(409, "预警已变化，请刷新")
        if u["role"] != "管理员" and t["owner"] and t["owner"] != u["name"]:
            raise HTTPException(403, "请由当前负责人转交")
        if (
            u["role"] != "管理员"
            and not t["owner"]
            and t["recipient"] not in (*DUTY_ROLES, u["name"])
            and not (t["escalated"] and t["backup"] in (*DUTY_ROLES, u["name"]))
        ):
            raise HTTPException(403, "请由当班接收人接手")
        target = t["recipient"]
        if body.action == "接手" and t["state"] == "待接手":
            state, owner = "跟进中", u["name"]
        elif (
            body.action == "转交" and body.recipient in roster(db) and body.note.strip()
        ):
            state, owner, target = "待接手", "", body.recipient
            duty = db.execute("SELECT * FROM phone_duty").fetchone()
            backup = (
                duty["backup_name"]
                if target != duty["backup_name"]
                else duty["primary_name"]
            )
            db.execute(
                "UPDATE phone_alert_tasks SET escalated=NULL,backup=?,due=? WHERE call_id=?",
                (backup, now + 60 * duty["minutes"], key),
            )
        elif body.action in ("办结", "排除") and body.note.strip():
            if body.action == "办结" and t["state"] != "跟进中":
                raise HTTPException(409, "请先接手再办结")
            state, owner = "已" + body.action, u["name"]
            if body.action == "排除":
                db.execute(
                    "UPDATE phone_alerts SET state='已排除' WHERE call_id=?", (key,)
                )
        else:
            raise HTTPException(400, "请核对操作、接收人并填写处理说明")
        db.execute(
            "UPDATE phone_alert_tasks SET owner=?,state=?,recipient=?,revision=revision+1 WHERE call_id=?",
            (owner, state, target, key),
        )
        event(db, key, body.action, u["name"], owner or target, body.note, now)
        log(
            db,
            key,
            u["name"],
            "值班" + body.action,
            body.note,
            {"接收人": owner or target},
        )
    return {"ok": True}


CDR_VIEW = """WITH compared AS (
 SELECT r.*,c.id AS call_id,CASE
 WHEN original IN ('明细重复','录音缺话单') THEN original
 WHEN original='未接来电' THEN CASE WHEN c.id IS NULL THEN '未接来电' ELSE '未接但有录音' END
 WHEN c.id IS NULL THEN '缺少录音'
 WHEN r.caller<>c.caller OR r.callee<>c.callee THEN '号码不匹配'
 WHEN original IN ('缺少录音','号码不匹配') THEN '已自动销差'
 ELSE '已匹配' END AS reason
 FROM phone_cdr_rows r JOIN phone_reconciliations b ON b.id=r.batch
 LEFT JOIN calls c ON c.external_id=r.external_key AND c.test=b.test AND c.source=CASE b.test WHEN 1 THEN '模拟交换机' ELSE '交换机目录' END
 WHERE r.batch=?
), ledger AS (SELECT *,CASE WHEN reason='已自动销差' THEN '已销差' WHEN reason IN ('已匹配','未接来电') THEN '正常'
 WHEN checked_reason=reason THEN '已核查' ELSE '待核查' END AS status FROM compared) """


@router.get("/ledger")
def ledger(test: bool = False, status: str = "待核查", offset: int = Query(0, ge=0)):
    if status not in ("全部", "正常", "待核查", "已核查", "已销差"):
        raise HTTPException(400, "无效筛选")
    with database() as db:
        db.execute("BEGIN")
        batch = db.execute(
            "SELECT id,at FROM phone_reconciliations WHERE test=? ORDER BY id DESC LIMIT 1",
            (int(test),),
        ).fetchone()
        counts = {}
        rows = []
        if batch:
            counts = {
                r[0]: r[1]
                for r in db.execute(
                    CDR_VIEW + "SELECT status,count(*) FROM ledger GROUP BY status",
                    (batch["id"],),
                )
            }
            rows = [
                dict(r)
                for r in db.execute(
                    CDR_VIEW
                    + """SELECT id,external_key,leg,started,caller,callee,call_id,original,reason,status,actor,note
                            FROM ledger WHERE (?='全部' OR status=?) ORDER BY id LIMIT 50 OFFSET ?""",
                    (batch["id"], status, status, offset),
                )
            ]
            for row in rows:
                raw = row.pop("external_key")
                try:
                    parts = json.loads(raw)
                except ValueError:
                    parts = None
                row["reference"] = mask_number(
                    str(parts[1])
                    if isinstance(parts, list) and len(parts) == 3
                    else raw
                )
                for k in ("caller", "callee", "leg", "note"):
                    row[k] = mask_number(row[k])
        return dict(rows=rows, counts=counts, batch=dict(batch) if batch else None)


class Check(BaseModel):
    note: str = Field(min_length=1, max_length=500)
    reason: str


@router.post("/ledger/{row_id}")
def check_row(row_id: int, body: Check, request: Request):
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT batch FROM phone_cdr_rows WHERE id=?", (row_id,)
        ).fetchone()
        state = (
            db.execute(
                CDR_VIEW + "SELECT reason,status FROM ledger WHERE id=?",
                (row[0], row_id),
            ).fetchone()
            if row
            else None
        )
        if not state or state["status"] != "待核查" or state["reason"] != body.reason:
            raise HTTPException(409, "差异已变化，请刷新")
        if not body.note.strip():
            raise HTTPException(400, "请填写核查结果")
        db.execute(
            "UPDATE phone_cdr_rows SET checked_reason=?,actor=?,note=? WHERE id=?",
            (body.reason, access.identity(request)["name"], body.note.strip(), row_id),
        )
    return {"ok": True}


@router.get("/items/{item}/suggestions")
def suggest(item: int, room: str = Query(min_length=1, max_length=80)):
    with database() as db:
        current = db.execute(
            "SELECT i.*,c.started,c.test FROM items i JOIN calls c ON c.id=i.call_id WHERE i.id=?",
            (item,),
        ).fetchone()
        if not current:
            raise HTTPException(404, "事项不存在")
        rows = db.execute(
            """SELECT o.id,o.title,o.owner,o.state,o.department,o.follow_at,c.started,i.quote,
           (SELECT note FROM history WHERE order_id=o.id AND note<>'' ORDER BY id DESC LIMIT 1) AS progress
           FROM orders o JOIN items i ON i.id=o.item JOIN calls c ON c.id=i.call_id
           WHERE c.test=? AND c.id<>? AND o.room=? AND (i.title=? OR o.department=?)
           AND julianday(c.started) BETWEEN julianday(?,'-7 days') AND julianday(?)
           ORDER BY (i.title=?) DESC,c.started DESC,o.id DESC LIMIT 5""",
            (
                current["test"],
                current["call_id"],
                room.strip(),
                current["title"],
                current["department"],
                current["started"],
                current["started"],
                current["title"],
            ),
        )
        return [
            {
                **dict(r),
                "quote": mask_number(r["quote"]),
                "progress": mask_number(r["progress"] or ""),
            }
            for r in rows
        ]


class Follow(BaseModel):
    revision: int
    claim: bool = False
    follow_at: datetime | None = None
    feedback: str = Field(default="", max_length=500)


@router.post("/orders/{oid}/follow")
def follow(oid: int, body: Follow, request: Request):
    user = access.identity(request)
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        o = db.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
        if not o or (
            user["role"] == "部门员工" and o["department"] != user["department"]
        ):
            raise HTTPException(404, "工单不存在")
        if o["desk_rev"] != body.revision:
            raise HTTPException(409, "交班记录已更新，请刷新")
        if not body.claim and not body.feedback.strip():
            raise HTTPException(400, "请填写回访结果或交班说明")
        if (
            body.claim
            and o["owner"]
            and o["owner"] != user["name"]
            and not body.feedback.strip()
        ):
            raise HTTPException(400, "换班接手请说明原因")
        owner = user["name"] if body.claim else o["owner"]
        at = body.follow_at.timestamp() if body.follow_at else None
        db.execute(
            "UPDATE orders SET owner=?,follow_at=?,feedback=?,desk_rev=desk_rev+1 WHERE id=?",
            (owner, at, body.feedback.strip() or o["feedback"], oid),
        )
        note = " · ".join(
            s
            for s in (
                owner,
                body.feedback.strip(),
                body.follow_at.isoformat(timespec="minutes") if body.follow_at else "",
            )
            if s
        )
        db.execute(
            "INSERT INTO history(order_id,actor,state,note,at) VALUES(?,?,?,?,?)",
            (oid, user["name"], "交班跟进", note, time.time()),
        )
    return {"ok": True}
