"""Durable WeCom delivery queue. A lost response requires a human receipt check."""

from datetime import datetime, timedelta
import json
import logging
import re
import threading
import time

import requests
from fastapi import HTTPException
from local import badge_daily, badge_scripts
from local.patrol import protect

HOOK = re.compile(r"https://qyapi\.weixin\.qq\.com/cgi-bin/webhook/send\?key=[A-Za-z0-9_-]{8,128}")
ACTIVE = ("待生成", "生成中", "待发送", "发送中", "回执未知", "发送失败", "生成失败")
LOG = logging.getLogger(__name__)


def now_text():
    return datetime.now(badge_scripts.BEIJING).isoformat(timespec="seconds")


def post_text(secret, content):
    try:
        url = protect(secret, decrypt=True).decode()
        if not HOOK.fullmatch(url):
            raise ValueError()
    except Exception:
        return "发送失败", "凭据不可用，请在原服务账户重新保存地址", False
    try:
        response = requests.post(
            url,
            json={"msgtype": "text", "text": {"content": content}},
            timeout=(4, 12),
            allow_redirects=False,
        )
        if response.status_code == 429:
            return "发送失败", "群机器人限流", True
        if response.status_code != 200:
            return "回执未知", f"请核对群消息（HTTP {response.status_code}）", False
        code = response.json()["errcode"]
        if type(code) is not int:
            raise ValueError()
    except requests.ConnectTimeout:
        return "发送失败", "连接群机器人超时", True
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return "回执未知", "未取得有效回执，请先核对群内消息", False
    if code:
        return "发送失败", f"企业微信错误码 {code}", code in (-1, 45009)
    return "已发送", "", False


class Delivery:
    def __init__(self, connection, edit_record):
        self.connection, self.edit_record = connection, edit_record
        self.stop = threading.Event()
        self.destination_lock = threading.Lock()
        self.thread = None

    def start(self):
        with self.connection() as db:
            db.execute("UPDATE badge_wecom_runs SET state='待生成' WHERE state='生成中'")
            db.execute(
                "UPDATE badge_wecom_runs SET state='回执未知',info='服务中断，请核对群内消息' WHERE state='发送中'"
            )
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, name="badge-wecom", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join()

    def run(self):
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                LOG.error("企业微信日报任务稍后重试")
            self.stop.wait(4)

    def enqueue(self, day, update=False, automatic=False, previous=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            cfg = db.execute("SELECT * FROM badge_wecom_config").fetchone()
            if not cfg["secret"] or automatic and not cfg["enabled"]:
                if automatic:
                    return
                raise HTTPException(409, "请先保存企业微信群 Webhook")
            last = db.execute(
                "SELECT * FROM badge_wecom_runs WHERE day=? ORDER BY revision DESC LIMIT 1", (day,)
            ).fetchone()
            if last and (
                not update or last["state"] in ACTIVE or previous is not None and previous != last["revision"]
            ):
                return last["id"]
            version = last["revision"] + 1 if last else 1
            return db.execute(
                """INSERT INTO badge_wecom_runs(day,revision,snapshot,parts,state,updated)
                                 VALUES(?,?,'{}','[]','待生成',?)""",
                (day, version, now_text()),
            ).lastrowid

    def prepare(self, day):
        with self.connection() as db:
            items, _ = badge_scripts.selected_records(db, day)
        errors = 0
        for item in items:
            if self.stop.is_set():
                break
            try:
                badge_scripts.prepare_record(self.connection, self.edit_record, item)
            except HTTPException as exc:
                if exc.status_code != 404:
                    errors += 1
            except (ValueError, RuntimeError, requests.RequestException):
                errors += 1
        return errors

    def tick(self):
        now = datetime.now(badge_scripts.BEIJING)
        with self.connection() as db:
            config = db.execute("SELECT enabled,clock FROM badge_wecom_config").fetchone()
        if config["enabled"] and now.strftime("%H:%M") >= config["clock"]:
            self.enqueue((now.date() - timedelta(days=1)).isoformat(), automatic=True)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT * FROM badge_wecom_runs WHERE state IN ('待发送','待生成')
                             AND next_try<=? ORDER BY id LIMIT 1""",
                (time.time(),),
            ).fetchone()
            if not row:
                return
            building = row["state"] == "待生成"
            db.execute(
                "UPDATE badge_wecom_runs SET state=?,updated=? WHERE id=?",
                ("生成中" if building else "发送中", now_text(), row["id"]),
            )
        if building:
            try:
                self.prepare(row["day"])
                if self.stop.is_set():
                    return
                with self.connection() as db:
                    db.execute("BEGIN")
                    snapshot = badge_daily.report(db, row["day"])
                parts = badge_daily.outbound(snapshot, row["revision"])
                with self.connection() as db:
                    db.execute(
                        """UPDATE badge_wecom_runs SET snapshot=?,parts=?,state='待发送',updated=?
                                  WHERE id=? AND state='生成中'""",
                        (
                            json.dumps(snapshot, ensure_ascii=False),
                            json.dumps(parts, ensure_ascii=False),
                            now_text(),
                            row["id"],
                        ),
                    )
            except Exception:
                with self.connection() as db:
                    db.execute(
                        "UPDATE badge_wecom_runs SET state='生成失败',info='请检查本机分析服务后重试' WHERE id=? AND state='生成中'",
                        (row["id"],),
                    )
            return
        with self.destination_lock:
            with self.connection() as db:
                active = db.execute("SELECT state FROM badge_wecom_runs WHERE id=?", (row["id"],)).fetchone()
                if active[0] != "发送中":
                    return
                secret = db.execute("SELECT secret FROM badge_wecom_config").fetchone()[0]
            parts = json.loads(row["parts"])
            state, info, retry = post_text(secret, parts[row["sent"]])
            sent, attempts, retry_at = row["sent"], row["attempts"], 0
            if state == "已发送":
                sent += 1
                attempts = 0
                if sent < len(parts):
                    state = "待发送"
            elif retry:
                attempts += 1
                if attempts < 4:
                    state, retry_at = "待发送", time.time() + 60 * attempts
            with self.connection() as db:
                db.execute(
                    """UPDATE badge_wecom_runs SET state=?,info=?,sent=?,attempts=?,next_try=?,updated=?
                              WHERE id=?""",
                    (state, info, sent, attempts, retry_at, now_text(), row["id"]),
                )

    def resolve(self, run, action):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM badge_wecom_runs WHERE id=?", (run,)).fetchone()
            if not row:
                raise HTTPException(404, "发送记录不存在")
            sent = row["sent"]
            if action == "已收到" and row["state"] == "回执未知":
                sent += 1
                state = "已发送" if sent == len(json.loads(row["parts"])) else "待发送"
            elif (
                action == "确认未收到"
                and row["state"] == "回执未知"
                or action == "重试"
                and row["state"] == "发送失败"
            ):
                state = "待发送"
            elif action == "重试" and row["state"] == "生成失败":
                state = "待生成"
            else:
                raise HTTPException(409, "发送状态已变化，请刷新")
            db.execute(
                "UPDATE badge_wecom_runs SET sent=?,state=?,info='',attempts=0,next_try=0,updated=? WHERE id=?",
                (sent, state, now_text(), run),
            )
