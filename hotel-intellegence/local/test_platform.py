"""Synthetic HTTP and migration tests; no hotel network or model downloads."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch
import wave

DATA = tempfile.TemporaryDirectory()
os.environ["HOTEL_VOICE_DATA"] = DATA.name
from fastapi.testclient import TestClient
from local import access, phone, platform_db, server, vision_api
from local.phone_feed import receive, reconcile


def wav():
    out = BytesIO()
    with wave.open(out, "wb") as f:
        f.setparams((1, 2, 8000, 0, "NONE", ""))
        f.writeframes(b"\x00\x08\x00\xf8" * 4000)
    return out.getvalue()


class PlatformTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patchers = [
            patch.object(platform_db, "DB", self.root / "platform.sqlite3"),
            patch.object(phone, "MEDIA", self.root),
            patch.object(server, "DB", self.root / "records.sqlite3"),
            patch.object(server, "AUDIO", self.root),
            patch.object(vision_api, "STORE", self.root),
            patch.object(access, "STORE", True),
            patch.object(access, "ORIGIN", "https://store.test"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        platform_db.migrate()
        with server.connection() as db:
            db.execute("CREATE TABLE records(id TEXT PRIMARY KEY,data TEXT)")
            server.badge_archive.migrate(db)
        self.pw = "test-password-synthetic"
        self.admin = self.login("manager", "经理")
        self.front = self.login("front", "前台")
        self.eng = self.login("eng", "部门员工", "工程部")
        self.house = self.login("house", "部门员工", "客房部")

    def login(self, name, role, dept=""):
        access.set_user(name, self.pw, role, dept)
        c = TestClient(
            server.app,
            base_url="https://store.test",
            headers={"Origin": "https://store.test"},
        )
        self.addCleanup(c.close)
        self.assertEqual(
            c.post(
                "/api/session/login", json={"name": name, "password": self.pw}
            ).status_code,
            200,
        )
        return c

    def test_auto_call_crossing_decode_blocks_keeps_evidence_positions(self):
        from local.audio import decode_blocks
        # A one-second boundary exercises the same streaming path as a long call.
        out = BytesIO()
        with wave.open(out, "wb") as f:
            f.setparams((1, 2, 8000, 0, "NONE", ""))
            f.writeframes(b"\x00\x08\x00\xf8" * 10000)
        out.seek(0)
        key = phone.ingest(out, "pbx.wav", "8206", "8101", "2026-11-25T10:00",
                           False, "交换机目录", "test-long")["id"]
        model = self.root / "models/sensevoice/model.int8.onnx"
        model.parent.mkdir(parents=True)
        model.touch()
        with (
            patch.object(phone, "ROOT", self.root),
            patch.object(phone, "decode_blocks", lambda p: decode_blocks(p, seconds=1)),
            patch("local.speech.recognize_turns", side_effect=lambda block, *args: {
                "segments": [{"text": "送浴巾", "start": 0, "end": len(block) / 16000}]
            }),
        ):
            self.assertTrue(phone.process_next())
        detail = self.front.get("/api/phone/calls/" + key).json()
        self.assertEqual([(s["start"], s["end"]) for s in detail["segments"]],
                         [(0, 1), (1, 2), (2, 2.5)])
        self.assertEqual([s["id"] for s in detail["segments"]], [0, 1, 2])

    def test_calls_orders_permissions_and_conflicts(self):
        admin = self.login("admin", "管理员")
        for ext, kind, label in [("0", "前台", "总台"), ("106", "客房", "8106")]:
            self.assertEqual(
                admin.put(
                    "/api/phone/extensions",
                    json=dict(number=ext, kind=kind, label=label),
                ).status_code,
                200,
            )
        endpoint = "/api/phone/calls"
        args = dict(
            files={"file": ("test.wav", wav())},
            data=dict(caller="106", callee="0", started="2026-11-25T10:00"),
        )
        key = self.front.post(endpoint, **args).json()["id"]
        self.assertTrue(self.front.post(endpoint, **args).json()["duplicate"])
        d = self.front.get(endpoint + "/" + key).json()
        self.assertEqual((d["room"], d["direction"]), ("8106", "客房 → 前台"))
        self.assertEqual(self.eng.get(endpoint + "/" + key + "/audio").status_code, 403)
        self.assertEqual(
            self.front.get(endpoint + "/" + key + "/export").status_code, 403
        )
        self.assertEqual(
            self.admin.get(endpoint + "/" + key + "/export").status_code, 200
        )
        self.assertEqual(self.eng.get("/api/patrol").status_code, 403)
        self.assertEqual(self.front.get("/api/records").status_code, 403)
        self.assertEqual(self.house.get("/api/vision/jobs").status_code, 403)
        self.assertEqual(
            self.front.post(
                endpoint, headers={"Origin": "https://other.invalid"}, **args
            ).status_code,
            403,
        )
        # Exercise the normal worker, substitute only ASR inference with synthetic evidence.
        models = self.root / "models"
        (models / "sensevoice").mkdir(parents=True)
        (models / "sensevoice/model.int8.onnx").touch()
        with (
            patch.object(phone, "ROOT", self.root),
            patch(
                "local.speech.recognize_turns",
                return_value={
                    "segments": [
                        {"text": "8206房间空调不制冷，再送两条浴巾", "start": 0, "end": 1}
                    ]
                },
            ),
        ):
            self.assertTrue(phone.process_next())
        items = self.front.get(endpoint + "/" + key).json()["items"]
        self.assertTrue(self.front.get(endpoint + "/" + key).json()["roomConflict"])
        self.assertEqual(len(items), 2)
        for i in items:
            body = dict(title=i["title"], room="8106", department=i["department"])
            path = "/api/phone/items/%d/dispatch" % i["id"]
            self.assertEqual(self.front.post(path, json=body).status_code, 200)
            self.assertEqual(self.front.post(path, json=body).status_code, 409)
        eng = self.eng.get("/api/phone/orders").json()
        self.assertEqual(len(eng), 1)
        path = "/api/phone/orders/%d" % eng[0]["id"]
        self.assertEqual(
            self.house.post(path, json={"state": "处理中"}).status_code, 404
        )
        with ThreadPoolExecutor(2) as pool:
            codes = list(
                pool.map(
                    lambda _: self.eng.post(path, json={"state": "处理中"}).status_code,
                    range(2),
                )
            )
        self.assertEqual(sorted(codes), [200, 409])
        self.assertEqual(
            self.eng.post(path, json={"state": "待复核", "note": "已检修"}).status_code,
            200,
        )
        self.assertEqual(self.eng.post(path, json={"state": "已关闭"}).status_code, 403)
        self.assertEqual(
            self.front.post(path, json={"state": "已关闭"}).status_code, 200
        )
        self.assertEqual(
            self.house.get("/api/phone/orders").json()[0]["state"], "待接单"
        )
        self.assertEqual(len(self.eng.get("/api/phone/orders").json()[0]["history"]), 4)

    def test_legacy_badge_audio_vision_and_restore(self):
        audio = wav()
        (self.root / "legacy.wav").write_bytes(audio)
        server.save(
            {
                "id": "legacy",
                "filename": "fixture.wav",
                "extension": ".wav",
                "status": "done",
                "reviewed": False,
            }
        )
        self.assertEqual(self.admin.get("/api/records/legacy/audio").content, audio)
        job = "a" * 32
        folder = self.root / job
        folder.mkdir()
        (folder / "report.json").write_text(
            json.dumps(
                {"id": job, "state": "已完成", "review": "已确认", "groups": []}
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            self.admin.get("/api/vision/jobs/" + job).json()["review"], "已确认"
        )
        r = self.admin.post(
            "/api/upload",
            files={"file": ("badge.wav", audio)},
            data={
                "source": "胸牌",
                "badge": "TEST-01",
                "employee": "测试甲",
                "recordedAt": "2026-11-25T10:00",
            },
        ).json()
        self.assertEqual((r["badge"], r["source"]), ("TEST-01", "胸牌"))
        self.assertEqual(
            self.admin.patch(
                "/api/records/legacy/review",
                json={"reviewed": True, "note": "人工核实"},
            ).json()["reviewNote"],
            "人工核实",
        )
        copy = self.root / "restore"
        shutil.copytree(self.root, copy)
        with (
            patch.object(server, "DB", copy / "records.sqlite3"),
            patch.object(server, "AUDIO", copy),
            patch.object(vision_api, "STORE", copy),
        ):
            self.assertEqual(self.admin.get("/api/records/legacy/audio").content, audio)
            self.assertEqual(
                self.admin.get("/api/vision/jobs/" + job).json()["id"], job
            )

    def test_finalized_feed_and_reconciliation(self):
        inbox = self.root / "feed"
        inbox.mkdir()
        (inbox / "one.wav").write_bytes(wav())
        meta = {
            "file": "one.wav",
            "caller": "0",
            "callee": "106",
            "started": "2026-11-25T11:00",
            "call_id": "example-01",
        }
        for name in ["1", "retry"]:
            (inbox / (name + ".ready.json")).write_text(
                json.dumps(meta), encoding="utf-8"
            )
            receive(inbox)
        cdr = inbox / "expected.csv"
        cdr.write_text(
            "call_id,caller,callee\nexample-01,0,106\nmissing,106,0\n", encoding="utf-8"
        )
        self.assertEqual(reconcile(cdr)["缺少录音"], 1)
        self.assertEqual(len(self.front.get("/api/phone/calls").json()), 1)
        (inbox / "bad.ready.json").write_text("{}")
        receive(inbox)
        self.assertTrue((inbox / "bad.ready.rejected").exists())


if __name__ == "__main__":
    unittest.main()
