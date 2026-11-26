"""Synthetic recordings and simulated WeCom receipts; never connect to a group."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from local import access, badge_archive, badge_daily, badge_scripts as scripts, wecom_api
from local.wecom_delivery import Delivery, post_text

DAY = (scripts.local_day() - timedelta(days=1)).isoformat()
RULE = dict(
    scene="餐厅服务",
    title="迎宾问候",
    standard="欢迎光临",
    trigger="客人进店",
    alternatives="欢迎您",
    enabled=True,
    since=DAY,
)
TEST_HOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=synthetic-test-only"


def sample(key="synthetic", **values):
    item = dict(
        id=key,
        status="done",
        employee="未指定员工",
        source="胸牌",
        recordedAt=DAY + "T08:00",
        segments=[dict(id=3, start=2, end=5, text="您好，欢迎光临", speakerId="S1")],
        speakers={"S1": {"name": "测试员工", "source": "manual"}},
        deviceId=1,
        report=dict(
            scene="餐厅服务",
            risks=[dict(title="餐品等待时间较长", evidence=[dict(segment=3)])],
            todos=[dict(title="跟进出餐进度", owner="测试员工", due="待确认", evidence=[dict(segment=3)])],
        ),
    )
    return dict(item, **values)


class WecomTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "test.sqlite3"
        with self.connection() as db:
            db.execute("CREATE TABLE records(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            badge_archive.migrate(db)
            db.execute(
                "INSERT INTO badge_devices(id,app,number,source,test) VALUES(1,'fixture','TEST','胸牌',0)"
            )
        self.worker = Delivery(self.connection, self.edit_record)
        app = FastAPI()
        app.include_router(wecom_api.router(self.worker))
        self.client = TestClient(app, base_url="http://127.0.0.1:8765/api/badge/wecom/")
        self.addCleanup(self.client.close)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def edit_record(self, key):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            item = json.loads(db.execute("SELECT data FROM records WHERE id=?", (key,)).fetchone()[0])
            yield db, item
            db.execute("UPDATE records SET data=? WHERE id=?", (json.dumps(item), key))

    def add(self, item):
        with self.connection() as db:
            db.execute("INSERT OR REPLACE INTO records VALUES(?,?)", (item["id"], json.dumps(item)))

    def test_binding_date_lineage_reviews_and_coverage(self):
        self.client.put("bindings/1", json={"since": DAY, "employee": "测试员工"}).raise_for_status()
        self.client.put(
            "bindings/1", json={"since": scripts.local_day().isoformat(), "employee": "新员工"}
        ).raise_for_status()
        version = self.client.post("rules", json=RULE).json()[0]["version"]
        cached = {str(version): [dict(segment=3, quote="欢迎光临", result="符合")]}
        self.add(sample(scriptChecks=cached))
        self.add(sample("copy", sourceId="synthetic", scriptChecks=cached))
        self.add(sample("testing", test=True))
        self.add(sample("old-test", wasMarkedTest=True))
        self.add(sample("test-copy", sourceId="old-test"))
        self.add(sample("unknown", recordedAt=""))
        self.add(sample("late", recordedAt=DAY + "T16:00:00Z"))
        self.add(sample("failed", status="error"))

        def preview():
            return self.client.get("preview", params={"day": DAY}).json()

        report = preview()
        self.assertEqual((report["count"], report["done"], report["failed"], report["undated"]), (2, 1, 1, 1))
        self.assertEqual(report["people"]["测试员工"]["checks"]["迎宾问候"], {"符合": 1})
        with self.edit_record("copy") as (_, item):
            item["speakers"]["S1"]["name"] = "顾客"
            item["report"]["todos"][0]["done"] = True
            item["report"]["risks"][0]["feedback"] = [dict(state="纠正", value="等候已超时")]
        self.assertEqual(preview()["evidence"][0]["status"], "证据不足")
        self.assertEqual(preview()["tasks"], [])
        self.assertEqual(preview()["events"][0]["title"], "等候已超时")
        self.client.post(
            "reviews/copy", json=dict(version=version, segment=3, status="已确认偏差")
        ).raise_for_status()
        self.assertEqual(preview()["evidence"][0]["status"], "已确认偏差")
        self.add(sample("nextshift", recordedAt=scripts.local_day().isoformat() + "T01:00"))
        self.assertNotIn("新员工", preview()["people"])

    def test_script_grounding_and_versioning(self):
        first = self.client.post("rules", json=RULE).json()[0]
        self.add(sample())
        with (
            patch(
                "local.badge_scripts.request_json",
                return_value=[
                    dict(version=first["version"], ref=3, quote="欢迎光临", result="符合"),
                    dict(version=first["version"], ref=3, quote="猜测", result="疑似偏差"),
                ],
            ) as model,
        ):
            self.worker.prepare(DAY)
            self.worker.prepare(DAY)
            self.assertEqual(model.call_count, 1)
        with self.edit_record("synthetic") as (_, item):
            self.assertEqual(len(item["scriptChecks"][str(first["version"])]), 1)
        self.client.post(
            "rules", json=dict(first, since=scripts.local_day().isoformat(), enabled=False)
        ).raise_for_status()
        self.assertTrue(self.client.get("preview", params={"day": DAY}).json()["evidence"])
        self.assertEqual(self.client.post("rules", json=first).status_code, 409)

    def configure(self, enabled=False):
        resp = self.client.put("settings", json=dict(webhook=TEST_HOOK, clock="00:00", enabled=enabled))
        resp.raise_for_status()
        self.assertNotIn("synthetic-test-only", resp.text + self.client.get("settings").text)

    def test_schedule_catchup_idempotency_and_lost_receipt(self):
        self.configure(True)
        with ThreadPoolExecutor(4) as pool:
            self.assertEqual(len(set(pool.map(lambda _: self.worker.enqueue(DAY), range(8)))), 1)
        self.worker.tick()  # build snapshot
        with patch("local.wecom_delivery.post_text", return_value=("回执未知", "超时", False)) as transport:
            self.worker.tick()
            self.worker.tick()
            self.assertEqual(transport.call_count, 1)
        r = self.client.get("history").json()[0]
        self.assertEqual((r["state"], r["sent"]), ("回执未知", 0))
        self.client.post("history/" + str(r["id"]), json={"action": "已收到"}).raise_for_status()
        self.worker.tick()
        self.assertEqual(self.client.get("history").json()[0]["state"], "已发送")
        self.add(sample("late-arrival"))
        self.assertNotIn("正式录音 1", self.client.get("history/" + str(r["id"])).json()["parts"][0])
        second = self.worker.enqueue(DAY, True, previous=1)
        self.assertEqual(second, self.worker.enqueue(DAY, True, previous=1))
        self.assertNotEqual(r["id"], second)

    def test_multipart_retry_redaction_and_resume(self):
        self.configure()
        with self.connection() as db:
            db.executemany(
                "INSERT INTO badge_wearers VALUES(?,?,?)", [(i, DAY, "测试员工" + str(i)) for i in range(500)]
            )
        self.add(sample())
        rid = self.worker.enqueue(DAY)
        with patch("local.badge_daily.checked_text", return_value="仅脱敏摘要"):
            self.worker.tick()
        parts = self.client.get("history/" + str(rid)).json()["parts"]
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(p.encode()) <= 2048 for p in parts))
        self.assertNotIn("欢迎光临", "".join(parts))
        self.assertNotIn("餐品等待", "".join(parts))
        with patch(
            "local.wecom_delivery.post_text", side_effect=[("已发送", "", False), ("发送失败", "限流", True)]
        ) as post:
            self.worker.tick()
            self.worker.tick()
            self.worker.tick()
            self.assertEqual(post.call_count, 2)
        with self.connection() as db:
            db.execute("UPDATE badge_wecom_runs SET next_try=0")
        with patch("local.wecom_delivery.post_text", return_value=("已发送", "", False)) as post:
            self.worker.tick()
            self.assertEqual(post.call_args.args[1], parts[1])

    def test_webhook_permissions_and_private_text(self):
        invalid = self.client.put(
            "settings", json=dict(webhook="https://example.com/?key=secret", clock="09:00")
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertNotIn("key=secret", invalid.text)
        for role in ("质检员", "前台", "部门员工"):
            self.assertFalse(access.allowed({"role": role}, "/api/badge/wecom/history", "GET"))
        self.assertTrue(access.allowed({"role": "经理"}, "/api/badge/wecom/preview", "GET"))
        self.assertFalse(access.allowed({"role": "经理"}, "/api/badge/wecom/send", "POST"))
        self.assertFalse(badge_daily.checked_text("房号 1234"))
        with patch("local.badge_daily.redact_locally", side_effect=ValueError()):
            self.assertFalse(badge_daily.checked_text("客人张三反馈等候过久"))
        self.configure()
        with self.connection() as db:
            secret = db.execute("SELECT secret FROM badge_wecom_config").fetchone()[0]
        self.assertNotIn(b"synthetic-test-only", secret)
        with patch("local.wecom_delivery.requests.post") as post:
            post.return_value.status_code = 200
            post.return_value.json.return_value = {"errcode": 93000}
            self.assertEqual(post_text(secret, "测试")[0], "发送失败")
            post.return_value.json.return_value = {"errcode": 0}
            self.client.post("test").raise_for_status()
            self.assertNotIn("录音日报", post.call_args.kwargs["json"]["text"]["content"])

    def test_v6_migration_preserves_archive(self):
        with self.connection() as db:
            for name in ("badge_wecom_runs", "badge_wecom_config", "badge_script_versions", "badge_wearers"):
                db.execute("DROP TABLE " + name)
            db.execute("PRAGMA user_version=6")
            old = json.dumps(dict(id="legacy", source="胸牌", report=dict(todos=[dict(done=True)])))
            db.execute("INSERT INTO records VALUES('legacy',?)", (old,))
            db.commit()
            badge_archive.migrate(db)
            badge_archive.migrate(db)
            self.assertEqual(db.execute("SELECT data FROM records").fetchone()[0], old)
            self.assertEqual(
                tuple(db.execute("SELECT clock,enabled,secret FROM badge_wecom_config").fetchone()),
                ("09:00", 0, None),
            )
