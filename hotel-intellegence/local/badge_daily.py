"""Build the local evidence view and the separate, redacted group message."""

from collections import Counter
from datetime import timedelta
import re

import requests
from local import badge_scripts as scripts
from local.badge_privacy import PRIVATE, PRIVATE_NUMBER, redact_locally, retained_parts


def checked_text(text):
    """Fail closed for old reports, human corrections and unrestricted free-text fields."""
    if not text or PRIVATE.search(text) or PRIVATE_NUMBER.search(text):
        return ""
    try:
        with requests.Session() as session:
            session.trust_env = False
            masked = redact_locally(session, text)
        retained_parts(text, masked)
        if not masked.strip("＊* ，。"):
            return ""
        return " ".join(masked.split())[:220]
    except (ValueError, requests.RequestException):
        return ""


def report(db, day):
    items, undated = scripts.selected_records(db, day)
    roster = {scripts.wearer(db, r[0], day) for r in db.execute("SELECT DISTINCT device FROM badge_wearers")}
    people = {name: {"count": 0, "checks": {}} for name in sorted(roster) if name}
    counts = Counter()
    events, tasks, evidence = [], [], []
    for item in items:
        name = scripts.employee_for(db, item, day)
        person = people.setdefault(name or "佩戴者待确认", {"count": 0, "checks": {}})
        person["count"] += 1
        counts[item["status"]] += 1
        if item["status"] != "done":
            continue
        stamp = scripts.recording_time(item["recordedAt"])
        for rule in scripts.applicable(db, item):
            rows = scripts.findings(item, rule, name)
            for row in rows:
                totals = person["checks"].setdefault(rule["title"], {})
                totals[row["status"]] = totals.get(row["status"], 0) + 1
                evidence.append(
                    dict(
                        **row,
                        record=item["id"],
                        employee=name,
                        version=rule["version"],
                        title=rule["title"],
                        time=stamp.strftime("%H:%M"),
                    )
                )
        source = item.get("report") or {}
        segments = {s["id"]: s for s in item.get("segments", []) if not s.get("overlap")}
        for group in ("risks", "conclusions", "todos"):
            for finding in source.get(group, []):
                ev = next((e for e in finding.get("evidence", []) if e.get("segment") in segments), None)
                if not ev:
                    continue
                history = finding.get("feedback") or [{}]
                state = history[-1].get("state")
                if state == "排除" or group == "todos" and (finding.get("done") or state == "已处理"):
                    continue
                start = segments[ev["segment"]]["start"]
                title = finding.get("title") or finding.get("value") or finding.get("detail")
                if state in ("确认", "纠正", "已处理"):
                    for review in reversed(history):
                        if review.get("state") in ("撤回", "排除"):
                            break
                        if review.get("state") == "纠正":
                            title = review["value"]
                            break
                if not title:
                    continue
                row = dict(
                    title=title,
                    record=item["id"],
                    start=start,
                    time=(stamp + timedelta(seconds=start)).strftime("%H:%M"),
                    state=state if state in ("确认", "纠正", "已处理") else "待复核",
                )
                if group == "todos":
                    tasks.append(
                        dict(
                            **row, owner=finding.get("owner") or "待确认", due=finding.get("due") or "待确认"
                        )
                    )
                else:
                    if state == "已处理" and history[-1].get("note"):
                        row["title"] += " · 处理结果：" + history[-1]["note"]
                    if row not in events:
                        events.append(row)
    return dict(
        day=day,
        count=len(items),
        done=counts["done"],
        failed=counts["error"],
        pending=len(items) - counts["done"] - counts["error"],
        undated=undated,
        people=people,
        events=events,
        tasks=tasks,
        evidence=evidence,
    )


def outbound(snapshot, revision):
    # Compile an allowlisted projection. Local evidence, filenames, IDs and raw audio stay local.
    day = snapshot["day"]
    heading = f"栖鉴 · 录音日报 {day}" + (f"（更新版 {revision}）" if revision > 1 else "")
    lines = [
        f"正式录音 {snapshot['count']} 段，已分析 {snapshot['done']}，待分析 {snapshot['pending']}，失败 {snapshot['failed']}。"
    ]
    if snapshot["undated"]:
        lines.append(f"档案中另有 {snapshot['undated']} 段缺录制时间，日期待核实。")
    cache = {}

    def safe(value, fallback):
        if value not in cache:
            cache[value] = checked_text(value)
        return cache[value] or fallback

    lines.append("重要事件")
    for event in snapshot["events"]:
        lines.append(f"· {event['time']} {safe(event['title'], '事件内容请在本机查看')}（{event['state']}）")
    if not snapshot["events"]:
        lines.append("已分析录音中暂无有据可查的事件。")
    lines.append("员工话术")
    for name, person in snapshot["people"].items():
        # Employee and rule labels are explicitly configured for the management group.
        label = display_label(name)
        if not person["count"]:
            lines.append(f"· {label}：无录音")
            continue
        lines.append(f"· {label}：{person['count']} 段录音")
        for title, counts in person["checks"].items():
            lines.append(
                "  " + display_label(title) + "：" + "、".join(f"{k} {v}" for k, v in counts.items())
            )
        if not person["checks"]:
            lines.append("  暂无适用话术结果")
    if not snapshot["people"]:
        lines.append("当日没有已绑定员工或可归属的录音。")
    lines.append("待解决事项")
    for task in snapshot["tasks"]:
        owner = task["owner"]
        if owner != "待确认" and owner not in snapshot["people"]:
            owner = safe(owner, "已登记，详见本机")
        due = task["due"]
        if due != "待确认":
            due = safe(due, "已登记，详见本机")
        lines.append(
            f"· {task['time']} {safe(task['title'], '事项内容请在本机查看')}（{task['state']}） · 负责人 {display_label(owner)} · 期限 {due}"
        )
    if not snapshot["tasks"]:
        lines.append("已分析录音中暂无未完成待办。")
    # Text messages have a 2048-byte UTF-8 budget. Reserve space for heading and part numbers.
    bodies, body = [], ""
    for line in lines:
        while len(line.encode()) > 1850:
            # One task can contain both a long description and a long deadline.
            if body:
                bodies.append(body)
                body = ""
            piece = line.encode()[:1850].decode(errors="ignore")
            bodies.append(piece)
            line = line[len(piece) :]
        if body and len((body + "\n" + line).encode()) > 1850:
            bodies.append(body)
            body = ""
        body = (body + "\n" + line).strip("\n")
    bodies.append(body)
    return [f"{heading}（{i}/{len(bodies)}）\n{body}" for i, body in enumerate(bodies, 1)]


def display_label(value):
    if PRIVATE.search(value) or PRIVATE_NUMBER.search(value) or re.search(r"https?://|<|>|@", value, re.I):
        return "已隐去"
    return " ".join(value.split())[:80]
