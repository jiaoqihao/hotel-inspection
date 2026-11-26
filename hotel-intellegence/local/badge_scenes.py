"""Evidence-backed sections shared by local model and rule reports."""

import re

UNCERTAIN = r"建议|提议|还没定|未决定|不同意|尚无定论|是否|要不要|如果|还没|还未|未定|尚未|没有决定|未决定|待确认|再讨论|考虑|暂不|[？?]"
DECISION = r"决定|决议|就按|确定采用|确定执行|一致同意"
DUE = r"(?:今天|明天|后天|本周[一二三四五六日]?|下周[一二三四五六日]?|周[一二三四五六日]|\d{1,2}月\d{1,2}[日号])(?:上午|下午|晚上)?(?:\d{1,2}[点时])?(?:之?前)?"
COMPLAINT = r"投诉|不满意|太差|退钱|退款|找经理|不能接受"
NEGATED = r"(?:不想|不必|不用|不会|没有|并未|无需|未曾|不要|不打算|不需要|并不|不是|并非|不)(?:再|提出|进行|申请|感到|觉得|需要)?"
CONDITIONAL = r"如果|假如|假设|培训|示例|是否|要不要|[？?]"


def signalled(text, pattern):
    """Test each clause so a denial cannot suppress a later explicit complaint."""
    for clause in re.split("[，,；;。！？]", text):
        if not re.search(pattern, clause) or re.search(CONDITIONAL, text):
            continue
        if not re.search(NEGATED + "(?:" + pattern + ")", clause):
            return True
    return False


def quotes(segments):
    for seg in segments:
        if seg.get("overlap") or seg["text"].startswith("[片段过短"):
            continue
        for sentence in re.findall(r"[^。！？!?\n]+[。！？!?]?", seg["text"]):
            if sentence.strip():
                yield dict(
                    segment=seg["id"], start=seg["start"], quote=sentence.strip()
                )


def insight(label, ev):
    return dict(label=label, value=ev["quote"], evidence=[ev])


def enrich(report, segments):
    scene = report["scene"]
    events = list(quotes(segments))
    if scene == "会议":
        topics = []
        for ev in events:
            q = ev["quote"]
            if re.search(UNCERTAIN, q):
                label = "待确认事项"
            elif re.search(DECISION, q):
                label = "已决定事项"
            elif re.search("议题|讨论|关于|汇报|开会|会议", q):
                label = "议题"
            else:
                continue
            topics.append(insight(label, ev))
        report["insights"] = topics
        minutes = []
        for label in ("议题", "已决定事项", "待确认事项"):
            values = [row["value"] for row in topics if row["label"] == label]
            minutes.append(label + "：" + (" ".join(values) if values else "待确认"))
        minutes.append(
            "待办："
            + (" ".join(t["title"] for t in report["todos"]) or "未提取到明确待办")
        )
        report["summary"] = "\n".join(minutes)
        for task in report["todos"]:
            if task["due"] == "待确认":
                hit = re.search(DUE, " ".join(e["quote"] for e in task["evidence"]))
                if hit:
                    task["due"] = hit.group()
        for row in report["checks"]:
            if "决策" in row["name"]:
                row["evidence"] = [
                    e
                    for r in topics
                    if r["label"] == "已决定事项"
                    for e in r["evidence"]
                ]
                row.update(
                    status="待复核" if row["evidence"] else "无法判断",
                    detail="请回听核对决策范围。",
                )
    if scene == "餐厅服务":
        # Only explicit expressions may trigger a complaint workflow.
        report["risks"] = [
            r
            for r in report["risks"]
            if not re.search("投诉|客诉|不满|情绪|退款", r["title"])
        ]
        for ev in events:
            q = ev["quote"]
            if signalled(q, COMPLAINT):
                report["risks"].append(
                    dict(
                        title="客诉预警",
                        level="优先处理",
                        complaint=True,
                        detail="出现投诉或不满措辞，待回听确认。",
                        evidence=[ev],
                    )
                )
            for label, pattern in [
                ("不满", "不满意|太失望|很生气|不能接受"),
                ("焦急", "很着急|赶时间|等不及|急死了"),
                ("满意", "很满意|非常满意|我满意|十分满意"),
            ]:
                if signalled(q, pattern):
                    report["insights"].append(insight("明确表达·" + label, ev))
    if scene in ("餐厅服务", "销售沟通"):
        stages = (
            [
                ("需求", "需要|希望|预算|人数|桌数"),
                ("方案", "套餐|报价|方案|可以提供"),
                ("顾虑", "担心|顾虑|太贵|超预算"),
                (
                    "回应",
                    "可以调整|可以优惠|可以提供|我们提供|可以安排|可以免费|为您调整",
                ),
                ("下一步", "发给|发送|预约|回电|跟进|看场"),
            ]
            if scene == "销售沟通"
            else [
                ("迎宾", "欢迎光临|您好|几位"),
                ("问需", "忌口|过敏|需要什么"),
                ("说菜", "推荐|招牌|口味"),
                ("订单复核", "您点的是|核对|确认一下"),
                ("问题回应", "我去|我来|帮您催|马上处理"),
                ("结果反馈", "已经上齐|已经送到|已经处理好|已解决|已经解决|已经换好"),
            ]
        )
        chain = []
        stage_events = {}
        for name, pattern in stages:
            found = [e for e in events if re.search(pattern, e["quote"])]
            if name == "结果反馈":
                found = [
                    e
                    for e in found
                    if not re.search(
                        UNCERTAIN + "|还没|没有|我会|保证|稍后", e["quote"]
                    )
                ]
            stage_events[name] = found
            chain.append(
                dict(
                    name=name,
                    status="待复核" if found else "待跟进",
                    detail="请结合前后原话核对。"
                    if found
                    else "当前录音缺少明确依据。",
                    evidence=found[:6],
                )
            )
        report["flow"] = chain
        if scene == "销售沟通":
            for name, pattern in [
                ("停车需求", "需要.{0,8}停车|担心停车"),
                ("婚宴需求", "婚宴"),
                ("预算约束", "预算|每桌.{0,8}元"),
                ("看场意向", "预约看场|想看场"),
            ]:
                ev = next((e for e in events if signalled(e["quote"], pattern)), None)
                if ev:
                    report["insights"].append(
                        dict(label="标签建议", value=name, evidence=[ev])
                    )
            for pos, concern in enumerate(events):
                if not signalled(concern["quote"], "担心|顾虑|太贵|超预算"):
                    continue
                subjects = [
                    "停车",
                    "价格",
                    "预算",
                    "费用",
                    "场地",
                    "时间",
                    "菜单",
                    "交通",
                    "噪声",
                    "住宿",
                    "座位",
                ]
                shared = [s for s in subjects if s in concern["quote"]]
                reply = next(
                    (
                        e
                        for e in events[pos + 1 : pos + 5]
                        if any(s in e["quote"] for s in shared)
                        and re.search("可以|提供|安排|调整|免费|优惠", e["quote"])
                    ),
                    None,
                )
                chain.append(
                    dict(
                        name="顾虑跟进",
                        status="回应待复核" if reply else "待跟进",
                        detail="找到后续同一事项的回应。"
                        if reply
                        else "尚待核对后续回应。",
                        evidence=[concern, reply] if reply else [concern],
                    )
                )
        else:
            for pos, ev in enumerate(events):
                if signalled(ev["quote"], COMPLAINT):
                    later = events[pos + 1 :]
                    response = next(
                        (e for e in later if e in stage_events["问题回应"]), None
                    )
                    outcome = next(
                        (e for e in later if e in stage_events["结果反馈"]), None
                    )
                    chain.append(
                        dict(
                            name="客诉后续",
                            status="结果待复核" if outcome else "待跟进",
                            detail="按时间关联后续原话，请核对是否指同一事项。",
                            evidence=[e for e in (ev, response, outcome) if e],
                        )
                    )
    return report
