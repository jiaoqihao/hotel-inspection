"""Period reports built from the same observations as their drill-down rows and CSV."""

from collections import defaultdict
import csv
from datetime import date, datetime, time as day_time, timedelta
import io
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from local.access import identity
from local.phone_insights import mask_number
from local.platform_db import database

router = APIRouter(prefix="/api/phone/reports")
METRICS = (
    "录音量",
    "分析覆盖率",
    "确认客诉率",
    "待复核",
    "未读高优预警",
    "预警查看率",
    "首次查看时长",
    "一次解决率",
    "工单超时率",
    "平均服务时长",
)
RATES = {"分析覆盖率", "确认客诉率", "预警查看率", "一次解决率", "工单超时率"}
TIMES = {"首次查看时长", "平均服务时长"}


def cohort(db, start, end, test, now):
    records = defaultdict(list)

    def record(metric, row, unit, value):
        records[metric].append(
            dict(
                id=row["id"],
                room=row["room"],
                date=row["started"],
                unit=unit,
                value=value,
            )
        )

    args = (int(test), start.isoformat(), end.isoformat())
    calls = db.execute(
        """SELECT c.id,c.room,c.started,c.revision,c.category,c.review_state,c.status,json_extract(c.meta,'$.confirmed_complaint') AS complaint,
        a.level,a.state FROM calls c LEFT JOIN phone_alerts a ON a.call_id=c.id WHERE test=?
        AND substr(started,1,10) BETWEEN ? AND ? ORDER BY started DESC""",
        args,
    )
    for r in calls:
        record("录音量", r, "通话", 1)
        record("分析覆盖率", r, "通话", int(r["revision"] > 0))
        record("处理状态 · " + r["status"], r, "通话", 1)
        record("日期 · " + r["started"][:10], r, "通话", 1)
        record("时段 · " + r["started"][11:13] + "时", r, "通话", 1)
        record("分类 · " + (r["category"] or "待分析"), r, "通话", 1)
        if r["revision"]:
            confirmed = bool(
                r["complaint"]
                if r["complaint"] is not None
                else r["category"] == "客诉"
            ) and r["review_state"] in ("已确认", "需跟进")
            record("确认客诉率", r, "通话", int(confirmed))
            if r["review_state"] == "待复核":
                record("待复核", r, "通话", 1)
            if confirmed:
                record("等级 · " + (r["level"] or "待定"), r, "确认客诉", 1)
    lo = datetime.combine(start, day_time.min).timestamp()
    hi = datetime.combine(end + timedelta(days=1), day_time.min).timestamp()
    for r in db.execute(
        """SELECT c.id,c.room,c.started,a.* FROM phone_alerts a JOIN calls c ON c.id=a.call_id
                          WHERE c.test=? AND a.created>=? AND a.created<? AND a.state<>'已排除'""",
        (int(test), lo, hi),
    ):
        read = r["first_read"] is not None
        record("预警查看率", r, "预警", int(read))
        if read:
            record("首次查看时长", r, "预警", max(0, r["first_read"] - r["created"]))
        elif r["level"] in ("S", "A"):
            record("未读高优预警", r, "预警", 1)
    orders = db.execute(
        """WITH events AS (
        SELECT order_id,min(at) AS issued,max(CASE WHEN state='已关闭' THEN at END) AS closed,
               max(CASE WHEN state='待复核' THEN at END) AS completed,
               sum(CASE WHEN state='处理中' THEN 1 ELSE 0 END) AS attempts
        FROM history GROUP BY order_id)
        SELECT c.id,c.started,o.room,o.id AS oid,o.state,o.title,i.title AS kind,o.due,e.* FROM orders o
        JOIN items i ON i.id=o.item JOIN calls c ON c.id=i.call_id JOIN events e ON e.order_id=o.id
        WHERE c.test=? AND e.issued>=? AND e.issued<?""",
        (int(test), lo, hi),
    )
    for r in orders:
        unit = "工单 #" + str(r["oid"])
        closed = r["closed"] if r["state"]=="已关闭" else None
        completed = (r["completed"] or closed) if closed is not None else None
        if closed is not None or r["due"] <= now:
            record(
                "工单超时率",
                r,
                unit,
                int((completed if completed is not None else now) > r["due"]),
            )
        if closed is not None:
            record("平均服务时长", r, unit, max(0, completed - r["issued"]))
            if r["kind"] in ("设施报修", "送物", "客房清洁") and not r["room"]:
                record("无法核实需求", r, unit, 1)
            elif now >= closed + 86400 and r["kind"] in (
                "设施报修",
                "送物",
                "客房清洁",
            ):
                linked = db.execute(
                    """SELECT 1 FROM phone_order_links l JOIN items i ON i.id=l.item
                    JOIN calls c ON c.id=i.call_id WHERE l.order_id=? AND
                    substr(c.started,1,19) BETWEEN ? AND ?""",
                    (
                        r["oid"],
                        datetime.fromtimestamp(r["issued"]).isoformat(
                            timespec="seconds"
                        ),
                        datetime.fromtimestamp(closed + 86400).isoformat(
                            timespec="seconds"
                        ),
                    ),
                ).fetchone()
                record("一次解决率", r, unit, int(not linked and r["attempts"] <= 1))
            elif r["kind"] in ("设施报修", "送物", "客房清洁"):
                record("观察中需求", r, unit, 1)
    for r in db.execute(
        """SELECT c.id,c.started,coalesce(nullif(o.room,''),nullif(c.room,''),'房间未知') AS room,i.title FROM calls c JOIN items i ON i.call_id=c.id
         LEFT JOIN phone_order_links l ON l.item=i.id LEFT JOIN orders o ON o.item=i.id OR o.id=l.order_id
         WHERE c.test=? AND substr(c.started,1,10) BETWEEN ? AND ? AND i.state IN ('已派单','已关联')""",
        args,
    ):
        record("问题 · " + r["title"], r, "已确认事项", 1)
        record("房间 · " + r["room"], r, "已确认事项", 1)
    return records


def measure(key, rows):
    n = len(rows)
    total = sum(r["value"] for r in rows)
    value = (
        round(total * 100 / n, 1)
        if key in RATES and n
        else round(total / n, 1) if key in TIMES and n else None
    )
    if key not in RATES | TIMES:
        value = total
    return dict(
        value=value,
        numerator=round(total, 1),
        denominator=n,
        unit="%" if key in RATES else "秒" if key in TIMES else "",
    )


def period(since, until):
    end = until or date.today()
    start = since or end - timedelta(days=6)
    if not 0 <= (end - start).days < 366 or start.year < 1971 or end.year > 2099:
        raise HTTPException(400, "请选择一年内的有效日期区间")
    return start, end


@router.get("")
def report(
    since: date | None = None,
    until: date | None = None,
    test: bool = False,
    metric: str = "",
):
    start, end = period(since, until)
    length = end - start + timedelta(days=1)
    with database() as db:
        db.execute("BEGIN")
        now = time.time()
        current = cohort(db, start, end, test, now)
        if metric:
            return {"rows": current.get(metric, []), "metric": metric}
        prev = cohort(db, start - length, end - length, test, now)
    return dict(
        since=start,
        until=end,
        previousSince=start - length,
        previousUntil=end - length,
        metrics={
            k: {**measure(k, current[k]), "previous": measure(k, prev[k])["value"]}
            for k in METRICS
        },
        groups={k: len(v) for k, v in current.items() if k not in METRICS},
        updated=now,
    )


@router.get("/export")
def export(
    request: Request,
    since: date | None = None,
    until: date | None = None,
    test: bool = False,
    metric: str = "录音量",
):
    start, end = period(since, until)
    with database() as db:
        rows = cohort(db, start, end, test, time.time()).get(metric, [])
        db.execute(
            "INSERT INTO phone_exports VALUES(?,?,?)",
            (identity(request)["name"], time.time(), f"{start}/{end}/{metric}"),
        )
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(["指标", "通话编号", "日期", "房间", "统计单位", "贡献值"])
    for row in rows:
        cells = [metric, row["id"], row["date"], row["room"], row["unit"], row["value"]]
        writer.writerow(
            [
                (
                    "'" + str(v)
                    if str(v).lstrip().startswith(("=", "+", "-", "@"))
                    else mask_number(str(v)) if i == 3 else v
                )
                for i, v in enumerate(cells)
            ]
        )
    return Response(
        stream.getvalue().encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="phone-report.csv"'},
    )
