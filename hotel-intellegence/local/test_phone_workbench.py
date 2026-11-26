"""Isolated regression checks for call evidence, operations and PBX delivery."""

from contextlib import closing
import csv
import io
import json
import shutil
import sqlite3
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
import wave

from local import test_platform as fixtures
from local import phone, platform_db
from local.phone_analysis import analyze, save_analysis
from local.phone_audio import quality, transcribe_channels
from local.phone_feed import receive, compare_cdr


class WorkbenchTests(unittest.TestCase):
    setUp = fixtures.PlatformTests.setUp
    login = fixtures.PlatformTests.login

    def set_call(self, key, text, test=True):
        started = datetime.now().replace(microsecond=0).isoformat()
        key = phone.ingest(
            io.BytesIO(fixtures.wav()),
            "example.wav",
            "13900001234",
            "8000",
            started,
            test,
            "模拟交换机" if test else "交换机目录",
            key,
        )["id"]
        seg = [dict(id=0, text=text, start=0, end=1, speaker="未区分")]
        with platform_db.database() as db:
            save_analysis(db, key, seg, analyze(seg, False))
            db.execute(
                "UPDATE calls SET status='已转写',segments=?,room='8206' WHERE id=?",
                (json.dumps(seg, ensure_ascii=False), key),
            )
        return key

    def test_review_grants_register_and_export(self):
        key = self.set_call("x", "我要投诉，送两条浴巾。我的号码13900001234")
        self.set_call("p", "请送浴巾", False)
        path = "/api/phone/calls/" + key
        data = self.front.get(path).json()
        self.assertNotIn("13900001234", json.dumps(data, ensure_ascii=False))
        self.assertEqual(
            self.front.get(
                "/api/phone/register", params=dict(q="浴巾", scope="测试")
            ).json()["total"],
            1,
        )
        qa = self.login("qc", "质检员")
        self.assertEqual(qa.get(path).status_code, 403)
        with platform_db.database() as db:
            db.execute("INSERT INTO phone_permissions VALUES('qc',1,0,0,0)")
        self.assertEqual(qa.get(path).status_code, 200)
        for forbidden in (path + "/audio", path + "/export", "/api/phone/orders"):
            self.assertEqual(qa.get(forbidden).status_code, 403)
        self.assertEqual(qa.post("/api/phone/items/1/dismiss").status_code, 403)
        self.assertEqual(
            self.front.put(path + "/favorite?enabled=true").status_code, 200
        )
        self.assertEqual(
            self.front.get("/api/phone/register?scope=测试&favorites=true").json()[
                "total"
            ],
            1,
        )
        body = dict(
            revision=data["revision"],
            category="客诉",
            summary="已核实",
            level="A",
            state="已确认",
            room="8206",
            note="回听双方",
            segments=[],
        )
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda _: self.front.post(path + "/review", json=body).status_code,
                    range(2),
                )
            )
        self.assertEqual(sorted(results), [200, 409])
        self.front.post(path + "/read")
        first = self.front.get(path).json()["alert"]["first_read"]
        self.admin.post(path + "/read")
        self.assertEqual(self.front.get(path).json()["alert"]["first_read"], first)
        args = dict(test="true", metric="确认客诉率")
        metrics = self.front.get("/api/phone/reports?test=true").json()["metrics"]
        self.assertEqual(metrics["确认客诉率"]["value"], 100)
        self.assertEqual(metrics["预警查看率"]["value"], 100)
        rows = self.front.get("/api/phone/reports", params=args).json()["rows"]
        self.assertEqual([r["id"] for r in rows], [key])
        exported = self.admin.get("/api/phone/reports/export", params=args)
        self.assertIn(key, exported.text)
        self.assertNotIn("13900001234", exported.text)
        self.assertEqual(self.front.get("/api/phone/reports/export").status_code, 403)
        self.assertIsNone(
            self.front.get("/api/phone/reports").json()["metrics"]["一次解决率"][
                "value"
            ]
        )

    def test_repeat_link_new_analysis_and_history(self):
        key = self.set_call("a", "空调不制冷，再送两条浴巾")
        repeat = self.set_call("b", "昨晚报过，空调不制冷")
        base = "/api/phone/calls/"
        d = self.front.get(base + key).json()
        repair = next(i for i in d["items"] if i["department"] == "工程部")
        oid = self.front.post(
            f"/api/phone/items/{repair['id']}/dispatch",
            json=dict(title="空调检修", room="8206", department="工程部", minutes=30),
        ).json()["id"]
        other = next(
            i
            for i in self.front.get(base + repeat).json()["items"]
            if i["department"] == "工程部"
        )
        link = f"/api/phone/items/{other['id']}/link"
        self.assertEqual(
            self.front.post(link, json=dict(order=oid, note="同一次报修")).status_code,
            200,
        )
        self.assertEqual(
            self.front.post(link, json=dict(order=oid, note="重复提交")).status_code,
            409,
        )
        self.assertIn(
            oid, [i["orderId"] for i in self.front.get(base + repeat).json()["items"]]
        )
        self.assertEqual(
            self.front.post(
                base + key + "/reanalyze", json=dict(revision=d["revision"])
            ).status_code,
            200,
        )
        self.assertEqual(len(self.front.get("/api/phone/orders").json()), 1)
        self.assertEqual(
            self.front.get(base + key + "/versions/1").json()["segments"][0]["text"],
            "空调不制冷，再送两条浴巾",
        )
        self.assertEqual(
            self.front.get("/api/phone/register?scope=测试&repeat=true").json()[
                "total"
            ],
            1,
        )

    def test_late_audio_two_legs_and_independent_cdr(self):
        root = self.root / "inbox"
        root.mkdir()
        stamp = datetime.now().isoformat()
        data = dict(
            call_id="shared",
            source_id="pbx",
            file="audio.wav",
            bytes=len(fixtures.wav()),
            started=stamp,
            caller="8100",
            callee="8206",
        )
        for leg in ("one", "two", "again"):
            (root / (leg + ".ready.json")).write_text(
                json.dumps({**data, "segment_id": "two" if leg == "again" else leg})
            )
        receive(root, test=True)
        self.assertEqual(len(list(root.glob("*.ready.json"))), 3)
        (root / "audio.wav").write_bytes(fixtures.wav()[:20])
        receive(root, test=True)
        self.assertEqual(len(list(root.glob("*.ready.json"))), 3)
        (root / "audio.wav").write_bytes(fixtures.wav())
        receive(root, test=True)
        self.assertEqual(
            self.front.get("/api/phone/register?scope=测试").json()["total"], 2
        )
        self.assertEqual(self.front.get("/api/phone/register").json()["total"], 0)
        data = io.StringIO()
        w = csv.writer(data)
        w.writerow(
            [
                "call_id",
                "caller",
                "callee",
                "source_id",
                "segment_id",
                "disposition",
                "started",
            ]
        )
        for key, leg, callee, state in (
            ("shared", "one", "8206", "ANSWERED"),
            ("shared", "two", "9100", "ANSWERED"),
            ("shared", "one", "8206", "ANSWERED"),
            ("m", "", "8206", "NO ANSWER"),
            ("late", "", "8206", "ANSWERED"),
        ):
            w.writerow([key, "8100", callee, "pbx", leg, state, stamp])
        counts = compare_cdr(io.StringIO(data.getvalue()), True)
        self.assertEqual(
            [
                counts[k]
                for k in ("已匹配", "号码不匹配", "明细重复", "未接来电", "缺少录音")
            ],
            [1, 1, 1, 1, 1],
        )
        admin = self.login("localadmin", "管理员")
        resp = admin.post(
            "/api/phone/reconcile",
            files={"file": ("cdr.csv", data.getvalue())},
            data={"test": "true"},
        )
        self.assertEqual(resp.json(), counts)
        (root / "bad.ready.json").write_text(json.dumps({"file": "../outside.wav"}))
        receive(root, test=True)
        self.assertTrue((root / "bad.ready.rejected").is_file())

    def test_audio_channel_mapping_and_silence(self):
        import numpy as np

        data = np.array(np.sin(np.arange(16000) * 0.1) * 10000, dtype="<i2")
        path = self.root / "stereo.wav"

        def write(right):
            with wave.open(str(path), "wb") as f:
                f.setparams((2, 2, 16000, 0, "NONE", ""))
                f.writeframes(np.column_stack((data, right)).astype("<i2").tobytes())

        write(data * 0)
        self.assertTrue(quality(path))
        write(data)
        rows, overlap = transcribe_channels(
            path,
            ["对方", "前台"],
            lambda *a: dict(segments=[dict(start=0, end=0.9, text="测试")]),
            None,
        )
        self.assertTrue(overlap)
        self.assertEqual([r["speaker"] for r in rows], ["对方", "前台"])
        neg = analyze(
            [dict(id=0, start=0, end=1, text="空调没坏。之前坏了，现在好了。请送牙刷")],
            False,
        )
        self.assertEqual([f["title"] for f in neg["findings"]], ["送物"])

    def test_closed_observation_and_overdue_denominators(self):
        from local.phone_reports import cohort, measure
        from datetime import date

        started = datetime(2025, 8, 10, 23, 55).timestamp()
        now = started + 2 * 86400
        key = self.set_call("base", "空调不制冷，再送两条浴巾")
        other = self.set_call("repeat", "空调还没修")
        with platform_db.database() as db:
            db.execute(
                "UPDATE calls SET started=? WHERE id=?", ("2025-08-10T23:55:00", key)
            )
            db.execute(
                "UPDATE calls SET started=? WHERE id=?", ("2025-08-11T00:20:00", other)
            )
            items = db.execute(
                "SELECT id,title FROM items WHERE call_id=? ORDER BY id", (key,)
            ).fetchall()
            for j, item in enumerate(items):
                oid = db.execute(
                    "INSERT INTO orders(item,title,room,department,state,due) VALUES(?,?,'8206','前台','已关闭',?)",
                    (item["id"], item["title"], started + 600),
                ).lastrowid
                db.execute("UPDATE items SET state='已派单' WHERE id=?", (item["id"],))
                for state, delay in (
                    ("待接单", 0),
                    ("处理中", 60),
                    ("待复核", 300 + 600 * j),
                    ("已关闭", 1200),
                ):
                    db.execute(
                        "INSERT INTO history(order_id,actor,state,note,at) VALUES(?,'前台',?,'',?)",
                        (oid, state, started + delay),
                    )
                if j == 0:
                    linked = db.execute(
                        "SELECT id FROM items WHERE call_id=? LIMIT 1", (other,)
                    ).fetchone()[0]
                    db.execute(
                        "INSERT INTO phone_order_links VALUES(?,?,?,'前台')",
                        (linked, oid, started + 1500),
                    )
            results = cohort(db, date(2025, 8, 10), date(2025, 8, 10), True, now)
            for metric, expected in (
                ("一次解决率", 50),
                ("工单超时率", 50),
                ("平均服务时长", 600),
            ):
                self.assertEqual(measure(metric, results[metric])["value"], expected)
                self.assertEqual(len(results[metric]), 2)
            fresh = cohort(
                db, date(2025, 8, 10), date(2025, 8, 10), True, started + 3600
            )
            self.assertEqual(len(fresh["观察中需求"]), 2)
            self.assertFalse(fresh["一次解决率"])

    def test_silent_file_can_be_retried_without_false_analysis(self):
        src = io.BytesIO()
        with wave.open(src, "wb") as f:
            f.setparams((1, 2, 8000, 0, "NONE", ""))
            f.writeframes(bytes(16000))
        src.seek(0)
        key = phone.ingest(src, "quiet.wav", "0", "106", "2026-11-25T10:00", True)["id"]
        model = self.root / "models/sensevoice/model.int8.onnx"
        model.parent.mkdir(parents=True)
        model.touch()
        with patch.object(phone, "ROOT", self.root), patch(
            "local.speech.recognize_turns"
        ) as recognizer:
            self.assertTrue(phone.process_next())
            recognizer.assert_not_called()
        d = phone.detail(key)
        self.assertEqual(d["status"], "失败")
        self.assertIn("静音", d["error"])
        self.assertIsNone(d["analysis"])
        self.assertEqual(
            self.front.post("/api/phone/calls/" + key + "/retry").status_code, 200
        )
        self.assertEqual(
            self.front.post("/api/phone/calls/" + key + "/retry").status_code, 409
        )

    def test_v1_snapshot_upgrade_and_restore(self):
        root = self.root / "old"
        root.mkdir()
        old = root / "platform.sqlite3"
        migration = (
            Path(platform_db.__file__).with_name("migrations") / "001_platform.sql"
        )
        with closing(sqlite3.connect(old)) as db, db:
            db.executescript(migration.read_text(encoding="utf-8"))
            db.execute(
                "INSERT INTO calls(id,source,external_id,caller,callee,started,room,direction,filename,stored,duration,test,status,segments) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "old",
                    "手工导入",
                    "id",
                    "100",
                    "200",
                    "2025-11-24T23:59:59",
                    "8206",
                    "内线",
                    "old.wav",
                    "old.wav",
                    1,
                    1,
                    "已转写",
                    json.dumps([dict(id=0, start=0, end=1, text="请送浴巾")]),
                ),
            )
        (root / "old.wav").write_bytes(fixtures.wav())
        backup = self.root / "backup"
        shutil.copytree(root, backup)
        with patch.object(platform_db, "DB", old), patch.object(phone, "MEDIA", root):
            platform_db.migrate()
            self.assertEqual(phone.detail("old")["category"], "")
            self.assertTrue(phone.audio("old").path.is_file())
            with platform_db.database() as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 4)
                self.assertEqual(
                    db.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                )
        restored = self.root / "restored"
        shutil.copytree(backup, restored)
        with closing(sqlite3.connect(restored / "platform.sqlite3")) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(
                db.execute("SELECT stored FROM calls").fetchone()[0], "old.wav"
            )
        self.assertEqual((restored / "old.wav").read_bytes(), fixtures.wav())
