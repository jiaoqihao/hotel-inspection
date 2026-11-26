"""Duty reminders, per-call reconciliation and shift handover with synthetic calls."""

import unittest

from local import test_phone_workbench as fixtures
from local.phone_desk import sync_alerts


class DeskTests(unittest.TestCase):
    setUp = fixtures.WorkbenchTests.setUp
    login = fixtures.WorkbenchTests.login
    set_call = fixtures.WorkbenchTests.set_call

    def test_station_reminder_escalation_and_ownership(self):
        key = self.set_call("guest", "我要投诉工作人员的态度让我很不满意")
        self.front.post("/api/phone/calls/" + key + "/read")
        path = "/api/phone/duty"
        sync_alerts(1000)
        row = self.admin.get(path + "?test=true").json()["rows"][0]
        self.assertEqual(row["state"], "待接手")
        self.assertEqual(self.admin.get(path).json()["rows"], [])
        sync_alerts(1300)
        sync_alerts(1500)
        row = self.admin.get(path + "?test=true").json()["rows"][0]
        self.assertEqual([e["action"] for e in row["events"]], ["升级提醒", "站内通知"])
        body = dict(action="接手", revision=row["revision"])
        self.assertEqual(self.front.post(path + "/" + key, json=body).status_code, 403)
        self.assertEqual(self.admin.post(path + "/" + key, json=body).status_code, 200)
        self.assertEqual(self.admin.post(path + "/" + key, json=body).status_code, 409)
        row = self.admin.get(path + "?test=true").json()["rows"][0]
        self.assertEqual(row["owner"], "manager")
        for action in ("转交", "接手", "办结"):
            row = self.admin.get(path + "?test=true").json()["rows"][0]
            resp = self.admin.post(
                path + "/" + key,
                json=dict(
                    action=action,
                    revision=row["revision"],
                    recipient="manager",
                    note="当班处理",
                ),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
        self.assertFalse(self.admin.get(path + "?test=true").json()["rows"])

    def test_late_audio_closes_checked_difference(self):
        admin = self.login("super", "管理员")
        uploaded = admin.post(
            "/api/phone/reconcile",
            files={
                "file": ("cdr.csv", "call_id,caller,callee\nlate,13900001234,8000\n")
            },
            data={"test": "true"},
        )
        self.assertEqual(uploaded.status_code, 200)
        path = "/api/phone/ledger?test=true"
        row = admin.get(path).json()["rows"][0]
        self.assertNotIn("13900001234", row["caller"])
        self.assertEqual(row["reference"], "late")
        check = "/api/phone/ledger/" + str(row["id"])
        body = dict(reason=row["reason"], note="等待补送")
        self.assertEqual(admin.post(check, json=body).status_code, 200)
        self.assertEqual(admin.post(check, json=body).status_code, 409)
        self.assertEqual(self.front.get(path).status_code, 403)
        key = self.set_call("late", "请送两条浴巾")
        resolved = admin.get(path + "&status=已销差").json()["rows"][0]
        self.assertEqual((resolved["call_id"], resolved["note"]), (key, "等待补送"))
        self.assertFalse(admin.get("/api/phone/ledger").json()["rows"])

    def test_reopen_and_shift_notes(self):
        key = self.set_call("first", "空调不制冷")
        repeat = self.set_call("repeat", "空调还不制冷，昨天报修过了")

        def item(k):
            return self.front.get("/api/phone/calls/" + k).json()["items"][0]["id"]

        oid = self.front.post(
            f"/api/phone/items/{item(key)}/dispatch",
            json=dict(title="空调检修", room="8206", department="工程部", minutes=30),
        ).json()["id"]
        path = f"/api/phone/orders/{oid}/follow"
        body = dict(
            claim=True,
            revision=0,
            feedback="工程部已查收",
            follow_at="2026-11-25T10:00:00+08:00",
        )
        self.assertEqual(self.house.post(path, json=body).status_code, 404)
        self.assertEqual(self.eng.post(path, json=body).status_code, 200)
        self.assertEqual(self.eng.post(path, json=body).status_code, 409)
        lookup = f"/api/phone/items/{item(repeat)}/suggestions"
        self.assertEqual(self.front.get(lookup + "?room=999").json(), [])
        row = self.front.get(lookup + "?room=8206").json()[0]
        self.assertEqual((row["id"], row["owner"]), (oid, "eng"))
        with fixtures.platform_db.database() as db:
            db.execute("UPDATE orders SET state='已关闭' WHERE id=?", (oid,))
            db.execute(
                "INSERT INTO history(order_id,actor,state,note,at) VALUES(?,'front','已关闭','完成',1)",
                (oid,),
            )
        result = self.front.post(
            f"/api/phone/items/{item(repeat)}/link",
            json=dict(order=oid, reopen=True, note="再次报修同台空调"),
        )
        self.assertEqual(result.status_code, 200)
        row = self.front.get("/api/phone/orders").json()[0]
        self.assertEqual(row["state"], "处理中")
        self.assertIn("已关闭", [e["state"] for e in row["history"]])
