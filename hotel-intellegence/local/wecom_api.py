"""Management endpoints for daily reports, effective-dated wearers and store scripts."""

from datetime import date, timedelta
import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, SecretStr

from local import access, badge_daily, badge_scripts as scripts
from local.patrol import protect
from local.wecom_delivery import HOOK, now_text, post_text


class Settings(BaseModel):
    webhook: SecretStr | None = None
    clock: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    enabled: bool = False


class Binding(BaseModel):
    since: date
    employee: str = Field(max_length=80)


class Rule(BaseModel):
    rule: str = Field(default="", max_length=64)
    version: int = 0
    since: date
    scene: Literal["会议", "销售沟通", "餐厅服务"]
    title: str = Field(min_length=1, max_length=60)
    trigger: str = Field(min_length=1, max_length=300)
    standard: str = Field(min_length=1, max_length=400)
    alternatives: str = Field(default="", max_length=400)
    enabled: bool = False


class ScriptReview(BaseModel):
    version: int
    segment: int
    status: Literal["符合", "已确认偏差", "证据不足", "排除"]


class Send(BaseModel):
    day: date
    update: bool = False
    previous: int | None = None


class Resolution(BaseModel):
    action: Literal["已收到", "确认未收到", "重试"]


def report_day(day):
    day = day or scripts.local_day() - timedelta(days=1)
    if day >= scripts.local_day():
        raise HTTPException(400, "请选择北京时间昨天或更早的日期")
    return day.isoformat()


def router(delivery):
    routes = APIRouter(prefix="/api/badge/wecom")
    connection = delivery.connection

    @routes.get("/settings")
    def settings():
        with connection() as db:
            row = db.execute("SELECT secret IS NOT NULL,clock,enabled FROM badge_wecom_config").fetchone()
        return dict(configured=bool(row[0]), clock=row[1], enabled=bool(row[2]), yesterday=report_day(None))

    @routes.put("/settings")
    def configure(body: Settings):
        supplied = body.webhook.get_secret_value().strip() if body.webhook else ""
        if supplied and not HOOK.fullmatch(supplied):
            raise HTTPException(400, "请粘贴从企业微信内部群复制的完整消息推送地址")
        with delivery.destination_lock, connection() as db:
            db.execute("BEGIN IMMEDIATE")
            secret = db.execute("SELECT secret FROM badge_wecom_config").fetchone()[0]
            if supplied:
                try:
                    secret = protect(supplied.encode())
                except Exception:
                    raise HTTPException(503, "当前服务账户无法加密保存地址") from None
                db.execute(
                    """UPDATE badge_wecom_runs SET state='已取消',info='推送地址已更换',updated=?
                              WHERE state NOT IN ('已发送','已取消')""",
                    (now_text(),),
                )
            if body.enabled and not secret:
                raise HTTPException(400, "请先填写群 Webhook")
            db.execute(
                "UPDATE badge_wecom_config SET secret=?,clock=?,enabled=?", (secret, body.clock, body.enabled)
            )
        return settings()

    @routes.post("/test")
    def test():
        with delivery.destination_lock, connection() as db:
            secret = db.execute("SELECT secret FROM badge_wecom_config").fetchone()[0]
            if not secret:
                raise HTTPException(409, "请先保存群 Webhook")
            state, info, _ = post_text(secret, "栖鉴连接测试 · 此条消息仅用于确认门店管理群。")
        if state != "已发送":
            raise HTTPException(502, info)
        return {"message": "企微已接收测试消息，请在目标群确认。"}

    @routes.get("/preview")
    def preview(day: date | None = None):
        with connection() as db:
            db.execute("BEGIN")
            return badge_daily.report(db, report_day(day))

    @routes.post("/message-preview")
    def message_preview(body: Send):
        day = report_day(body.day)
        with connection() as db:
            db.execute("BEGIN")
            snap = badge_daily.report(db, day)
            last = db.execute("SELECT max(revision) FROM badge_wecom_runs WHERE day=?", (day,)).fetchone()[0]
        return {"parts": badge_daily.outbound(snap, (last + int(body.update)) if last else 1)}

    @routes.post("/prepare")
    def prepare(body: Send):
        errors = delivery.prepare(report_day(body.day))
        return {"message": f"已检查已有转写，{errors} 段暂未完成，可稍后重试。"}

    @routes.post("/send")
    def send(body: Send):
        return {"id": delivery.enqueue(report_day(body.day), body.update, previous=body.previous)}

    @routes.get("/history")
    def history():
        with connection() as db:
            return [
                dict(row)
                for row in db.execute("""SELECT id,day,revision,state,sent,json_array_length(parts) AS total,
                info,updated FROM badge_wecom_runs ORDER BY id DESC LIMIT 60""")
            ]

    @routes.get("/history/{run}")
    def snapshot(run: int):
        with connection() as db:
            row = db.execute("SELECT parts FROM badge_wecom_runs WHERE id=?", (run,)).fetchone()
            if not row:
                raise HTTPException(404, "发送记录不存在")
            return {"parts": json.loads(row[0])}

    @routes.post("/history/{run}")
    def resolve(run: int, body: Resolution):
        delivery.resolve(run, body.action)
        return {"ok": True}

    @routes.get("/bindings/{device}")
    def bindings(device: int):
        with connection() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT since,employee FROM badge_wearers WHERE device=? ORDER BY since DESC", (device,)
                )
            ]

    @routes.put("/bindings/{device}")
    def bind(device: int, body: Binding):
        with connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM badge_devices WHERE id=? AND retired=0", (device,)).fetchone():
                raise HTTPException(409, "设备不存在或已停用")
            db.execute(
                "INSERT OR REPLACE INTO badge_wearers VALUES(?,?,?)",
                (device, body.since.isoformat(), body.employee.strip()),
            )
        return bindings(device)

    @routes.get("/rules")
    def rules():
        with connection() as db:
            return scripts.rules_at(db, "9999-12-31")

    @routes.post("/rules")
    def change_rule(body: Rule):
        values = body.model_dump(exclude={"rule", "since", "version"})
        for key in ("title", "trigger", "standard", "alternatives"):
            values[key] = values[key].strip()
        if not all(values[k] for k in ("title", "trigger", "standard")):
            raise HTTPException(400, "请填写名称、触发条件和标准说法")
        with connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rule_id = body.rule or uuid4().hex
            last = db.execute(
                "SELECT version,since FROM badge_script_versions WHERE rule=? ORDER BY version DESC LIMIT 1",
                (rule_id,),
            ).fetchone()
            if body.rule and (not last or body.version != last[0] or body.since.isoformat() < last[1]):
                raise HTTPException(409, "请刷新后编辑，生效日期不得早于上一版本")
            db.execute(
                "INSERT INTO badge_script_versions(rule,since,definition) VALUES(?,?,?)",
                (rule_id, body.since.isoformat(), json.dumps(values, ensure_ascii=False)),
            )
        return rules()

    @routes.post("/reviews/{record_id}")
    def review(record_id: str, body: ScriptReview, request: Request):
        with delivery.edit_record(record_id) as (db, item):
            if item.get("status") != "done" or body.version not in [
                r["version"] for r in scripts.applicable(db, item)
            ]:
                raise HTTPException(409, "录音或话术版本已变化")
            seg = next((s for s in item.get("segments", []) if s["id"] == body.segment), None)
            if seg is None or seg.get("overlap") and body.status in ("符合", "已确认偏差"):
                raise HTTPException(400, "请选择归属清楚且可回听的发言")
            name = scripts.employee_for(
                db, item, scripts.recording_time(item["recordedAt"]).date().isoformat()
            )
            if not name and body.status in ("符合", "已确认偏差"):
                raise HTTPException(409, "请先确认录音员工")
            item.setdefault("scriptReviews", {}).setdefault(str(body.version), {}).setdefault(
                str(body.segment), []
            ).append(
                dict(status=body.status, employee=name, actor=access.identity(request)["name"], at=now_text())
            )
        return {"ok": True}

    return routes
