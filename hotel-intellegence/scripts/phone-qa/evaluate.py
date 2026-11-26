"""Evaluate a frozen synthetic corpus in a fresh, explicitly isolated data directory."""

import json
import os
from pathlib import Path
import sys
import re
import wave
import numpy as np

root = Path(os.environ["HOTEL_VOICE_DATA"])
if (root / "platform.sqlite3").exists() or not (root / "samples").is_dir():
    raise SystemExit(
        "Provide a new isolated data directory containing generated samples."
    )
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from local import phone, platform_db
from local.phone_feed import receive
from datetime import datetime

cases = json.loads(Path(__file__).with_name("cases.json").read_text(encoding="utf-8"))
platform_db.migrate()
inbox = root / "phone-test-inbox"
inbox.mkdir(exist_ok=True)
started = datetime.now().astimezone().isoformat()


def distance(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def norm(text):
    return re.sub(
        r"[^\w]",
        "",
        text.translate(str.maketrans("零一二三四五六七八九", "0123456789")),
    )


def variant(file, num):
    if num % 5 not in (3, 4):
        return "安静"
    with wave.open(str(file)) as wav:
        rate = wav.getframerate()
        assert wav.getsampwidth() == 2 and wav.getnchannels() == 1
        data = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    if num % 5 == 3:
        data = np.clip(
            data + np.random.default_rng(num).normal(0, 250, len(data)), -32768, 32767
        ).astype("<i2")
        channels = 1
        label = "噪声"
    else:
        data = np.column_stack([data, np.zeros_like(data)]).astype("<i2")
        channels = 2
        label = "单侧双声道"
    with wave.open(str(file), "wb") as wav:
        wav.setparams((channels, 2, rate, 0, "NONE", ""))
        wav.writeframes(data.tobytes())
    return label


results = []
for n, (category, titles, truth) in enumerate(cases, 1):
    path = root / "samples" / f"call-{n:02}.wav"
    noise = variant(path, n)
    if n % 2:
        with path.open("rb") as inp:
            key = phone.ingest(inp, path.name, "测试主叫", "未知", started, True)["id"]
    else:
        import shutil

        shutil.copyfile(path, inbox / path.name)
        data = dict(
            file=path.name,
            call_id=str(n),
            caller="测试主叫",
            callee="未知",
            started=started,
        )
        manifest = inbox / (str(n) + ".ready.json")
        manifest.write_text(json.dumps(data))
        receive(inbox, test=True)
        with platform_db.database() as db:
            key = db.execute(
                "SELECT id FROM calls WHERE external_id=? AND source='模拟交换机'",
                (str(n),),
            ).fetchone()[0]
    assert phone.process_next()
    detail = phone.detail(key)
    text = "".join(s["text"] for s in detail["segments"])
    predicted = {f["title"] for f in (detail["analysis"] or {}).get("findings", [])}
    expected = set(titles.split("|")) if titles else set()
    valid = all(
        any(
            s["id"] == f["segment"] and f["quote"] in s["text"]
            for s in detail["segments"]
        )
        for f in (detail["analysis"] or {}).get("findings", [])
    )
    numbers = re.findall(r"[0-9]{3,}", norm(truth))
    recovered = re.findall(r"[0-9]{3,}", norm(text))
    results.append(
        dict(
            numbers=len(numbers),
            correct_numbers=sum(x in recovered for x in numbers),
            n=n,
            group=noise,
            truth=category,
            actual=detail["category"],
            text=text,
            hit=len(predicted & expected),
            miss=len(expected - predicted),
            extra=len(predicted - expected),
            errors=distance(norm(truth), norm(text)),
            chars=len(norm(truth)),
            valid=valid,
        )
    )
(root / "evaluation.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
)
for label, rows in [("全部", results), ("留出", results[20:])] + [
    (g, [r for r in results if r["group"] == g]) for g in ("安静", "噪声", "单侧双声道")
]:
    print(
        label,
        len(rows),
        "主类",
        sum(r["truth"] == r["actual"] for r in rows),
        "事项命中/漏检/多报",
        [sum(r[k] for r in rows) for k in ("hit", "miss", "extra")],
        "字错/参考字数",
        [sum(r[k] for r in rows) for k in ("errors", "chars")],
        "证据有效通话",
        sum(r["valid"] for r in rows),
    )
