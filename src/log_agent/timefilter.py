"""日志时间窗口：解析 --since / --until，识别日志行里的时间戳，判断某行是否落在窗口内。

日志时间戳常见两类：
- 完整日期时间：`2026-06-09 14:02:03,123`、`2026-06-09T14:02:03.123Z`、`2026/06/09 14:02:03`
- 只有时间：syslog 的 `Jun  9 14:02:03`、部分 logging 格式的 `14:02:03.123`

用户给的边界也可能只有时间（`14:00`）。任一侧只有时间时，按配置时区的"一天中的时刻"比较。
完整日期保留时区偏移，无偏移时间按配置时区解释，比较时统一转成 UTC。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .logformat import UNPARSED, custom_formats, line_record, match_custom, maybe_wrapped, unwrap

Stamp = datetime | time

TS_RE = re.compile(
    r"(?P<date>\d{4}[-/]\d{2}[-/]\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:[.,](?P<frac>\d{1,9}))?"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?"
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

_default_timezone: tzinfo = UTC


def parse_timezone(value: str) -> tzinfo:
    if value.upper() in {"UTC", "Z"}:
        return UTC
    match = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", value)
    if match:
        hours, minutes = int(match[2]), int(match[3])
        if hours < 24 and minutes < 60:
            offset = timedelta(hours=hours, minutes=minutes)
            return timezone(offset if match[1] == "+" else -offset)
    else:
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    raise ValueError(f"无法识别的时区 '{value}'，请使用 UTC、+08:00 或系统支持的 IANA 时区名称")


def set_default_timezone(value: str = "UTC") -> None:
    global _default_timezone
    _default_timezone = parse_timezone(value)


def default_timezone() -> tzinfo:
    return _default_timezone


def utc_stamp(value: datetime) -> datetime:
    """无偏移时间按配置解释；比较和排序始终使用 UTC。"""
    return (value if value.tzinfo is not None else value.replace(tzinfo=_default_timezone)).astimezone(UTC)


def _micro(frac: str | None) -> int:
    return int((frac or "0")[:6].ljust(6, "0"))


_MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov",
                                       "Dec"), start=1)}
# nginx / Apache：[30/Sep/2026:14:00:00 +0800]
_CLF_TS = re.compile(r"\[(?P<d>\d{2})/(?P<mon>[A-Z][a-z]{2})/(?P<y>\d{4}):(?P<t>\d{2}:\d{2}:\d{2})(?:\.(?P<f>\d+))?"
                     r" (?P<z>[+-]\d{4})\]")
# syslog（RFC 3164）：Sep 30 14:00:00，可带 <PRI>；没有年份
_SYSLOG_TS = re.compile(r"^(?:<\d{1,3}>)?(?P<mon>[A-Z][a-z]{2}) {1,2}(?P<d>\d{1,2}) (?P<t>\d{2}:\d{2}:\d{2})(?:\.(?P<f>\d+))?\b")
# Tomcat / JUL：30-Sep-2026 14:00:00.123
_DMY_TS = re.compile(r"^(?P<d>\d{2})-(?P<mon>[A-Z][a-z]{2})-(?P<y>\d{4}) (?P<t>\d{2}:\d{2}:\d{2})(?:[.,](?P<f>\d+))?\b")
# glog / klog：E0930 14:00:00.123456；没有年份
_GLOG_TS = re.compile(r"^[IWEF](?P<m>\d{2})(?P<d>\d{2}) (?P<t>\d{2}:\d{2}:\d{2})\.(?P<f>\d+)\s")
# 行首的 Unix 时间戳（秒 / 毫秒），如 squid：1780279200.123
_EPOCH_START = re.compile(r"^(?P<e>\d{10}(?:\.\d{1,9})?|\d{13})(?=[\s,|])")
_LOGFMT_TS = re.compile(r"(?:^|\s)(?:ts|time|timestamp|t)=\"?(?P<v>[^\s\"]+)\"?")
_JSON_TS_KEYS = ("timestamp", "@timestamp", "time", "ts", "@t", "t", "datetime", "date")


def _now() -> datetime:
    return datetime.now(_default_timezone)


def _infer_year(month: int, day: int) -> int:
    """syslog / glog 不带年份：按今年算；落在“明天之后”说明是去年的日志。"""
    now = _now()
    try:
        candidate = date(now.year, month, day)
    except ValueError:  # 2 月 29 日
        return now.year - 1
    return now.year - 1 if candidate > now.date() + timedelta(days=1) else now.year


def _from_epoch(value: float) -> datetime | None:
    for scale in (1, 1e3, 1e6, 1e9):  # 秒 / 毫秒 / 微秒 / 纳秒
        seconds = value / scale
        if 946684800 <= seconds < 4102444800:  # 2000-01-01 ~ 2100-01-01
            return datetime.fromtimestamp(seconds, tz=UTC)
    return None


def _clock(text: str, frac: str | None) -> time:
    return time.fromisoformat(text).replace(microsecond=_micro(frac))


def _named_month(match: re.Match[str]) -> datetime | None:
    month = _MONTHS.get(match["mon"])
    if month is None:
        return None
    groups = match.groupdict()
    year = int(groups["y"]) if groups.get("y") else _infer_year(month, int(match["d"]))
    zone = parse_timezone(groups["z"]) if groups.get("z") else None
    return datetime.combine(date(year, month, int(match["d"])), _clock(match["t"], groups.get("f")), tzinfo=zone)


def _parse_value(value: object) -> Stamp | None:
    """JSON / logfmt 字段里的时间：ISO 字符串或数字时间戳。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return _from_epoch(float(value))
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"\d{10,19}(?:\.\d+)?", text):
            return _from_epoch(float(text))
        match = TS_RE.search(text)
        if match and match.group("date"):
            return _iso(match)
        if match:  # 旧行为：JSON 里只有时刻的字符串
            return time.fromisoformat(match.group("tonly")).replace(microsecond=_micro(match.group("tfrac")))
    return None


def _iso(match: re.Match[str]) -> Stamp:
    day = date.fromisoformat(match.group("date").replace("/", "-"))
    clock = time.fromisoformat(match.group("time")).replace(microsecond=_micro(match.group("frac")))
    zone = parse_timezone(match.group("zone")) if match.group("zone") else None
    return datetime.combine(day, clock, tzinfo=zone)


# strptime 里表示“哪一天”的指令；一个都没有就是纯时钟格式
_DATE_DIRECTIVE = re.compile(r"%[djmbBhxcUWV]")


def parse_custom_time(text: str, time_format: str | None) -> Stamp | None:
    """自定义格式里 time 分组的解析：给了 time_format 就按它来，否则按内置规则识别。"""
    text = text.strip()
    if time_format:
        has_year = "%Y" in time_format or "%y" in time_format or "%G" in time_format
        if not has_year and not _DATE_DIRECTIVE.search(time_format):
            # 只有时钟（%H:%M:%S）：strptime 会补成 1900-01-01，当成“只有时间”处理，和内置格式一致
            try:
                return datetime.strptime(text, time_format).timetz()
            except ValueError:
                return None
        try:
            if has_year:
                return datetime.strptime(text, time_format)
            # 没有年份时 strptime 默认 1900（不是闰年），02-29 会解析失败；先用闰年解析再推断年份
            value = datetime.strptime(f"2000 {text}", f"%Y {time_format}")
        except ValueError:
            return None
        year = _infer_year(value.month, value.day)
        for candidate in range(year, year - 8, -1):
            try:
                return value.replace(year=candidate)
            except ValueError:  # 02-29 落在非闰年，往前找最近的闰年
                continue
        return None
    if re.fullmatch(r"\d{10,19}(?:\.\d+)?", text):
        return _from_epoch(float(text))
    # 先按带日期的写法找（分组里通常不带 nginx 的方括号，补上再试），最后才接受“只有时刻”
    for candidate in (text, f"[{text}]"):
        found = _find_text_timestamp(candidate, scan=len(candidate))
        if found and isinstance(found[1], datetime):
            return found[1]
    found = _find_text_timestamp(text, scan=len(text))
    return found[1] if found else None


def _find_text_timestamp(line: str, scan: int = _SCAN_CHARS) -> tuple[str, Stamp] | None:
    source = line[:scan]
    match = TS_RE.search(source)
    try:
        if match and match.group("date"):
            return match.group(0), _iso(match)
        # 没有完整日期时，先试带日期的专用格式，再退回“只有时刻”；缩进的续行（栈帧）不会以这些格式开头
        specials = () if line[:1] in (" ", "\t") else ((_CLF_TS, 160), (_DMY_TS, scan), (_SYSLOG_TS, scan),
                                                        (_GLOG_TS, scan))
        for pattern, window in specials:
            special = pattern.search(line, 0, window)
            if special:
                if pattern is _GLOG_TS:
                    month, day = int(special["m"]), int(special["d"])
                    stamp = datetime.combine(date(_infer_year(month, day), month, day), _clock(special["t"], special["f"]))
                    return special.group(0).strip(), stamp
                stamp = _named_month(special)
                if stamp is not None:
                    return special.group(0).strip("[] "), stamp
        epoch = _EPOCH_START.match(source)
        if epoch:
            stamp = _from_epoch(float(epoch["e"]))
            if stamp is not None:
                return epoch["e"], stamp
        if "=" in source:
            logfmt = _LOGFMT_TS.search(line[:400])
            if logfmt:
                stamp = _parse_value(logfmt["v"])
                if stamp is not None:
                    return logfmt["v"], stamp
        if match:
            clock = time.fromisoformat(match.group("tonly")).replace(microsecond=_micro(match.group("tfrac")))
            return match.group(0), clock
    except ValueError:
        return None
    return None


def find_timestamp(line: str, _depth: int = 0, record: object = UNPARSED) -> tuple[str, Stamp] | None:
    """在行首附近找时间戳，返回 (原文, 解析值)；找不到或数值非法返回 None。

    支持：ISO（含 `/` 分隔、逗号毫秒、时区偏移）、nginx / Apache `[30/Sep/2026:14:00:00 +0800]`、
    syslog `Sep 30 14:00:00`、Tomcat `30-Sep-2026 14:00:00`、glog `E0930 14:00:00.123456`、
    行首 Unix 时间戳、logfmt 的 `ts=` / `time=`、JSON 的字符串或数字时间字段，
    以及配置文件里的自定义格式；Docker / CRI 容器日志先看应用自己的时间，没有再用运行时时间。
    """
    if custom_formats():
        custom = match_custom(line)
        if custom:
            fmt, match = custom
            raw = fmt.group(match, ("time", "ts", "timestamp"))
            # 格式没有时间分组（只定义了 level）时继续走内置识别，否则整份文件的时间窗口都失效
            if raw is not None:
                stamp = parse_custom_time(raw, fmt.time_format)
                return (raw, stamp) if stamp is not None else None
    first = line[:1]
    if record is UNPARSED:
        record = line_record(line)
    if record is None and first.isdigit():
        # 热路径：最常见的 ISO 时间戳直接解析，不经过容器 / 专用格式的判断
        match = TS_RE.search(line, 0, _SCAN_CHARS)
        if match and match.group("date") and line[10:11] != "T":
            try:
                return match.group(0), _iso(match)
            except ValueError:
                return None
    if _depth == 0 and (record is not None or maybe_wrapped(line)):
        wrapped = unwrap(line, record)
        if wrapped:
            inner, outer = wrapped
            found = find_timestamp(inner, _depth + 1) if inner else None
            if found and isinstance(found[1], datetime):
                return found
            stamp = _parse_value(outer) if outer else None
            return (outer, stamp) if stamp is not None else found
    if record is not None:
        for key in _JSON_TS_KEYS:
            if key in record:
                stamp = _parse_value(record[key])
                if stamp is not None:
                    return str(record[key]), stamp
        return None
    return _find_text_timestamp(line)


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
    normalized = raw.replace("/", "-").replace("T", " ")
    zone = None
    suffix = re.search(r"(Z|[+-]\d{2}:?\d{2})$", normalized)
    if suffix:
        zone = parse_timezone(suffix[1])
        normalized = normalized[:suffix.start()]
    for fmt, precision in _DATETIME_FORMATS:
        try:
            value = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        value = value.replace(tzinfo=zone)
        return Bound(raw, value + precision - timedelta(microseconds=1) if upper else value)
    if zone is not None:
        raise ValueError(f"带时区的时间边界需要完整日期：{raw}")
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
    if isinstance(value, datetime):
        return value.astimezone(_default_timezone).time() if value.tzinfo is not None else value.time()
    return value


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
            left, right = utc_stamp(stamp), utc_stamp(bound.value)  # type: ignore[arg-type,assignment]
        return (left > right) - (left < right)

    def contains(self, stamp: Stamp) -> bool:
        if self.since and self._compare(stamp, self.since) < 0:
            return False
        return not (self.until and self._compare(stamp, self.until) > 0)

    def past_end(self, stamp: Stamp) -> bool:
        """是否已明显越过上界，可以提前结束扫描（只在双方都有完整日期时判断）。"""
        if not self.until or self.until.time_only or not isinstance(stamp, datetime):
            return False
        return utc_stamp(stamp) > utc_stamp(self.until.value) + _OUT_OF_ORDER_TOLERANCE  # type: ignore[arg-type]


def parse_window(since: str | None, until: str | None) -> TimeWindow:
    """解析一对边界；空字符串视为不限。

    Raises:
        ValueError: 格式无法识别，或起点晚于终点。
    """
    lower = parse_bound(since, upper=False) if since and since.strip() else None
    upper = parse_bound(until, upper=True) if until and until.strip() else None
    if lower and upper and lower.time_only == upper.time_only and TimeWindow._compare(lower.value, upper) > 0:
        raise ValueError(f"起始时间 {lower.raw} 晚于结束时间 {upper.raw}")
    return TimeWindow(lower, upper)


class WindowTracker:
    """逐行判断是否在窗口内。没有时间戳的行（堆栈、多行 SQL）沿用上一条日志的时间。"""

    def __init__(self, window: TimeWindow) -> None:
        self.window = window
        self.current: Stamp | None = None
        self.saw_timestamp = False
        self.done = False

    def accept(self, line: str, found: object = UNPARSED) -> bool:
        """found：调用方已经算过的 find_timestamp(line) 结果，避免重复解析。"""
        if not self.window:
            return True
        if found is UNPARSED:
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
