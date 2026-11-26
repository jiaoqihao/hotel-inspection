"""Local badge archive, customer links and append-only finding reviews."""

from pathlib import Path
from datetime import datetime, timezone
import json
from typing import Literal
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from local.access import identity
from local.badge_privacy import SCENES, declared_test_scenes
from local.badge_analysis import coverage

GROUPS = ("conclusions", "checks", "risks", "insights", "todos", "flow")


def migrate(db):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > 7:
        raise RuntimeError("胸牌档案数据库版本高于本程序")
    if version == 0:
        db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE IF NOT EXISTS badge_customers(
            id TEXT PRIMARY KEY, name TEXT NOT NULL, test INTEGER NOT NULL,
            UNIQUE(name, test));
        PRAGMA user_version=1;
        COMMIT;
        """)

    if version < 2:
        db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE badge_sync_config(id INTEGER PRIMARY KEY, app TEXT NOT NULL, secret BLOB NOT NULL,
            devices TEXT NOT NULL, test INTEGER NOT NULL, enabled INTEGER NOT NULL);
        CREATE TABLE badge_sync_cursor(app TEXT, device TEXT, until TEXT NOT NULL, PRIMARY KEY(app,device));
        CREATE TABLE badge_sync_files(app TEXT, device TEXT, file TEXT, record_id TEXT NOT NULL,
            PRIMARY KEY(app,device,file));
        PRAGMA user_version=2;
        COMMIT;
        """)

    if version < 3:
        db.executescript("""
        BEGIN IMMEDIATE;
        ALTER TABLE badge_sync_cursor ADD COLUMN since TEXT;
        UPDATE badge_sync_cursor SET since=strftime('%Y-%m-%dT%H:%M:%S', until, '-1 day');
        PRAGMA user_version=3;
        COMMIT;
        """)

    if version < 4:
        db.executescript("""
        BEGIN IMMEDIATE;
        CREATE TABLE badge_devices(
            id INTEGER PRIMARY KEY, app TEXT NOT NULL, number TEXT NOT NULL, source TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '', position TEXT NOT NULL DEFAULT '', test INTEGER NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, retired INTEGER NOT NULL DEFAULT 0,
            revision INTEGER NOT NULL DEFAULT 1, synced TEXT, error TEXT NOT NULL DEFAULT '',
            UNIQUE(app,number));
        INSERT INTO badge_devices(app,number,source,test,synced)
        SELECT c.app,j.value->>'$.number',j.value->>'$.source',c.test,k.until
        FROM badge_sync_config c,json_each(c.devices) j LEFT JOIN badge_sync_cursor k
        ON k.app=c.app AND k.device=j.value->>'$.number';
        INSERT OR IGNORE INTO badge_devices(app,number,source,test,retired)
        SELECT f.app,f.device,r.data->>'$.source',coalesce(r.data->>'$.test',0),1
        FROM badge_sync_files f JOIN records r ON r.id=f.record_id
        WHERE r.data->>'$.source' IN ('胸牌','挂牌');
        UPDATE records SET data=json_set(data,'$.deviceId',d.id)
        FROM badge_sync_files f JOIN badge_devices d ON d.app=f.app AND d.number=f.device
        WHERE records.id=f.record_id;
        UPDATE records SET data=json_set(data,'$.deviceId',
            (SELECT min(d.id) FROM badge_devices d WHERE d.number=records.data->>'$.badge' AND d.source=records.data->>'$.source'))
        WHERE data->>'$.deviceId' IS NULL AND 1=(SELECT count(*) FROM badge_devices d
            WHERE d.number=records.data->>'$.badge' AND d.source=records.data->>'$.source');
        PRAGMA user_version=4;
        COMMIT;
        """)

    if version < 5:
        db.executescript("""
        BEGIN IMMEDIATE;
        UPDATE records SET data=json_set(data,'$.wasMarkedTest',json('true'),'$.test',json('false'))
        WHERE data->>'$.test'=1;
        UPDATE badge_devices SET test=0;
        UPDATE badge_sync_config SET test=0;
        PRAGMA user_version=5;
        COMMIT;
        """)


    if version < 6:
        # Only change taxonomy metadata. Retain finding indices, human reviews and tasks.
        with db:
            db.execute("BEGIN IMMEDIATE")
            for key, data in db.execute("SELECT id,data FROM records").fetchall():
                item = json.loads(data)
                report = item.get("report") or {}
                before = json.dumps(item, ensure_ascii=False)
                if item.get("sceneHint") == "其他":
                    item["sceneHint"] = "自动识别"
                if "其他" in item.get("detectedScenes", []):
                    item["detectedScenes"] = [name for name in item["detectedScenes"] if name in SCENES]
                if report.get("scene") == "其他" or "其他" in report.get("scenes", []):
                    names = [name for name in report.get("scenes", []) if name in SCENES]
                    if not names:
                        names = declared_test_scenes(seg.get("text","") for seg in item.get("segments", [])
                                                    if not seg.get("overlap"))
                        report["sceneSource"] = "转写明示" if names else "待重新识别"
                    report.update(scenes=names, scene=names[0] if len(names)==1 else "混合场景" if names else None)
                    item["detectedScenes"] = names
                    if "coverage" in report:
                        report["coverage"] = [row for row in report["coverage"] if row.get("scene") in names]
                    if report.get("sceneSource") == "转写明示" and not any(report.get(group) for group in GROUPS):
                        report["summary"] = "录音明示" + "、".join(names) + "场景识别测试。"
                        report["coverage"] = coverage(names, {group: [] for group in GROUPS})
                    for group in GROUPS:
                        for row in report.get(group, []):
                            if row.get("scene") == "其他":
                                row.pop("scene")
                after = json.dumps(item, ensure_ascii=False)
                if after != before:
                    db.execute("UPDATE records SET data=? WHERE id=?", (after, key))
            db.execute("PRAGMA user_version=6")


    if version < 7:
        db.executescript(Path(__file__).with_name("wecom_schema.sql").read_text(encoding="utf-8"))


class Customer(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    test: bool = False


class Link(BaseModel):
    customerId: str | None


class Feedback(BaseModel):
    state: Literal["确认", "纠正", "排除", "撤回", "已处理"]
    value: str = Field(default="", max_length=600)
    note: str = Field(default="", max_length=1000)


def router(connection, edit_record):
    routes = APIRouter()

    def change(record_id, apply):
        with edit_record(record_id) as (db, item):
            if item["status"] != "done":
                raise HTTPException(409, "请等待分析结束")
            apply(db, item)
        return item

    @routes.get("/api/badge/customers")
    def customers():
        with connection() as db:
            people = {
                r["id"]: dict(id=r["id"], name=r["name"], entries=[])
                for r in db.execute(
                    "SELECT id,name FROM badge_customers ORDER BY name,id",

                )
            }
            rows = db.execute(
                """SELECT data FROM records WHERE json_extract(data,'$.customerId') IS NOT NULL
                                 ORDER BY rowid DESC""",

            )
            for row in rows:
                item = json.loads(row[0])
                customer = people.get(item["customerId"])
                if not customer:
                    continue
                for i, finding in enumerate(item["report"].get("insights", [])):
                    history = finding.get("feedback")
                    if not history:
                        continue
                    last = history[-1]
                    if (
                        last["state"] in ("确认", "纠正")
                        and last.get("customerId") == customer["id"]
                    ):
                        customer["entries"].append(
                            dict(
                                recordId=item["id"],
                                filename=item["filename"],
                                index=i,
                                label=finding["label"],
                                value=last["value"] or finding["value"],
                                evidence=finding["evidence"],
                            )
                        )
        return list(people.values())

    @routes.post("/api/badge/customers")
    def create_customer(body: Customer):
        name = body.name.strip()
        if not name:
            raise HTTPException(400, "请填写客户名称")
        with connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT id,name FROM badge_customers WHERE name=? ORDER BY test,id LIMIT 1', (name,)).fetchone()
            if existing:
                return dict(existing)
            person = dict(id=uuid4().hex, name=name)
            db.execute('INSERT INTO badge_customers VALUES(?,?,0)', (person['id'], name))
            return person

    @routes.patch("/api/records/{id}/customer")
    def link(id: str, body: Link):
        def apply(db, item):
            report = item.get("report") or {}
            if "销售沟通" not in report.get("scenes", [report.get("scene")]):
                raise HTTPException(400, "仅销售录音可关联客户")
            person = None
            if body.customerId:
                person = db.execute(
                    "SELECT name FROM badge_customers WHERE id=?",
                    (body.customerId,),
                ).fetchone()
                if not person:
                    raise HTTPException(404, "当前数据范围内没有此客户")
            item.update(
                customerId=body.customerId, customerName=person[0] if person else None
            )

        return change(id, apply)

    @routes.patch("/api/records/{id}/findings/{group}/{index}")
    def review(id: str, group: str, index: int, body: Feedback, request: Request):
        def apply(db, item):
            rows = item["report"].get(group, [])
            if group not in GROUPS or not 0 <= index < len(rows):
                raise HTTPException(404, "报告条目不存在")
            finding = rows[index]
            history = finding.setdefault("feedback", [])
            value, note = body.value.strip(), body.note.strip()
            if body.state == "纠正" and not value:
                raise HTTPException(400, "请填写纠正后的结论")
            if body.state == "已处理" and (
                not finding.get("complaint")
                or not note
                or not history
                or history[-1]["state"] not in ("确认", "纠正", "已处理")
            ):
                raise HTTPException(409, "请先确认客诉预警，再填写处理结果")
            if body.state in ("确认", "纠正") and not finding.get("evidence"):
                raise HTTPException(400, "缺少可回听证据")
            event = dict(
                state=body.state,
                value=value if body.state == "纠正" else "",
                note=note,
                actor=identity(request)["name"],
            )
            if group == "insights" and finding.get("scene", item["report"]["scene"]) == "销售沟通":
                event["customerId"] = item.get("customerId")
            if history and all(history[-1].get(k) == v for k, v in event.items()):
                return
            history.append(dict(**event, at=datetime.now(timezone.utc).isoformat()))
            item.update(reviewed=False, reviewedAt=None)

        return change(id, apply)

    return routes
