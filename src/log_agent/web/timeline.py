"""错误时间线：按时间分桶统计 ERROR / WARN / 其他日志量，并标出错误尖峰。

只读本地日志、不调用模型。每份日志先按秒聚合一次（结果缓存在 LogFile.scan_cache 里，
文件一变就失效），再按展示需要的桶宽合并，所以切换桶数不必重新扫全文。

与 CLI 共用 find_timestamp / level_and_body / 错误签名，统计口径和 log_overview 一致：
- 没有时间戳的行（堆栈续行）不单独计数，挂在上一条日志的时间上只用于定位；
- 无偏移的时间按会话时区解释（不读全局时区，避免和正在执行的分析轮次互相影响）；
- 签名经过脱敏后才返回给浏览器。
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, tzinfo
from pathlib import Path
from typing import Any

from ..logfile import open_log
from ..logformat import is_stack_line, level_and_body, line_record
from ..redact import redact_log
from ..timefilter import find_timestamp
from ..tools import _error_signature

# 展示用桶宽候选（秒）：从 1 秒到 1 天，挑第一个让桶数不超过目标值的
NICE_WIDTHS = (1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400)
TOP_SIGNATURES = 3
# 只有时钟没有日期的日志（如 `14:00:01 ERROR ...`）统一挂在这一天上，界面只显示时分秒
_TIME_ONLY_DATE = date(2000, 1, 1)
_SPIKE_MIN_ERRORS = 3
_SPIKE_Z = 3.0


@dataclass
class _Second:
    total: int = 0
    warn: int = 0
    error: int = 0
    first_line: int = 0
    first_error: int = 0
    signatures: Counter[str] = field(default_factory=Counter)
    examples: dict[str, int] = field(default_factory=dict)


@dataclass
class FileScan:
    source: str
    seconds: dict[int, _Second]
    events: int
    timestamped: int
    time_only: bool


def _epoch(stamp: datetime | time, tz: tzinfo, anchor: date = _TIME_ONLY_DATE) -> tuple[int, bool]:
    if isinstance(stamp, time):
        value = datetime.combine(anchor, stamp.replace(tzinfo=None), tzinfo=tz)
        return int(value.timestamp()), True
    value = stamp if stamp.tzinfo is not None else stamp.replace(tzinfo=tz)
    return int(value.timestamp()), False


def scan_file(path: str | Path, tz: tzinfo, encoding: str | None = None) -> FileScan:
    """逐行扫描一份日志，按秒聚合；同一文件、同一时区重复调用直接命中缓存。"""
    log = open_log(path, encoding, ignore_global=True)
    key = ("web-timeline", repr(tz))
    with log.scan_lock:
        cached = log.scan_cache.get(key)
        if cached is not None:
            return cached
        result = _scan(log, str(path), tz)
        log.scan_cache[key] = result
        return result


def _scan(log, source: str, tz: tzinfo) -> FileScan:
    # Anchor leading clock-only entries to the first dated event in this file.
    anchor = _TIME_ONLY_DATE
    with closing(log.iter_lines(1)) as lines:
        for _, line in lines:
            if is_stack_line(line):
                continue
            found = find_timestamp(line, record=line_record(line))
            if found and isinstance(found[1], datetime):
                stamp = found[1]
                anchor = (stamp if stamp.tzinfo is None else stamp.astimezone(tz)).date()
                break
    seconds: dict[int, _Second] = {}
    current: int | None = None
    events = timestamped = 0
    time_only = True
    for lineno, line in log.iter_lines(1):
        if is_stack_line(line):
            continue
        record = line_record(line)
        found = find_timestamp(line, record=record)
        parsed = level_and_body(line, record=record)
        if found:
            if isinstance(found[1], datetime):
                stamp = found[1]
                anchor = (stamp if stamp.tzinfo is None else stamp.astimezone(tz)).date()
            current, only = _epoch(found[1], tz, anchor)
            time_only = time_only and only
            timestamped += 1
        elif not parsed:
            continue  # 堆栈续行、多行 SQL：属于上一条日志，不单独计数
        if current is None:
            continue
        events += 1
        bucket = seconds.get(current)
        if bucket is None:
            bucket = seconds[current] = _Second(first_line=lineno)
        bucket.total += 1
        level = parsed[0] if parsed else ""
        if level in ("FATAL", "ERROR"):
            bucket.error += 1
            bucket.first_error = bucket.first_error or lineno
            signature = _error_signature(parsed[1][:4000])
            if signature:
                bucket.signatures[signature] += 1
                bucket.examples.setdefault(signature, lineno)
        elif level == "WARN":
            bucket.warn += 1
    return FileScan(source, seconds, events, timestamped, time_only and timestamped > 0)


def pick_width(span_seconds: int, target: int) -> int:
    need = max(1, math.ceil(span_seconds / max(1, target)))
    return next((w for w in NICE_WIDTHS if w >= need), NICE_WIDTHS[-1] * math.ceil(need / NICE_WIDTHS[-1]))


def _spikes(errors: list[int]) -> tuple[list[int], float]:
    """稳健 z 分数找尖峰：中位数 + MAD，避免一次大爆发把均值和方差一起抬高、反而什么都标不出来。"""
    if not errors:
        return [], 0.0
    med = statistics.median(errors)
    mad = statistics.median(abs(e - med) for e in errors)
    scale = max(1.4826 * mad, 1.0)
    threshold = max(_SPIKE_MIN_ERRORS, med + _SPIKE_Z * scale)
    return [i for i, e in enumerate(errors) if e >= threshold], threshold


def build_timeline(
    paths: Sequence[str | Path],
    tz: tzinfo = UTC,
    *,
    target_buckets: int = 120,
    encoding: str | None = None,
    redact: bool = True,
) -> dict[str, Any]:
    target_buckets = max(10, min(int(target_buckets), 600))
    files: list[dict[str, Any]] = []
    scans: list[FileScan] = []
    for path in paths:
        try:
            scan = scan_file(path, tz, encoding)
        except (OSError, EOFError) as exc:
            files.append({"source": str(path), "name": Path(path).name, "error": f"{type(exc).__name__}: {exc}"})
            continue
        scans.append(scan)
        keys = scan.seconds.keys()
        files.append({
            "source": scan.source,
            "name": Path(scan.source).name,
            "events": scan.events,
            "timestamped": scan.timestamped,
            "first": _iso(min(keys), tz) if keys else None,
            "last": _iso(max(keys), tz) if keys else None,
        })

    all_seconds = [s for scan in scans for s in scan.seconds]
    base = {"timezone": _tz_name(tz), "files": files, "time_only": all(s.time_only for s in scans) if scans else False}
    if not all_seconds:
        return {**base, "bucket_seconds": 0, "buckets": [], "spikes": [], "threshold": 0,
                "totals": {"events": 0, "warn": 0, "error": 0}}

    start, end = min(all_seconds), max(all_seconds)
    width = pick_width(end - start + 1, target_buckets)
    # 按会话时区对齐桶边界：分钟 / 小时 / 天桶从整点开始，界面刻度才好读
    offset = int(datetime.fromtimestamp(start, tz).utcoffset().total_seconds()) if width >= 60 else 0
    origin = (start + offset) // width * width - offset
    count = (end - origin) // width + 1

    buckets: list[dict[str, Any]] = [
        {"index": i, "start": _iso(origin + i * width, tz), "end": _iso(origin + (i + 1) * width, tz),
         "start_ts": origin + i * width, "total": 0, "warn": 0, "error": 0,
         "first": [], "first_error": [], "_sigs": Counter(), "_sig_src": {}}
        for i in range(count)
    ]
    for scan in scans:
        firsts: dict[int, tuple[int, int]] = {}
        first_errors: dict[int, tuple[int, int]] = {}
        for second, item in scan.seconds.items():
            idx = (second - origin) // width
            bucket = buckets[idx]
            bucket["total"] += item.total
            bucket["warn"] += item.warn
            bucket["error"] += item.error
            if idx not in firsts or second < firsts[idx][0]:
                firsts[idx] = (second, item.first_line)
            if item.first_error and (idx not in first_errors or second < first_errors[idx][0]):
                first_errors[idx] = (second, item.first_error)
            for sig, n in item.signatures.items():
                bucket["_sigs"][sig] += n
                bucket["_sig_src"].setdefault(sig, (scan.source, item.examples[sig]))
        for idx, (_, line) in firsts.items():
            buckets[idx]["first"].append({"source": scan.source, "line": line})
        for idx, (_, line) in first_errors.items():
            buckets[idx]["first_error"].append({"source": scan.source, "line": line})

    for bucket in buckets:
        sigs: Counter[str] = bucket.pop("_sigs")
        sources: dict[str, tuple[str, int]] = bucket.pop("_sig_src")
        bucket["top"] = [
            {"signature": redact_log(sig, enabled=redact), "count": n,
             "source": sources[sig][0], "line": sources[sig][1]}
            for sig, n in sigs.most_common(TOP_SIGNATURES)
        ]

    spikes, threshold = _spikes([b["error"] for b in buckets])
    for i in spikes:
        buckets[i]["spike"] = True
    return {
        **base,
        "bucket_seconds": width,
        "start": _iso(origin, tz),
        "end": _iso(origin + count * width, tz),
        "buckets": buckets,
        "spikes": spikes,
        "threshold": threshold,
        "totals": {
            "events": sum(b["total"] for b in buckets),
            "warn": sum(b["warn"] for b in buckets),
            "error": sum(b["error"] for b in buckets),
        },
    }


def _iso(epoch: int, tz: tzinfo) -> str:
    return datetime.fromtimestamp(epoch, tz).isoformat()


def _tz_name(tz: tzinfo) -> str:
    key = getattr(tz, "key", None)
    if key:
        return key
    minutes = int(datetime.now(tz).utcoffset().total_seconds() // 60)
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
