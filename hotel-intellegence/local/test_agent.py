"""Isolated agent contract and HTTP end-to-end tests using synthetic hotel records."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

DATA = tempfile.TemporaryDirectory()
os.environ["HOTEL_VOICE_DATA"] = DATA.name
import httpx
from local import access, agent_api, badge_archive, platform_db, server, vision_api
from local.agent_catalog import catalog
from local.agent_tools import uid


class AgentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for obj, field, value in [
            (platform_db, "DB", root / "platform.sqlite3"),
            (server, "DB", root / "recordings.db"),
            (server, "AUDIO", root),
            (vision_api, "INDEX", root / "vision.db"),
            (vision_api, "STORE", root),
            (access, "STORE", True),
            (access, "ORIGIN", "https://store.test"),
        ]:
            p = patch.object(obj, field, value)
            p.start()
            self.addCleanup(p.stop)
        platform_db.migrate()
        with server.connection() as db:
            db.execute("CREATE TABLE records(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            badge_archive.migrate(db)
            db.execute(
                "INSERT INTO badge_sync_config(id,app,secret,devices,test,enabled) VALUES(1,'test',X'00','[]',1,0)"
            )
            db.execute(
                "INSERT INTO badge_devices(app,number,source,name,position,test) VALUES('test','TEST-01','挂牌','测试一号','客房',1)"
            )
        with vision_api.report_index() as db:
            db.execute(
                "CREATE TABLE jobs(id TEXT PRIMARY KEY,filename,created,scene,state,review)"
            )
        self.client = await self.login("测试客房", "部门员工", "客房部")
        self.other = await self.login("测试管理员", "管理员", "")
        self.root = root

    async def login(self, name, role, dept):
        access.set_user(name, "test-agent-secret", role, dept)
        c = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app),
            base_url=access.ORIGIN,
            headers={"Origin": access.ORIGIN},
        )
        self.addAsyncCleanup(c.aclose)
        (
            await c.post(
                "/api/session/login",
                json={"name": name, "password": "test-agent-secret"},
            )
        ).raise_for_status()
        return c

    async def call(self, name, args=None, key=None):
        response = await self.client.post(
            "/api/agent/tools/call",
            json={"name": name, "arguments": args or {}, "requestKey": key or uid()},
        )
        response.raise_for_status()
        return response.json()

    async def test_coverage_and_device_access(self):
        public = await self.client.get("/api/agent/tools")
        self.assertEqual(public.status_code, 200)
        routes = {
            (t.path, t.method.lower()) for t in catalog(server.app).values() if t.path
        }
        actual = {
            (p, m)
            for p, ops in server.app.openapi()["paths"].items()
            if p.startswith("/api/")
            and not p.startswith(("/api/agent/", "/api/session"))
            and p != "/api/ping"
            for m in ops
        }
        self.assertEqual(routes, actual)
        self.assertEqual((await self.client.get("/api/badge/devices")).status_code, 403)
        device = (await self.call("device_list"))["data"]["devices"][0]
        args = dict(
            number=device["number"], position="前台", revision=device["revision"]
        )
        out = await self.call("device_position", args, "device-request")
        self.assertEqual(out["state"], "done", out)
        self.assertEqual(out["data"]["before"], "客房")
        self.assertEqual(
            (await self.call("device_position", args, "device-request")), out
        )
        self.assertEqual((await self.call("device_position", args))["state"], "failed")
        saved = (await self.other.get("/api/badge/devices")).json()["devices"][0]
        self.assertEqual((saved["position"], saved["test"]), ("前台", 1))
        with platform_db.database() as db:
            self.assertEqual(
                db.execute(
                    "SELECT owner FROM agent_calls WHERE id=?", (out["call"],)
                ).fetchone()[0],
                "测试客房",
            )
        self.assertNotIn("token", (await self.call("system_status"))["data"])
        self.assertEqual(
            (await self.other.get("/api/agent/calls/" + out["call"])).status_code, 404
        )

    def recording(self, key, when, test=True):
        row = dict(
            id=key,
            status="done",
            filename="合成测试录音.wav",
            extension=".wav",
            source="挂牌",
            test=test,
            recordedAt=when,
            createdAt="2026-11-28T12:00:00Z",
            employee="测试员工",
            segments=[dict(id=1, start=2, end=4, text="您好，请问需要什么帮助？")],
        )
        server.save(row)
        return row

    async def test_day_evidence_and_owner_isolation(self):
        self.recording("sample", "2026-11-24T16:15:00Z")
        self.recording("previous", "2026-11-24T15:55:00Z")
        args = {"module": "语音", "day": "2026-11-25"}
        self.assertEqual((await self.call("business_day", args))["data"]["total"], 0)
        self.assertEqual(
            (await self.call("business_day", {**args, "test": True}))["data"]["rows"][
                0
            ]["id"],
            "sample",
        )
        out = await self.call(
            "recording_module", dict(recording_id="sample", module="原话撰写")
        )
        self.assertEqual(out["evidence"][0]["start"], 2)
        path = "/" + out["evidence"][0]["url"]
        self.assertEqual((await self.client.get(path)).status_code, 404)
        (self.root / "sample.wav").write_bytes(b"synthetic fixture audio")
        self.assertEqual(
            (await self.client.get(path, headers={"Range": "bytes=0-8"})).content,
            b"synthetic",
        )
        self.assertEqual((await self.other.get(path)).status_code, 404)
        self.assertEqual(
            (await self.client.get("/api/records/sample/audio")).status_code, 403
        )
        job = uid()
        folder = self.root / job
        folder.mkdir()
        vision_api.save(
            folder,
            dict(
                id=job,
                filename="测试点位",
                created="2026-11-24T16:01:00Z",
                cameraId="cam1",
                scene="公区卫生",
                test=True,
                state="已完成",
                review="待复核",
                groups=[
                    {
                        "frames": [{"file": "frame.jpg", "seconds": 1}],
                        "findings": [{"detail": "测试画面已取样"}],
                    }
                ],
            ),
        )
        self.assertEqual(
            (
                await self.call(
                    "business_day",
                    {"module": "摄像头", "day": "2026-11-25", "test": True},
                )
            )["data"]["total"],
            1,
        )
        vision = await self.call("vision_report", dict(job_id=job))
        self.assertEqual(vision["evidence"][0]["kind"], "image")

    async def test_confirmation_and_duplicate_delivery(self):
        from local.wecom_delivery import Delivery

        with patch.object(Delivery, "enqueue", return_value=42) as delivery:
            pending = await self.call("wecom_send", dict(body={"day": "2026-11-25"}))
            delivery.assert_not_called()
            key = pending["call"]
            url = "/api/agent/calls/" + key + "/decision"
            self.assertEqual(
                (await self.other.post(url, json={"accept": True})).status_code, 404
            )
            one, two = await asyncio.gather(
                *[self.client.post(url, json={"accept": True}) for _ in range(2)]
            )
            self.assertTrue(
                any(r.json()["state"] == "done" for r in [one, two]),
                [one.json(), two.json()],
            )
            delivery.assert_called_once()
        self.recording("sample", "2026-11-25T10:00")
        p = await self.call("recording_delete", {"id": "sample"})
        url = "/api/agent/calls/" + p["call"] + "/decision"
        self.assertEqual(
            (await self.client.post(url, json={"accept": False})).json()["state"],
            "cancelled",
        )
        self.assertEqual(
            (await self.client.post(url, json={"accept": True})).json()["state"],
            "cancelled",
        )
        self.assertEqual(server.record("sample")["id"], "sample")

    async def test_stream_run_and_reconnect(self):
        result = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "id": "a",
                    "function": {"name": "device_list", "arguments": "{}"},
                }
            ],
        }
        edit = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "type": "function",
                    "id": "b",
                    "function": {
                        "name": "device_position",
                        "arguments": json.dumps(
                            dict(number="TEST-01", revision=1, position="前台"),
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }
        body = dict(text="把 TEST-01 的岗位改为前台", requestKey=uid())
        with patch.object(
            agent_api,
            "model_turn",
            new=AsyncMock(
                side_effect=[
                    result,
                    edit,
                    {"content": "YES"},
                    {"role": "assistant", "content": "岗位已更新。[1]"},
                ]
            ),
        ):
            r = (await self.client.post("/api/agent/runs", json=body)).json()
            ev = await self.client.get("/api/agent/runs/" + r["run"] + "/events")
            self.assertIn("event: finished", ev.text)
            self.assertEqual(
                (await self.client.post("/api/agent/runs", json=body)).json(), r
            )
        row = (await self.client.get("/api/agent/threads/" + r["thread"])).json()[
            "messages"
        ][-1]
        self.assertEqual(row["state"], "done", row)
        self.assertEqual(row["evidence"][0]["data"]["after"], "前台")
        self.assertEqual(
            (await self.other.get("/api/agent/threads/" + r["thread"])).status_code, 404
        )
        self.assertEqual((await self.other.get("/api/agent/threads")).json(), [])

    async def test_confirmed_batch_and_legacy_export(self):
        for key in ("TEST-1", "TEST-2"):
            self.recording(key, "2026-11-25T14:00")
        actions = [
            {"name": "recording_delete", "arguments": {"id": key}}
            for key in ("TEST-1", "TEST-2")
        ]
        preview = await self.call("batch_update", {"actions": actions})
        self.assertEqual(preview["preview"]["count"], 2)
        self.assertEqual(server.record("TEST-1")["status"], "done")
        url = "/api/agent/calls/" + preview["call"] + "/decision"
        self.assertEqual(
            (
                await self.client.post(url, json={"accept": True, "arguments": {}})
            ).status_code,
            422,
        )
        result = (await self.client.post(url, json={"accept": True})).json()
        self.assertEqual(result["state"], "done", result)
        self.assertEqual(
            (await self.client.post(url, json={"accept": True})).json(), result
        )
        self.assertEqual(len(result["data"]), 2)
        r = self.recording("TEST-HTML", "2026-11-25T14:00")
        server.save(
            {
                **r,
                "report": {"summary": "<script>合成原话</script>", "reportVersion": 1},
            }
        )
        export = await self.call("recording_export_html", {"id": r["id"]})
        response = await self.client.get("/" + export["evidence"][0]["url"])
        self.assertEqual(response.status_code, 200)
        self.assertIn("&lt;script&gt;", response.text)
        self.assertIn("attachment", response.headers["Content-Disposition"])

    async def test_selected_file_and_cross_department_order(self):
        from io import BytesIO
        import wave
        from local import phone

        data = BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setparams((1, 2, 8000, 0, "NONE", ""))
            wav.writeframes(bytes(16000))
        with patch.object(phone, "MEDIA", self.root):
            f = (
                await self.client.post(
                    "/api/agent/uploads",
                    files={"file": ("测试通话.wav", data.getvalue())},
                )
            ).json()
            args = {
                "body": {
                    "file": f["id"],
                    "caller": "8001",
                    "callee": "0",
                    "started": "2026-11-25T10:00",
                    "test": True,
                }
            }
            call = await self.call("phone_import", args)
            self.assertEqual(call["state"], "done", call)
            key = call["data"]["id"]
            denied = await self.other.post(
                "/api/agent/tools/call",
                json={"name": "phone_import", "arguments": args, "requestKey": uid()},
            )
            self.assertEqual(denied.json()["state"], "failed")
            await self.call(
                "phone_add_item",
                dict(
                    key=key,
                    body=dict(
                        title="维修", department="工程部", quote="测试报修", start=0
                    ),
                ),
            )
            item = (await self.call("phone_detail", {"key": key}))["data"]["items"][0][
                "id"
            ]
            sent = await self.call(
                "phone_dispatch",
                dict(
                    item=item,
                    body=dict(title="测试报修", department="工程部", room="TEST-01"),
                ),
            )
            self.assertEqual(sent["state"], "done", sent)
            order = await self.call("order_detail", {"oid": sent["data"]["id"]})
            self.assertEqual(order["data"]["department"], "工程部")
            self.assertEqual(order["data"]["history"][0]["actor"], "测试客房")
            self.assertEqual((await self.client.get("/api/phone/orders")).json(), [])

    async def test_failure_and_stop(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            result = (
                await self.client.post(
                    "/api/agent/runs", json={"text": "测试连接", "requestKey": uid()}
                )
            ).json()
            event = await self.client.get(
                "/api/agent/runs/" + result["run"] + "/events"
            )
            self.assertIn("failed", event.text)
        started = asyncio.Event()

        async def wait(*args, **kwargs):
            started.set()
            await asyncio.sleep(5)

        with patch.object(agent_api, "model_turn", side_effect=wait):
            run = (
                await self.client.post(
                    "/api/agent/runs", json={"text": "测试停止", "requestKey": uid()}
                )
            ).json()["run"]
            await started.wait()
            await self.client.post("/api/agent/runs/" + run + "/stop")
            self.assertIn(
                "stopped",
                (await self.client.get("/api/agent/runs/" + run + "/events")).text,
            )
