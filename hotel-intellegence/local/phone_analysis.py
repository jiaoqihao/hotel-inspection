"""Evidence-grounded telephone analysis. Every automatic finding cites a transcript segment."""

import json
import re
import time

import requests

CATEGORIES = ("客诉", "潜在客诉", "客需", "商机", "咨询", "其他")
RULES = (
    (
        "进房或人身安全",
        "客诉",
        "值班经理",
        "S",
        r"陌生人.{0,6}(开|进).{0,3}(门|房)|有人闯入|受伤|被人打|着火",
    ),
    ("服务投诉", "客诉", "值班经理", "A", r"投诉|不满意|太差|差评|找经理|要求赔偿"),
    (
        "重复催办",
        "潜在客诉",
        "值班经理",
        "B",
        r"催一下|又来催|还没(来|送|修|处理)|还没有(来|送|修)|等了.{1,8}(小时|分钟)|昨[天晚].{0,6}(说|报|反映)过",
    ),
    ("噪音异味", "潜在客诉", "客房部", "C", r"太吵|很吵|噪[音声]|异味|烟味|睡不着"),
    (
        "设施报修",
        "客需",
        "工程部",
        "",
        r"不制冷|不出热水|坏了|漏水|维修|报修|修一下|灯不亮|空调.{0,4}不凉|没热水",
    ),
    ("送物", "客需", "客房部", "", r"浴巾|毛巾|牙刷|拖鞋|矿泉水|瓶水|纸巾|洗发水"),
    ("客房清洁", "客需", "客房部", "", r"打扫|清洁|换床单|收拾房间"),
    (
        "预订意向",
        "商机",
        "前台",
        "",
        r"(预[订定]|想订|想[要]?订|想租|订一|订.{0,3}间|续住|包场|团队用房)",
    ),
    (
        "信息咨询",
        "咨询",
        "前台",
        "",
        r"几点|什么时候|怎么走|在哪|多少钱|如何|请问|有.{0,6}吗",
    ),
)
NEGATED = re.compile(
    r"不(需要|用了|用再|要了|要送|用送|用修|用打扫|用报修|投诉)|没(有)?坏|已经.{0,4}(好了|修好|送到|解决)|现在好了|取消.{0,6}(报修|送物)"
)
QC = (
    ("复述需求", r"确认一下|您是需要|您需要的是|我重复一下"),
    ("明确处置", r"我.{0,6}(安排|登记|联系|通知)|已经登记|马上.{0,4}(送|来|处理)"),
    ("跟进方式", r"回电|回访|给您回复|再联系|稍后.{0,6}(回复|联系)"),
)


def extract(segments):
    findings = {}
    checks = {}
    for seg in segments:
        if seg.get("overlap") or seg.get("needsReview"):
            continue
        resolved = bool(
            re.search(
                r"(之前|以前|原来|早先).{0,18}(坏|漏|不制冷).{0,14}(好了|修好了|解决了)",
                seg["text"],
            )
        )
        for raw in re.split(r"[，。；！？,;!?\n]", seg["text"]):
            quote = raw.strip()
            if not quote:
                continue
            ev = dict(segment=seg["id"], start=seg["start"], quote=quote)
            for label, pattern in QC:
                if re.search(pattern, quote):
                    checks.setdefault(label, ev)
            if NEGATED.search(quote):
                continue
            for title, category, dept, level, pattern in RULES:
                if (
                    resolved
                    and title == "设施报修"
                    and re.search(r"之前|以前|原来|早先", quote)
                ):
                    continue
                if re.search(pattern, quote):
                    findings.setdefault(
                        title,
                        dict(
                            title=title,
                            category=category,
                            department=dept,
                            level=level,
                            **ev
                        ),
                    )
    return list(findings.values()), [
        dict(
            label=label,
            result="符合" if label in checks else "证据不足",
            evidence=checks.get(label),
        )
        for label, _ in QC
    ]


def semantic(segments):
    """Local, optional reclassification. Reject unknown fields and ungrounded quotations."""
    host = "http://127.0.0.1:8766"
    if not segments or sum(len(s["text"]) for s in segments) > 6500:
        return []
    try:
        if not requests.get(host + "/health", timeout=0.25).ok:
            return []
        prompt = (
            "提取酒店来电的实际事项。否定、已解决、假设的问题不要生成新工单。"
            "每项输出 title(从给定类别选)、segment(整数)、quote(同一片段连续原话)。"
            "返回JSON对象，键为items。允许空。类别：" + "、".join(r[0] for r in RULES)
        )
        reply = requests.post(
            host + "/v1/chat/completions",
            json={
                "messages": [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": json.dumps(
                            [{"id": s["id"], "text": s["text"]} for s in segments],
                            ensure_ascii=False,
                        ),
                    },
                ],
                "temperature": 0,
                "max_tokens": 600,
                "response_format": {"type": "json_object"},
            },
            timeout=(1, 24),
        )
        reply.raise_for_status()
        items = json.loads(reply.json()["choices"][0]["message"]["content"])["items"]
        if not isinstance(items, list):
            return []
    except (requests.RequestException, ValueError, KeyError, TypeError, IndexError):
        return []
    by_id = {s["id"]: s for s in segments}
    kinds = {r[0]: r for r in RULES}
    found = []
    for item in items[:12]:
        if not isinstance(item, dict) or type(item.get("segment")) is not int:
            continue
        s = by_id.get(item["segment"])
        title, quote = item.get("title"), item.get("quote")
        if not (
            isinstance(title, str)
            and title in kinds
            and isinstance(quote, str)
            and len(quote.strip()) >= 3
            and s
            and not s.get("needsReview")
            and not s.get("overlap")
            and quote in s["text"]
        ):
            continue
        clauses = [c for c in re.split(r"[，。；！？,;!?\n]", s["text"]) if quote in c]
        if not clauses or any(NEGATED.search(c) for c in clauses):
            continue
        _, category, dept, level, _ = kinds[title]
        found.append(
            dict(
                title=title,
                category=category,
                department=dept,
                level=level,
                segment=s["id"],
                start=s["start"],
                quote=quote,
            )
        )
    return found


def analyze(segments, use_model=True):
    findings, checks = extract(segments)
    extra = semantic(segments) if use_model else []
    seen = {f["title"] for f in findings}
    for f in extra:
        if f["title"] not in seen:
            findings.append(f)
            seen.add(f["title"])
    category = next(
        (c for c in CATEGORIES if any(f["category"] == c for f in findings)), "其他"
    )
    level = next((l for l in "SABC" if any(f["level"] == l for f in findings)), "")
    summary = "；".join(dict.fromkeys(f["quote"] for f in findings))[:500]
    return dict(
        category=category,
        level=level,
        findings=findings,
        checks=checks,
        summary=summary,
        method="规则与本地语义模型" if extra else "规则初筛",
        warnings=[
            s["id"] for s in segments if s.get("overlap") or s.get("needsReview")
        ],
    )


def save_analysis(db, key, segments, result):
    call = db.execute(
        "SELECT review_state,category FROM calls WHERE id=?", (key,)
    ).fetchone()
    version = db.execute(
        "SELECT coalesce(max(version),0)+1 FROM phone_analyses WHERE call_id=?", (key,)
    ).fetchone()[0]
    now = time.time()
    db.execute(
        "INSERT INTO phone_analyses VALUES(?,?,?,?,?)",
        (
            key,
            version,
            now,
            json.dumps(segments, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
        ),
    )
    reviewed = call["review_state"] != "待复核"
    db.execute(
        """UPDATE calls SET revision=revision+1, category=CASE WHEN ? THEN category ELSE ? END,
               summary=CASE WHEN ? THEN summary ELSE ? END WHERE id=?""",
        (reviewed, result["category"], reviewed, result["summary"], key),
    )
    if not reviewed:
        # Refresh machine suggestions while keeping human-added, dispatched and dismissed evidence.
        previous = db.execute(
            "SELECT result FROM phone_analyses WHERE call_id=? AND version=?",
            (key, version - 1),
        ).fetchone()
        if previous:
            for old in json.loads(previous[0])["findings"]:
                db.execute(
                    "DELETE FROM items WHERE call_id=? AND title=? AND quote=? AND start=? AND automatic=1 AND state='待确认'",
                    (key, old["title"], old["quote"], old["start"]),
                )
        for f in result["findings"]:
            if not db.execute(
                "SELECT 1 FROM items WHERE call_id=? AND title=?", (key, f["title"])
            ).fetchone():
                db.execute(
                    "INSERT INTO items(call_id,title,department,quote,start,automatic) VALUES(?,?,?,?,?,1)",
                    (key, f["title"], f["department"], f["quote"], f["start"]),
                )
        if result["level"]:
            db.execute(
                """INSERT INTO phone_alerts(call_id,level,state,created) VALUES(?,?,'待确认',?)
                        ON CONFLICT(call_id) DO UPDATE SET level=excluded.level WHERE state='待确认'""",
                (key, result["level"], now),
            )
        else:
            db.execute(
                "UPDATE phone_alerts SET state='已排除' WHERE call_id=? AND state='待确认'",
                (key,),
            )
