"""Badge archive regression tests using synthetic text and isolated SQLite files."""

import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory()
os.environ["HOTEL_VOICE_DATA"] = TEMP.name
from fastapi.testclient import TestClient
from local import access, badge_archive, server, platform_db
from local.badge_scenes import enrich
from local.rules import rule_report


def segments(*lines):
    return [
        dict(id=i, start=i * 5, end=i * 5 + 4, text=line)
        for i, line in enumerate(lines)
    ]


def rules(scene, *lines):
    # Old-format archive fixtures exercise review and export compatibility.
    transcript = segments(*lines)
    return enrich(rule_report(transcript, scene), transcript)


class BadgeArchiveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        for key, value in [("DB", root / "records.sqlite3"), ("AUDIO", root)]:
            override = patch.object(server, key, value)
            override.start()
            self.addCleanup(override.stop)
        platform = patch.object(platform_db, "DB", root / "platform.sqlite3")
        platform.start()
        self.addCleanup(platform.stop)
        platform_db.migrate()
        with server.connection() as db:
            db.execute("CREATE TABLE records(id TEXT PRIMARY KEY,data TEXT NOT NULL)")
            db.execute(
                "INSERT INTO records VALUES (?,?)",
                (
                    "legacy",
                    json.dumps(
                        dict(
                            id="legacy",
                            status="done",
                            source="历史导入",
                            reviewed=True,
                            report=dict(todos=[dict(done=True)]),
                        )
                    ),
                ),
            )
            db.commit()
            badge_archive.migrate(db)
            badge_archive.migrate(db)
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 6)
        self.client = TestClient(server.app, headers={"x-local-token": server.TOKEN})
        self.addCleanup(self.client.close)

    def add(self, id, scene, *lines, test=True):
        server.save(
            dict(
                id=id,
                filename=id + ".mp3",
                extension=".mp3",
                status="done",
                source="挂牌",
                test=test,
                reviewed=False,
                report=rules(scene, *lines),
                segments=segments(*lines),
            )
        )

    def edit(self, id, group, index, state, **kw):
        return self.client.patch(
            f"/api/records/{id}/findings/{group}/{index}", json=dict(state=state, **kw)
        )

    def test_legacy_and_store_totals(self):
        self.add("a", "其他", "我是测试人员。")
        self.add("b", "其他", "大家好。", test=False)
        old = self.client.get("/api/records/legacy").json()
        self.assertTrue(old["reviewed"] and old["report"]["todos"][0]["done"])
        self.assertEqual(
            self.client.get("/api/reports").json()["胸牌语音"],
            {"挂牌归档": 2, "历史导入": 1},
        )
        self.assertEqual(self.client.get("/api/overview").json()["badge"], 2)

    def test_device_sync_retries_partial_file_and_suppresses_duplicates(self):
        from io import BytesIO
        import wave
        from unittest.mock import MagicMock
        from local.badge_sync import BadgeSync, Configuration, SyncError

        sync = BadgeSync(server.connection, server.ingest_badge, lambda: None)
        config = Configuration(appId='synthetic', secret='synthetic-secret',
                               devices=[dict(number='TEST-001', source='挂牌')], test=True)
        with patch('local.badge_sync.vendor', side_effect=[{'accessToken':'fixture'}, {'items':[]}]):
            sync.configure(config)
        row = sync.settings()
        self.assertNotIn(b'synthetic-secret', row['secret'])
        out = BytesIO()
        with wave.open(out, 'wb') as wav:
            wav.setparams((1, 2, 8000, 0, 'NONE', ''))
            wav.writeframes(bytes(16000))
        audio = out.getvalue()
        item = dict(filename='test.wav', size=len(audio), url='https://example.invalid/test.wav',
                    startTime='2026-11-25T10:00:00')
        device = config.devices[0].model_dump()
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        with patch('local.badge_sync.vendor', return_value=[item]), patch('local.badge_sync.requests.get', return_value=response) as get:
            response.iter_content.return_value = [audio[:10]]
            with self.assertRaises(SyncError):
                sync.sync_device(row, device, 'synthetic-token')
            self.assertEqual(sync.status()['imported'], 0)
            response.iter_content.return_value = [audio]
            sync.sync_device(row, device, 'synthetic-token')
            sync.sync_device(row, device, 'synthetic-token')
            self.assertEqual(get.call_count, 2)
            saved = next(r for r in server.all_records() if r.get('importedFrom'))
            self.assertFalse(saved['test'])
            self.assertEqual((saved['source'], saved['badge'], saved['recordedAt']),
                             ('挂牌', 'TEST-001', item['startTime']))
            self.assertEqual((server.AUDIO / (saved['id']+'.wav')).read_bytes(), audio)
            response = self.client.patch('/api/records/'+saved['id']+'/metadata',
                json={'filename':'合成片段','employee':'测试员工','note':'临时库'})
            self.assertEqual(response.json()['filename'], '合成片段')
            self.client.delete('/api/records/'+saved['id']).raise_for_status()
            sync.sync_device(row, device, 'synthetic-token')
            self.assertEqual(get.call_count, 2)
            self.assertFalse(any(r.get('importedFrom') for r in server.all_records()))
            self.assertFalse(access.allowed({'role':'质检员'}, '/api/records/example/metadata', 'PATCH'))
            self.assertFalse(access.allowed({'role':'质检员'}, '/api/badge/sync', 'PUT'))

    def test_meeting_decisions_and_owner_due(self):
        r = rules(
            "会议",
            "今天讨论项目预算。",
            "建议采购屏幕，尚未决定。",
            "我们决定周五试运行。",
            "李经理负责联系供应商，周五前提交报价。",
        )
        self.assertEqual(
            [x["value"] for x in r["insights"] if x["label"] == "已决定事项"],
            ["我们决定周五试运行。"],
        )
        self.assertIn("待确认事项：建议采购屏幕", r["summary"])
        self.assertEqual(
            (r["todos"][0]["owner"], r["todos"][0]["due"]), ("李经理", "周五前")
        )

    def test_sales_reply_and_tags(self):
        r = rules(
            "销售沟通",
            "担心停车不方便。",
            "可以提供免费停车。",
            "还担心噪声。",
            "请发给我报价。",
        )
        links = [x for x in r["flow"] if x["name"] == "顾虑跟进"]
        self.assertEqual([len(x["evidence"]) for x in links], [2, 1])
        self.assertTrue(any(x["label"] == "标签建议" for x in r["insights"]))

    def test_profile_review_and_removal(self):
        for id in ["a", "b"]:
            self.add(id, "销售沟通", "需要十桌婚宴。")
        url = "/api/badge/customers"
        person = self.client.post(url, json=dict(name="合成客户", test=True)).json()
        other = self.client.post(url, json=dict(name="营业客户")).json()

        def entries():
            return self.client.get(url + "?test=true").json()[0]["entries"]

        for id in ["a", "b"]:
            link = "/api/records/" + id + "/customer"
            self.assertEqual(
                self.client.patch(link, json=dict(customerId=other["id"])).status_code,
                200,
            )
            self.client.patch(
                link, json=dict(customerId=person["id"])
            ).raise_for_status()
            for _ in range(2):
                self.edit(id, "insights", 0, "确认").raise_for_status()
        self.assertEqual(len(entries()), 2)
        self.edit(
            "a", "insights", 0, "纠正", value="十桌", note="已回听"
        ).raise_for_status()
        data = self.client.get("/api/records/a/export").json()["report"]["insights"][0]
        self.assertEqual(len(data["feedback"]), 2)
        self.assertEqual(data["feedback"][-1]["note"], "已回听")
        self.assertTrue(any(e["value"] == "十桌" for e in entries()))
        # Import the application in a fresh interpreter against the same archive.
        subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from fastapi.testclient import TestClient; from local.server import app; "
                "rows=TestClient(app).get('/api/badge/customers?test=true').json(); "
                "assert len(rows[0]['entries']) == 2 and any(e['value']=='十桌' for e in rows[0]['entries'])",
            ],
            env={**os.environ, "HOTEL_VOICE_DATA": str(server.DB.parent)},
            check=True,
            timeout=30,
            capture_output=True,
        )

        self.edit("a", "insights", 0, "撤回").raise_for_status()
        self.assertEqual(len(entries()), 1)
        self.client.delete("/api/records/b").raise_for_status()
        self.assertEqual(entries(), [])

    def test_complaint_and_denial(self):
        self.add(
            "a", "餐厅服务", "我不需要投诉。", "我很着急，我要投诉。", "我去厨房催。"
        )
        r = server.record("a")["report"]
        alerts = [(i, row) for i, row in enumerate(r["risks"]) if row.get("complaint")]
        self.assertEqual(len(alerts), 1)
        i, row = alerts[0]
        self.assertEqual(row["evidence"][0]["segment"], 1)
        self.assertEqual(r["flow"][-1]["status"], "待跟进")
        self.assertTrue(any(x["label"] == "明确表达·焦急" for x in r["insights"]))
        self.assertEqual(
            self.edit("a", "risks", i, "已处理", note="核实").status_code, 409
        )
        self.edit("a", "risks", i, "排除", note="误报").raise_for_status()
        self.edit("a", "risks", i, "确认").raise_for_status()
        self.edit("a", "risks", i, "已处理", note="已反馈").raise_for_status()
        self.assertEqual(
            server.record("a")["report"]["risks"][i]["feedback"][-1]["note"], "已反馈"
        )

    def test_historical_copy_and_retry_reuse_transcript(self):
        from local.badge_analysis import AnalysisError
        self.add("a", "销售沟通", "请明天给我报价。")
        server.update("a", reviewed=True)
        original = server.record("a")
        (server.AUDIO / "a.mp3").write_bytes(b"synthetic-audio")
        with patch.object(server.POOL, "submit") as submit:
            path = "/api/records/a/deepseek-copy"
            copy = self.client.post(path).json()
            self.assertEqual(self.client.post(path).json()["id"], copy["id"])
            submit.assert_called_once()
        key = copy["id"]
        with patch.object(server, "transcribe") as asr, patch.object(
            server, "analyze_segments", side_effect=AnalysisError("DeepSeek 请求限流")
        ):
            server.process(key)
            failed = server.record(key)
            self.assertEqual(failed["status"], "error")
            self.assertEqual(failed["segments"], original["segments"])
            asr.assert_not_called()
        with patch.object(server.POOL, "submit") as submit:
            self.client.post(f"/api/records/{key}/analyze").raise_for_status()
            self.assertEqual(self.client.post(f"/api/records/{key}/analyze").status_code, 409)
            submit.assert_called_once()
        with patch.object(server, "transcribe") as asr, patch.object(server, "analyze_segments", return_value={"todos": []}):
            server.process(key)
            asr.assert_not_called()
        self.assertEqual(server.record(key)["status"], "done")
        self.assertEqual(server.record("a"), original)
        self.assertEqual(self.client.get(f"/api/records/{key}/audio").content, b"synthetic-audio")

    def test_old_empty_report_can_be_retried_in_place_once(self):
        self.add("empty", "销售沟通", "我定了一间房。")
        original = server.record("empty")
        empty = dict(mode="noEvidence", scene=None, reportVersion=4, todos=[])
        path = "/api/records/empty/analyze"
        with patch.object(server.POOL, "submit") as submit:
            server.update("empty", report=empty, reviewed=True)
            self.assertEqual(self.client.post(path).status_code,409)
            server.update("empty", reviewed=False, report=dict(empty,todos=[{"done":True}]))
            self.assertEqual(self.client.post(path).status_code,409)
            server.update("empty", report=empty)
            result = self.client.post(path)
            result.raise_for_status()
            self.assertEqual(result.json()["segments"],original["segments"])
            self.assertEqual(result.json()["id"],"empty")
            self.assertEqual(self.client.post(path).status_code,409)
            submit.assert_called_once_with(server.process,"empty")

    def test_scene_is_readable_before_report_finishes(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from local.badge_analysis import FIELDS
        self.add("scene-first","会议","开会讨论培训材料。")
        server.update("scene-first",status="queued",report=None)
        started, finish = Event(), Event()
        def detail(data):
            started.set()
            self.assertTrue(finish.wait(8))
            return [dict(scene="会议",**{k:[] for k in FIELDS})]
        url="/api/records/scene-first"
        with patch("local.badge_privacy.redact_locally",side_effect=lambda _,text:text), patch("local.badge_analysis.classify",return_value=["会议"]), patch("local.badge_analysis.request_reports",side_effect=detail), ThreadPoolExecutor(1) as pool:
            job=pool.submit(server.process,"scene-first")
            try:
                self.assertTrue(started.wait(8))
                record=self.client.get(url).json()
                self.assertEqual((record["status"],record["detectedScenes"],record["report"]),("analyzing",["会议"],None))
            finally:
                finish.set()
            job.result(timeout=8)
        report=self.client.get(url+"/export").json()
        self.assertEqual(report["report"]["scene"],"会议")
        self.assertNotIn("segments",report)
        self.assertNotIn("employee",report)

    def test_retired_scene_migration_preserves_findings_and_recognizes_test(self):
        for key, text in [("canteen","食团场景识别测试。食堂场景识别测试。"),("unknown","你好。")]:
            server.save(dict(id=key, sceneHint="其他", detectedScenes=["其他"], reviewed=True,
                             report=dict(scene="其他",scenes=["其他"],coverage=[],todos=[dict(title="历史待办",done=True)]),
                             segments=segments(text), status="done"))
        with server.connection() as db:
            db.execute("PRAGMA user_version=5")
            badge_archive.migrate(db)
            badge_archive.migrate(db)
        self.assertEqual(server.record("canteen")["report"]["scene"],"餐厅服务")
        old=server.record("unknown")
        self.assertIsNone(old["report"]["scene"])
        self.assertTrue(old["reviewed"] and old["report"]["todos"][0]["done"])

    def test_queue_processes_all_new_uploads_but_not_history(self):
        for i in range(7):
            server.save(dict(id=f"new-{i}", status="uploaded", source=("胸牌","挂牌")[i%2], autoAnalyze=True))
        server.save(dict(id="old-upload", status="uploaded"))
        with patch.object(server, "model_status", return_value={"asr":True}), patch.object(server.POOL, "submit") as submit:
            server.queue_badge_analysis()
            self.assertEqual(submit.call_count, 5)
            for i in range(5):
                server.update(f"new-{i}", status="done")
            server.queue_badge_analysis()
            server.queue_badge_analysis()
            self.assertEqual(submit.call_count, 7)
        self.assertEqual(server.record("old-upload")["status"], "uploaded")

    def test_role_boundary(self):
        with (
            patch.object(access, "STORE", True),
            patch.object(access, "ORIGIN", "http://testserver"),
            patch.object(
                access, "identity", return_value={"role": "前台", "name": "test"}
            ),
        ):
            for path in ("/api/badge/customers", "/api/records/a/findings/checks/0", "/api/records/a/deepseek-copy"):
                self.assertEqual(self.client.get(path).status_code, 403)


if __name__ == "__main__":
    unittest.main()
