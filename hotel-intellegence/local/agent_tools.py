"""Execute the reviewed tools against normal business handlers, with an authenticated actor."""

from datetime import date, timedelta, timezone
from html import escape
from pathlib import Path
from urllib.parse import urlencode
import json
import re
import time
import uuid

from fastapi import HTTPException
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from jsonschema import Draft202012Validator
import httpx

from local import badge_scripts, platform_db
from local.agent_catalog import catalog, needs_confirmation, resolved_path

CHINA = timezone(timedelta(hours=8))
MEDIA = {
    "vision_frame": "image",
    "recording_audio": "audio",
    "phone_audio": "audio",
    "vision_export_json": "download",
    "recording_export_html": "download",
    "vision_export": "download",
    "recording_export": "download",
    "phone_report_export": "download",
    "phone_export": "download",
}


def uid():
    return uuid.uuid4().hex


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def asset(owner, kind, data):
    key = uid()
    encoded = dump(data)
    with platform_db.database() as db:
        if kind == "media":
            found = db.execute(
                "SELECT id FROM agent_assets WHERE owner=? AND kind=? AND data=?",
                (owner, kind, encoded),
            ).fetchone()
            if found:
                return found[0]
        db.execute(
            "INSERT INTO agent_assets VALUES(?,?,?,?,?)",
            (key, owner, kind, encoded, time.time()),
        )
    return key


def asset_data(owner, key, kind):
    with platform_db.database() as db:
        row = db.execute(
            "SELECT data FROM agent_assets WHERE id=? AND owner=? AND kind=?",
            (key, owner, kind),
        ).fetchone()
    if not row:
        raise HTTPException(404, "附件或证据不存在")
    return json.loads(row[0])


def grant(actor):
    return dict(
        name=actor,
        role="管理员",
        department="",
        phone=dict.fromkeys(("audio", "numbers", "quality", "export"), True),
    )


def safe_result(value):
    if isinstance(value, list):
        return [safe_result(v) for v in value]
    if isinstance(value, dict):
        return {
            k: safe_result(v)
            for k, v in value.items()
            if k.lower()
            not in {
                "token",
                "secret",
                "webhook",
                "password",
                "url",
                "appsecret",
                "accesskey",
                "appid",
                "storage",
                "stored",
                "folder",
            }
        }
    if isinstance(value, str):
        return re.sub(
            r"(?:rtsp://|https://qyapi\.weixin\.qq\.com/)[^\s]+",
            "[连接地址已隐藏]",
            value,
            flags=re.I,
        )
    return value


async def dispatch(app, actor, tool, args):
    path = resolved_path(tool, args)
    query = {
        k: v for k, v in args.items() if k != "body" and "{" + k + "}" not in tool.path
    }

    async def business(scope, receive, send):
        scope.update(app=app)
        scope["hotel.agent_actor"] = grant(actor)
        await app(scope, receive, send)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=business), base_url="http://localhost"
    ) as client:
        body = args.get("body")
        if tool.form:
            fields = dict(body)
            selected = asset_data(actor, fields.pop("file"), "upload")
            with Path(selected["path"]).open("rb") as stream:
                response = await client.request(
                    tool.method,
                    path,
                    params=query,
                    data=fields,
                    files={"file": (selected["name"], stream)},
                )
        else:
            response = await client.request(tool.method, path, params=query, json=body)
    if response.is_error:
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = None
        raise HTTPException(
            response.status_code,
            safe_result(detail)
            if isinstance(detail, str)
            else "业务参数无效或目标状态已变更",
        )
    return safe_result(response.json())


class LocalMedia(Response):
    """Delegate byte ranges and content disposition directly to the original media handler."""

    def __init__(self, app, actor, tool, args):
        super().__init__()
        self.app, self.actor, self.tool, self.args = app, actor, tool, args

    async def __call__(self, scope, receive, send):
        if self.tool.name in ("vision_export_json", "recording_export_html"):
            tools = catalog(self.app)
            if self.tool.name == "vision_export_json":
                report = await dispatch(
                    self.app, self.actor, tools["vision_report"], self.args
                )
                response = Response(
                    dump(report),
                    media_type="application/json",
                    headers={
                        "Content-Disposition": 'attachment; filename="inspection-report.json"'
                    },
                )
            else:
                item = await dispatch(
                    self.app, self.actor, tools["recording_export"], self.args
                )
                if not item.get("report"):
                    raise HTTPException(409, "分析报告尚未生成")
                response = Response(
                    recording_html(item),
                    media_type="text/html",
                    headers={
                        "Content-Disposition": 'attachment; filename="recording-report.html"',
                        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'",
                    },
                )
            return await response(scope, receive, send)
        path = resolved_path(self.tool, self.args)
        params = {
            k: v for k, v in self.args.items() if "{" + k + "}" not in self.tool.path
        }
        scope = {
            **scope,
            "path": path,
            "raw_path": path.encode(),
            "root_path": "",
            "query_string": urlencode(params).encode(),
            "app": self.app,
            "hotel.agent_actor": grant(self.actor),
        }
        await self.app.router(scope, receive, send)


def recording_html(item):
    # Keep the same archival projection as the normal report export.
    labels = {
        "scene": "服务场景",
        "summary": "分析结论",
        "scope": "取样范围",
        "checks": "服务检查",
        "todos": "待办",
        "risks": "关注事项",
        "insights": "关键信息",
        "evidence": "依据",
        "quote": "原话",
        "segment": "片段",
        "start": "起始秒",
        "end": "结束秒",
        "detail": "详情",
        "name": "名称",
        "title": "事项",
        "status": "状态",
        "flow": "服务环节",
        "conclusions": "结论依据",
        "coverage": "场景分析明细",
        "owner": "负责人",
        "due": "截止时间",
    }

    def section(value):
        if isinstance(value, dict):
            return (
                "<dl>"
                + "".join(
                    "<dt>"
                    + escape(labels.get(k, k))
                    + "</dt><dd>"
                    + section(v)
                    + "</dd>"
                    for k, v in value.items()
                )
                + "</dl>"
            )
        if isinstance(value, list):
            return (
                "<ol>" + "".join("<li>" + section(v) + "</li>" for v in value) + "</ol>"
            )
        return escape(str(value)) if value is not None else "—"

    title = escape(item.get("filename", "录音报告"))
    html = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>'
        + title
        + "</title><style>body{max-width:800px;margin:3em auto;padding:0 1em;color:#214637;font:16px/1.7 system-ui}dt{font-weight:600}dd{margin:0 0 .8em 1em}h1{border-bottom:2px solid #32664e}</style>"
    )
    return (
        html
        + "<h1>"
        + title
        + "</h1>"
        + section(item["report"])
        + (
            "<h2>原话转写</h2>" + section(item["segments"])
            if item.get("segments")
            else ""
        )
        + "</html>"
    )


def media_ref(actor, name, args, title, detail="", start=0):
    key = asset(actor, "media", dict(name=name, args=args))
    return dict(
        kind=MEDIA[name],
        title=title,
        detail=detail,
        start=start or 0,
        url="api/agent/evidence/" + key,
        tool=name,
    )


def business_day(args):
    from local import server, vision_api

    try:
        day = date.fromisoformat(args["day"]).isoformat()
    except ValueError:
        raise HTTPException(422, "日期请使用 YYYY-MM-DD") from None
    module = args["module"]
    test = args.get("test", False)
    missing = 0
    if module == "语音":
        with server.connection() as db:
            items, missing = badge_scripts.selected_records(
                db, day, include_history=True, test=test
            )
            rows = [
                dict(
                    id=r["id"],
                    title=r.get("filename"),
                    employee=badge_scripts.employee_for(db, r, day) or "佩戴者待确认",
                    time=r.get("recordedAt"),
                    status=r.get("status"),
                    scene=(r.get("report") or {}).get("scene"),
                )
                for r in items
            ]
    elif module == "电话":
        with platform_db.database() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT id,filename AS title,started AS time,status,room FROM calls WHERE test=? AND substr(started,1,10)=? ORDER BY started,id",
                    (test, day),
                )
            ]
    else:
        with vision_api.report_index() as db:
            rows = []
            for key, title, created, state, scene in db.execute(
                "SELECT id,filename,created,state,scene FROM jobs WHERE date(created,'+8 hours')=? ORDER BY created,id",
                (day,),
            ):
                try:
                    report = vision_api.detail(key)
                except HTTPException:
                    missing += 1
                    continue
                if not report.get("cameraId"):
                    missing += 1  # An uploaded clip has no verified capture date.
                    continue
                if bool(report.get("test")) == test:
                    rows.append(
                        dict(
                            id=key,
                            title=title,
                            time=created,
                            status=state,
                            scene=scene,
                            camera=report["cameraId"],
                        )
                    )
    offset, limit = args.get("offset", 0), args.get("limit", 20)
    return dict(
        day=day,
        module=module,
        test=test,
        total=len(rows),
        unverifiedTime=missing,
        rows=rows[offset : offset + limit],
        nextOffset=offset + limit if offset + limit < len(rows) else None,
    )


def excerpt(item, args):
    mode = args["module"]
    result = dict(id=item["id"], status=item["status"], module=mode)
    if mode == "原话撰写":
        offset, limit = args.get("offset", 0), args.get("limit", 30)
        segments = item.get("segments") or []
        result.update(
            segments=segments[offset : offset + limit],
            nextOffset=offset + limit if offset + limit < len(segments) else None,
        )
    elif mode == "场景识别":
        result.update(
            report=item.get("report"), detectedScenes=item.get("detectedScenes", [])
        )
    else:
        result["quality"] = item.get("quality")
    return result


def evidence(actor, name, args, data):
    refs = []
    if name in ("recording_detail", "recording_module", "phone_detail"):
        phone = name == "phone_detail"
        key = args.get("recording_id") or args.get("id") or args.get("key")
        for seg in (data.get("segments") or [])[:100]:
            if seg.get("text"):
                refs.append(
                    media_ref(
                        actor,
                        "phone_audio" if phone else "recording_audio",
                        {"key" if phone else "id": key},
                        data.get("filename") or "录音原话",
                        seg["text"],
                        seg.get("start"),
                    )
                )
    elif name == "vision_report":
        for group in data.get("groups", []):
            findings = " · ".join(
                f.get("detail") or f.get("name", "") for f in group.get("findings", [])
            )
            for f in group.get("frames", []):
                refs.append(
                    media_ref(
                        actor,
                        "vision_frame",
                        dict(job_id=args["job_id"], name=f["file"]),
                        data.get("filename", "巡检画面"),
                        findings,
                        f.get("seconds"),
                    )
                )
    elif name == "wecom_preview":
        for row in (data.get("evidence") or [])[:60]:
            if row.get("record") and row.get("quote"):
                refs.append(
                    media_ref(
                        actor,
                        "recording_audio",
                        dict(id=row["record"]),
                        row.get("title", "话术原话"),
                        row["quote"],
                        row.get("start"),
                    )
                )
    elif name == "order_list":
        refs = [
            dict(
                kind="order",
                title="工单 #" + str(r["id"]),
                detail=" · ".join(
                    str(r.get(k) or "") for k in ("room", "department", "state", "note")
                ),
                data=r,
            )
            for r in data[:100]
        ]
    return refs


def scrub_input(value):
    # Connection credentials are entered in a dedicated local field (opaque handle).
    if isinstance(value, dict):
        for k, v in value.items():
            if (
                k.lower() in {"secret", "webhook", "url", "password"}
                and v
                and not str(v).startswith("local-secret:")
            ):
                raise HTTPException(422, "请通过本机私密参数输入连接凭据")
            scrub_input(v)
    elif isinstance(value, list):
        for v in value:
            scrub_input(v)


def resolve_secrets(actor, value):
    if isinstance(value, dict):
        result = {}
        for k, v in value.items():
            if (
                k.lower() in {"secret", "webhook", "url", "password"}
                and isinstance(v, str)
                and v.startswith("local-secret:")
            ):
                from local.patrol import protect

                entry = asset_data(actor, v.split(":", 1)[1], "secret")
                result[k] = protect(bytes.fromhex(entry), decrypt=True).decode()
            else:
                result[k] = resolve_secrets(actor, v)
        return result
    if isinstance(value, list):
        return [resolve_secrets(actor, v) for v in value]
    return value


async def execute(app, actor, tool, args, call_id):
    from local import server

    tools = catalog(app)
    name = tool.name
    if name in MEDIA:
        ref = media_ref(actor, name, args, tool.label)
        return {"data": {"resource": ref["url"]}, "evidence": [ref]}
    if name == "business_day":
        data = await run_in_threadpool(business_day, args)
    elif name == "recording_list":
        # The archive index supplies identifiers. Full transcripts are fetched separately by ID.
        limit, offset = args.get("limit", 20), args.get("offset", 0)
        with server.connection() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    """SELECT id,data->>'$.filename' AS filename,data->>'$.employee' AS employee,
                data->>'$.recordedAt' AS recordedAt,data->>'$.report.scene' AS scene,data->>'$.test' AS test,
                data->>'$.status' AS status FROM records
                WHERE instr(coalesce(data->>'$.filename','')||' '||coalesce(data->>'$.employee',''),?)>0
                ORDER BY rowid DESC LIMIT ? OFFSET ?""",
                    (args.get("query", ""), limit + 1, offset),
                )
            ]
        data = {
            "records": rows[:limit],
            "nextOffset": offset + limit if len(rows) > limit else None,
        }
    elif name == "batch_update":
        results = []
        refs = []
        for index, action in enumerate(args["actions"]):
            result = await invoke(
                app,
                actor,
                action["name"],
                action["arguments"],
                call_id + "-" + str(index),
                confirmed=True,
            )
            results.append(
                {k: result[k] for k in ("call", "state", "error") if k in result}
            )
            refs.extend(result.get("evidence", []))
            if result["state"] != "done":
                return {
                    "error": "批量操作已暂停，请核对各项回执。",
                    "data": results,
                    "evidence": refs,
                }
        return {"data": results, "evidence": refs}
    elif name == "order_detail":
        rows = await dispatch(app, actor, tools["order_list"], {})
        row = next((r for r in rows if r["id"] == args["oid"]), None)
        if not row:
            raise HTTPException(404, "工单不存在")
        return {"data": row, "evidence": evidence(actor, "order_list", {}, [row])}
    elif name == "recording_module":
        item = server.record(args["recording_id"])
        data = excerpt(item, args)
        refs = await run_in_threadpool(
            evidence, actor, name, args, {**data, "filename": item.get("filename")}
        )
        return {"data": data, "evidence": refs}
    elif name == "device_position":
        rows = server.badge_sync.directory.rows()["devices"]
        matches = [
            r for r in rows if r["number"] == args["number"] and not r["retired"]
        ]
        if len(matches) != 1:
            raise HTTPException(409, "设备编号未唯一匹配到在用设备，请重新核对")
        row = matches[0]
        body = {k: row[k] for k in ("name", "enabled", "retired", "test")}
        body.update(position=args["position"], revision=args["revision"])
        saved = await dispatch(
            app, actor, tools["device_edit"], dict(device_id=row["id"], body=body)
        )
        updated = next(d for d in saved["devices"] if d["id"] == row["id"])
        data = dict(
            id=row["id"],
            number=row["number"],
            name=row["name"],
            before=row["position"],
            after=updated["position"],
            revision=updated["revision"],
        )
        return {
            "data": data,
            "evidence": [
                dict(
                    kind="change",
                    title=row["name"] or row["number"],
                    detail="设备岗位已更新",
                    data=data,
                )
            ],
        }
    else:
        data = await dispatch(app, actor, tool, resolve_secrets(actor, args))
    return {
        "data": data,
        "evidence": await run_in_threadpool(evidence, actor, name, args, data),
    }


def validate(tool, args):
    errors = iter(Draft202012Validator(tool.schema).iter_errors(args))
    if error := next(errors, None):
        field = ".".join(map(str, error.absolute_path)) or "工具"
        raise HTTPException(
            422, field + " 参数不符合工具定义（" + str(error.validator) + "）"
        )
    scrub_input(args)
    if tool.path:
        try:
            resolved_path(tool, args)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None


def call_state(row):
    value = json.loads(row["result"]) if row["result"] else {}
    return dict(call=row["id"], state=row["state"], **value)


async def finish(app, actor, key, tool, args):
    try:
        payload = await execute(app, actor, tool, args, key)
        state = "failed" if "error" in payload else "done"
        if tool.method != "GET" and not payload.get("evidence"):
            payload["evidence"] = [
                dict(
                    kind="receipt",
                    title=tool.label,
                    detail="业务系统已接受操作",
                    data=safe_result(args),
                )
            ]
    except (HTTPException, ValueError, OSError) as exc:
        payload = {
            "error": str(
                exc.detail if isinstance(exc, HTTPException) else "业务记录或附件不可用"
            )
        }
        state = "failed"
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Agent operation failed: %s", tool.name)
        payload = {"error": "业务操作中断，请核对记录后再试"}
        state = "failed"
    if state == "failed":
        payload.setdefault("evidence", []).append(
            dict(kind="error", title=tool.label, detail=payload["error"])
        )
    with platform_db.database() as db:
        db.execute(
            "UPDATE agent_calls SET state=?,result=? WHERE id=?",
            (state, dump(payload), key),
        )
    return dict(call=key, state=state, **payload)


async def invoke(
    app, actor, name, args, request_key, force_confirm=False, confirmed=False
):
    tool = catalog(app).get(name)
    if not tool:
        raise HTTPException(404, "工具不存在")
    validate(tool, args)
    key = uid()
    if name == "batch_update":
        for action in args["actions"]:
            child = catalog(app).get(action["name"])
            if not child or child.name == "batch_update" or child.method == "GET":
                raise HTTPException(422, "批量清单只能包含单项写操作")
            validate(child, action["arguments"])
    pending = (needs_confirmation(name, args) or force_confirm) and not confirmed
    with platform_db.database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM agent_calls WHERE owner=? AND request_key=?",
            (actor, request_key),
        ).fetchone()
        if row:
            if row["name"] != name or row["arguments"] != dump(args):
                raise HTTPException(409, "重复请求的参数已变化")
            return call_state(row)
        state = "pending" if pending else "running"
        size = (
            len(args["actions"])
            if name == "batch_update"
            else len(args["body"])
            if name == "camera_add"
            else len(args["body"]["devices"])
            if name == "device_register"
            else 1
        )
        scope = {
            "wecom_send": "发送至当前配置的门店管理群",
            "wecom_test": "发送测试消息至门店管理群",
            "recording_delete": "移除录音文件与归档记录",
        }.get(name, tool.label)
        payload = (
            {
                "preview": {
                    "title": tool.label,
                    "count": size,
                    "impact": scope,
                    "arguments": safe_result(args),
                }
            }
            if pending
            else {}
        )
        db.execute(
            "INSERT INTO agent_calls VALUES(?,?,?,?,?,?,?,?)",
            (
                key,
                actor,
                request_key,
                name,
                dump(args),
                state,
                dump(payload),
                time.time(),
            ),
        )
    if pending:
        return dict(state=state, call=key, **payload)
    return await finish(app, actor, key, tool, args)


async def decide(app, actor, key, accept):
    with platform_db.database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM agent_calls WHERE owner=? AND id=?", (actor, key)
        ).fetchone()
        if not row:
            raise HTTPException(404, "未找到操作")
        if row["state"] != "pending":
            return call_state(row)
        db.execute(
            "UPDATE agent_calls SET state=?,result=NULL WHERE id=?",
            ("running" if accept else "cancelled", key),
        )
    if not accept:
        return dict(call=key, state="cancelled")
    return await finish(
        app, actor, key, catalog(app)[row["name"]], json.loads(row["arguments"])
    )
