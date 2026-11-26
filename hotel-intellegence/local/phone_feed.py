"""Consume producer-finalized JSON manifests next to their recordings."""

import csv
import json
from pathlib import Path
from fastapi import HTTPException

from local.platform_db import database


def external_key(call_id, source_id="", segment_id=""):
    if source_id or segment_id:
        return json.dumps([source_id, call_id, segment_id], ensure_ascii=False)
    return call_id


def receive(folder, *, test=False):
    from local.phone import ingest

    root = Path(folder).resolve(strict=True)
    handled = 0
    for manifest in sorted(root.glob("*.ready.json")):
        if handled >= 20:
            break
        if manifest.resolve().parent != root:
            continue
        try:
            if manifest.stat().st_size > 65536:
                raise ValueError("清单过大")
            data = json.loads(manifest.read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("清单需要对象")

            def required(field, limit):
                value = data.get(field)
                if (
                    not isinstance(value, str)
                    or not value.strip()
                    or len(value) > limit
                ):
                    raise ValueError("字段无效 " + field)
                return value.strip()

            audio = (root / required("file", 240)).resolve()
            if audio.parent != root or audio == manifest:
                raise ValueError("音频必须在接入目录且已写完")
            if not audio.is_file():
                continue
            if "bytes" in data:
                size = data["bytes"]
                if type(size) is not int or size <= 0 or audio.stat().st_size > size:
                    raise ValueError("清单字节数不匹配")
                if audio.stat().st_size < size:
                    continue
            metadata = {
                k: required(k, 80)
                for k in (
                    "answered_extension",
                    "ended",
                    "direction",
                    "segment_id",
                    "linked_id",
                    "source_id",
                )
                if data.get(k)
            }
            call_id = required("call_id", 128)
            metadata["call_id"] = call_id
            call_id = external_key(
                call_id, metadata.get("source_id", ""), metadata.get("segment_id", "")
            )
            if "channel_map" in data:
                mapping = data["channel_map"]
                if (
                    not isinstance(mapping, list)
                    or len(mapping) != 2
                    or not all(isinstance(v, str) for v in mapping)
                    or set(mapping) != {"前台", "对方"}
                ):
                    raise ValueError("双声道顺序须为前台、对方或相反顺序")
                metadata["channel_map"] = mapping
            with audio.open("rb") as stream:
                ingest(
                    stream,
                    audio.name,
                    required("caller", 32),
                    required("callee", 32),
                    required("started", 40),
                    test,
                    "模拟交换机" if test else "交换机目录",
                    call_id,
                    metadata,
                )
        except (ValueError, HTTPException) as exc:
            manifest.with_suffix(".error.txt").write_text(str(exc), encoding="utf-8")
            manifest.replace(manifest.with_suffix(".rejected"))
        else:
            manifest.replace(manifest.with_suffix(".done"))
        handled += 1


def compare_cdr(stream, test=False, *, details=None):
    """Rows identify independent call legs. Extra recordings require a stated CDR date window."""
    from datetime import date

    reader = csv.DictReader(stream)
    if not {"call_id", "caller", "callee"} <= set(reader.fieldnames or ()):
        raise ValueError("CSV 需要 call_id,caller,callee 三列")
    with database() as db:
        calls = {
            r["external_id"]: r
            for r in db.execute(
                "SELECT external_id,caller,callee,started FROM calls WHERE source=? AND test=?",
                ("模拟交换机" if test else "交换机目录", int(test)),
            )
        }
    counts = dict.fromkeys(
        (
            "已读明细",
            "已匹配",
            "缺少录音",
            "号码不匹配",
            "明细重复",
            "未接来电",
            "未接但有录音",
        ),
        0,
    )
    seen = set()
    dates = set()
    for row in reader:
        if not all((row.get(k) or "").strip() for k in ("call_id", "caller", "callee")):
            raise ValueError("话单有空标识或空号码")
        key = external_key(
            row["call_id"].strip(),
            row.get("source_id") or "",
            row.get("segment_id") or "",
        )
        counts["已读明细"] += 1
        if counts["已读明细"] > 50000:
            raise ValueError("每次对账最多五万行")
        if row.get("started"):
            dates.add(date.fromisoformat(row["started"][:10]).isoformat())
        missed = (row.get("disposition") or "").strip().upper() in (
            "NO ANSWER",
            "BUSY",
            "FAILED",
            "未接",
            "未接听",
        )
        caller, callee = row["caller"].strip(), row["callee"].strip()
        if key in seen:
            reason = "明细重复"
        elif missed:
            reason = "未接来电"
            if key in calls:
                counts["未接但有录音"] += 1
        elif key not in calls:
            reason = "缺少录音"
        elif (calls[key]["caller"], calls[key]["callee"]) != (caller, callee):
            reason = "号码不匹配"
        else:
            reason = "已匹配"
        counts[reason] += 1
        if details is not None:
            details.append(
                (
                    key,
                    row.get("segment_id") or "",
                    caller,
                    callee,
                    row.get("started") or "",
                    reason,
                )
            )
        seen.add(key)
    if dates:
        extra = [
            r for k, r in calls.items() if k not in seen and r["started"][:10] in dates
        ]
        counts["话单所含日期内无对应话单的录音"] = len(extra)
        if details is not None:
            details.extend(
                (
                    r["external_id"],
                    "",
                    r["caller"],
                    r["callee"],
                    r["started"],
                    "录音缺话单",
                )
                for r in extra
            )
    return counts


def reconcile(cdr, *, test=False):
    with Path(cdr).open(encoding="utf-8-sig", newline="") as src:
        return compare_cdr(src, test)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="用独立通话明细对账")
    parser.add_argument("cdr")
    parser.add_argument("--test", action="store_true")
    args = parser.parse_args()
    print(json.dumps(reconcile(args.cdr, test=args.test), ensure_ascii=False))
