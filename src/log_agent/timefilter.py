"""日志时间窗口：解析 --since / --until，识别日志行里的时间戳，判断某行是否落在窗口内。

日志时间戳常见两类：
- 完整日期时间：`2026-06-09 14:02:03,123`、`2026-06-09T14:02:03.123Z`、`2026/06/09 14:02:03`
- 只有时间：syslog 的 `Jun  9 14:02:03`、部分 logging 格式的 `14:02:03.123`

用户给的边界也可能只有时间（`14:00`）。任一侧只有时间时，按"一天中的时刻"比较。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

Stamp = datetime | time

TS_RE = re.compile(
    r"(?P<date>\d{4}[-/]\d{2}[-/]\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:[.,](?P<frac>\d{1,9}))?"
    r"|\b(?P<tonly>\d{2}:\d{2}:\d{2})(?:[.,](?P<tfrac>\d{1,9}))?\b"
)

# 时间戳只在行首附近找，避免把消息正文里的时间（如"超时 00:00:30"）当成日志时间
_SCAN_CHARS = 80

# 日志在多线程下常有轻微乱序；越过 until 这么久之后才提前结束扫描
_OUT_OF_ORDER_TOLERANCE = timedelta(minutes=5)

_DATETIME_FORMATS: list[tuple[str, timedelta]] = [
    ("%Y-%m-%d %H:%M:%S.%f", timedelta(microseconds=1)),
    ("%Y-%m-%d %H:%M:%S", timedelta(seconds=1)),
    ("%Y-%m-%d %H:%M", timedelta(minutes=1)),
    ("%Y-%m-%d", timedelta(days=1)),
]
_TIME_FORMATS: list[tuple[str, timedelta]] = [
    ("%H:%M:%S.%f", timedelta(microseconds=1)),
    ("%H:%M:%S", timedelta(seconds=1)),
    ("%H:%M", timedelta(minutes=1)),
]

BOUND_EXAMPLES = "2026-06-09 14:00、2026-06-09T14:00:30、2026-06-09 或 14:00"


def _micro(frac: str | None) -> int:
    return int((frac or "0")[:6].ljust(6, "0"))


def find_timestamp(line: str) -> tuple[str, Stamp] | None:
    """在行首附近找时间戳，返回 (原文, 解析值)；找不到或数值非法返回 None。"""
    match = TS_RE.search(line[:_SCAN_CHARS])
    if not match:
        return None
    try:
        if match.group("date"):
            day = date.fromisoformat(match.group("date").replace("/", "-"))
            clock = time.fromisoformat(match.group("time")).replace(microsecond=_micro(match.group("frac")))
            return match.group(0), datetime.combine(day, clock)
        clock = time.fromisoformat(match.group("tonly")).replace(microsecond=_micro(match.group("tfrac")))
        return match.group(0), clock
    except ValueError:
        return None


@dataclass(frozen=True)
class Bound:
    raw: str
    value: Stamp

    @property
    def time_only(self) -> bool:
        return isinstance(self.value, time)


def parse_bound(text: str, *, upper: bool) -> Bound:
    """解析时间边界。上界按给定精度包含整段：`--until 14:05` 包含 14:05:59.999999。

    Raises:
        ValueError: 无法识别的格式。
    """
    raw = text.strip()
    normalized = raw.replace("/", "-").replace("T", " ").rstrip("Z")
    for fmt, precision in _DATETIME_FORMATS:
        try:
            value = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        return Bound(raw, value + precision - timedelta(microseconds=1) if upper else value)
    for fmt, precision in _TIME_FORMATS:
        try:
            clock = datetime.strptime(normalized, fmt).time()
        except ValueError:
            continue
        if upper:
            anchor = datetime.combine(date(2000, 1, 1), clock)
            end = anchor + precision - timedelta(microseconds=1)
            clock = end.time() if end.date() == anchor.date() else time.max
        return Bound(raw, clock)
    raise ValueError(f"无法识别的时间 '{raw}'，支持的写法如：{BOUND_EXAMPLES}")


def _as_time(value: Stamp) -> time:
    return value.time() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class TimeWindow:
    since: Bound | None = None
    until: Bound | None = None

    def __bool__(self) -> bool:
        return self.since is not None or self.until is not None

    def describe(self) -> str:
        return f"{self.since.raw if self.since else '开头'} → {self.until.raw if self.until else '结尾'}"

    @staticmethod
    def _compare(stamp: Stamp, bound: Bound) -> int:
        if bound.time_only or isinstance(stamp, time):
            left, right = _as_time(stamp), _as_time(bound.value)
        else:
            left, right = stamp, bound.value  # type: ignore[assignment]
        return (left > right) - (left < right)

    def contains(self, stamp: Stamp) -> bool:
        if self.since and self._compare(stamp, self.since) < 0:
            return False
        return not (self.until and self._compare(stamp, self.until) > 0)

    def past_end(self, stamp: Stamp) -> bool:
        """是否已明显越过上界，可以提前结束扫描（只在双方都有完整日期时判断）。"""
        if not self.until or self.until.time_only or not isinstance(stamp, datetime):
            return False
        return stamp > self.until.value + _OUT_OF_ORDER_TOLERANCE  # type: ignore[operator]


def parse_window(since: str | None, until: str | None) -> TimeWindow:
    """解析一对边界；空字符串视为不限。

    Raises:
        ValueError: 格式无法识别，或起点晚于终点。
    """
    lower = parse_bound(since, upper=False) if since and since.strip() else None
    upper = parse_bound(until, upper=True) if until and until.strip() else None
    if lower and upper and lower.time_only == upper.time_only and lower.value > upper.value:  # type: ignore[operator]
        raise ValueError(f"起始时间 {lower.raw} 晚于结束时间 {upper.raw}")
    return TimeWindow(lower, upper)


class WindowTracker:
    """逐行判断是否在窗口内。没有时间戳的行（堆栈、多行 SQL）沿用上一条日志的时间。"""

    def __init__(self, window: TimeWindow) -> None:
        self.window = window
        self.current: Stamp | None = None
        self.saw_timestamp = False
        self.done = False

    def accept(self, line: str) -> bool:
        if not self.window:
            return True
        found = find_timestamp(line)
        if found:
            self.current = found[1]
            self.saw_timestamp = True
            if self.window.past_end(self.current):
                self.done = True
                return False
        return self.current is not None and self.window.contains(self.current)


_default_window = TimeWindow()


def set_default_window(since: str | None, until: str | None) -> TimeWindow:
    """由 CLI 的 --since / --until 设置；工具调用没显式传边界时使用。"""
    global _default_window
    _default_window = parse_window(since, until)
    return _default_window


def default_window() -> TimeWindow:
    return _default_window


def reset_default_window() -> None:
    global _default_window
    _default_window = TimeWindow()
