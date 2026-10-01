"""共享的日志字段识别：结构化字段优先，普通文本保持兼容。

识别顺序（先命中先用）：
1. 配置文件里的自定义格式（`[[log_formats]]`）
2. 容器包装：Docker json-file（`{"log": "...", "stream": ..., "time": ...}`）、CRI / containerd
   （`2026-...Z stdout F <原始行>`）——先拆出里面的应用日志再识别
3. JSON：level / severity / levelname / severityText / lvl / loglevel / log.level / @l，
   pino / bunyan 的数字级别（30=INFO、50=ERROR…），Serilog CLEF 无 @l 时为 INFO
4. glog / klog：`E0930 14:02:03.123456 1234 file.go:42] msg`
5. nginx / Apache 访问日志：按 HTTP 状态码判级别（5xx=ERROR、4xx=WARN）
6. logfmt：`level=error msg="..." status=502`
7. 普通文本里的级别单词；syslog 的 `<PRI>` 作为兜底
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# 先用首字母前瞻排除大部分位置，再按公共前缀分组：比逐个尝试所有单词快 2~3 倍（每行都要跑）
LEVEL_RE = re.compile(
    r"\b(?=[acdefinpstw])(FATAL|CRIT(?:ICAL)?|EMERG|ALERT|PANIC|SEVERE|FINE(?:R|ST)?|ERR(?:OR)?|WARN(?:ING)?|NOTICE"
    r"|INFO(?:RMATION)?|DEBUG|TRACE)\b",
    re.IGNORECASE,
)
# 这些词在普通文本、logger / 线程名里很常见（alert-dispatcher、panic recovered、everything is fine），
# 只在像“级别字段”时才算：全大写的独立 token，或者写在方括号里（nginx / Apache 的 [crit]、[core:notice]）
_STRICT_LEVELS = frozenset({"CRIT", "EMERG", "ALERT", "PANIC", "NOTICE", "FINE", "FINER", "FINEST"})
_TOKEN_BEFORE = frozenset(" \t[(<")
_TOKEN_AFTER = frozenset(" \t])>:,")
LEVEL_ALIAS = {
    "CRITICAL": "FATAL", "CRIT": "FATAL", "EMERG": "FATAL", "ALERT": "FATAL", "PANIC": "FATAL", "SEVERE": "FATAL",
    "ERR": "ERROR", "EROR": "ERROR", "WARNING": "WARN", "WARNG": "WARN", "NOTICE": "INFO", "INFORMATION": "INFO",
    "DBUG": "DEBUG", "TRCE": "TRACE", "VERBOSE": "TRACE", "FINE": "DEBUG", "FINER": "TRACE", "FINEST": "TRACE",
}
LEVELS = ("FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE")


def find_text_level(text: str) -> tuple[str, int] | None:
    """在普通文本里找级别词，返回 (归一化级别, 级别词结束位置)。

    按出现顺序取第一个合格的；_STRICT_LEVELS 里的词要求是独立 token（不贴着 `-` / `.`），
    并且全大写或写在方括号里；行首的 Go `panic:` 也算。
    """
    first = LEVEL_RE.search(text)
    if first is None:
        return None
    upper = first.group(1).upper()
    if upper not in _STRICT_LEVELS:  # 热路径：绝大多数行第一个就是 ERROR / INFO 这类核心级别词
        return LEVEL_ALIAS.get(upper, upper), first.end()
    for match in LEVEL_RE.finditer(text, first.start()):
        word = match.group(1)
        upper = word.upper()
        if upper in _STRICT_LEVELS:
            start, end = match.span(1)
            before = text[start - 1] if start else " "
            after = text[end] if end < len(text) else " "
            bracketed = before in "[:" and after == "]" and (before == "[" or "[" in text[:start])
            go_panic = upper == "PANIC" and start == 0 and after == ":"
            standalone = before in _TOKEN_BEFORE and after in _TOKEN_AFTER and word == upper
            if not (bracketed or go_panic or standalone):
                continue
        return LEVEL_ALIAS.get(upper, upper), match.end()
    return None

CODE_FIELDS = ("status", "status_code", "statusCode", "http_status", "code", "error_code", "errorCode")

_JSON_LEVEL_KEYS = ("level", "severity", "levelname", "severityText", "lvl", "loglevel", "log_level", "log.level", "@l")
_JSON_MESSAGE_KEYS = ("message", "msg", "@m", "@mt", "event", "error.message")
# pino / bunyan
_NUMERIC_LEVELS = {10: "TRACE", 20: "DEBUG", 30: "INFO", 40: "WARN", 50: "ERROR", 60: "FATAL"}
# syslog 严重度：0-2 紧急 / 告警 / 严重，3 错误，4 警告，5-6 通知 / 信息，7 调试
_SYSLOG_SEVERITY = ("FATAL", "FATAL", "FATAL", "ERROR", "WARN", "INFO", "INFO", "DEBUG")


def normalize_level(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        # 1e999 解析出来是 inf，int() 会抛 OverflowError；30.5 这种也不是合法的数字级别
        if not math.isfinite(value) or not value.is_integer():
            return None
        value = int(value)
    if isinstance(value, int):
        return _NUMERIC_LEVELS.get(value)
    text = str(value).strip().upper()
    if text.isascii() and text.isdigit():
        return _NUMERIC_LEVELS.get(int(text)) if len(text) <= 3 else None
    text = LEVEL_ALIAS.get(text, text)
    return text if text in LEVELS else None


def json_record(line: str) -> dict[str, Any] | None:
    # 防止超长单行解析引入额外的大量内存开销。
    if len(line) > 65536 or not line.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def lookup(record: dict[str, Any], key: str) -> Any:
    """支持 `log.level` 这类点号路径，也支持键名本身带点。"""
    if key in record:
        return record[key]
    value: Any = record
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


# ---------------------------------------------------------------------------
# 自定义格式
# ---------------------------------------------------------------------------

_TIME_GROUPS = ("time", "ts", "timestamp")
_MESSAGE_GROUPS = ("message", "msg")


@dataclass
class CustomFormat:
    name: str
    pattern: re.Pattern[str]
    time_format: str | None = None
    levels: dict[str, str] = field(default_factory=dict)

    def group(self, match: re.Match[str], names: Iterable[str]) -> str | None:
        groups = match.groupdict()
        return next((groups[n] for n in names if groups.get(n) is not None), None)


_custom: list[CustomFormat] = []


def parse_custom_formats(specs: Any) -> list[CustomFormat]:
    """校验并编译配置文件里的 `[[log_formats]]`。

    Raises:
        ValueError: 字段缺失、正则无效、缺少命名分组、级别映射非法，或 sample 与格式不符。
    """
    if specs is None:
        return []
    if isinstance(specs, dict):
        specs = [specs]
    if not isinstance(specs, list):
        raise ValueError("log_formats 必须写成 [[log_formats]] 表数组")
    formats: list[CustomFormat] = []
    for index, spec in enumerate(specs, start=1):
        if not isinstance(spec, dict):
            raise ValueError(f"log_formats 第 {index} 项必须是表")
        name = str(spec.get("name") or f"custom-{index}")
        where = f"log_formats[{name}]"
        unknown = set(spec) - {"name", "pattern", "time_format", "levels", "sample"}
        if unknown:
            raise ValueError(f"{where}: 不认识的字段 {', '.join(sorted(unknown))}")
        raw = spec.get("pattern")
        if not isinstance(raw, str) or not raw:
            raise ValueError(f"{where}: 缺少 pattern（带命名分组的正则）")
        try:
            pattern = re.compile(raw)
        except re.error as exc:
            raise ValueError(f"{where}: pattern 不是合法的正则：{exc}") from exc
        names = set(pattern.groupindex)
        if not names & {*_TIME_GROUPS, "level"}:
            raise ValueError(f"{where}: pattern 至少要有 (?P<time>…) 或 (?P<level>…) 命名分组")
        levels_raw = spec.get("levels") or {}
        if not isinstance(levels_raw, dict):
            raise ValueError(f"{where}: levels 必须是表，例如 {{ E = \"ERROR\" }}")
        levels: dict[str, str] = {}
        for key, value in levels_raw.items():
            level = normalize_level(value)
            if level is None:
                raise ValueError(f"{where}: levels.{key} = {value!r} 不是可识别的级别（{' / '.join(LEVELS)}）")
            levels[str(key).upper()] = level
        time_format = spec.get("time_format")
        if time_format is not None and (not isinstance(time_format, str) or "%" not in time_format):
            raise ValueError(f"{where}: time_format 必须是 strptime 格式，例如 \"%d.%m.%Y %H:%M:%S\"")
        fmt = CustomFormat(name, pattern, time_format, levels)
        sample = spec.get("sample")
        if sample is not None:
            _check_sample(fmt, str(sample), where)
        formats.append(fmt)
    return formats


def _check_sample(fmt: CustomFormat, sample: str, where: str) -> None:
    from .timefilter import parse_custom_time

    match = fmt.pattern.match(sample)
    if not match:
        raise ValueError(f"{where}: sample 与 pattern 不匹配：{sample}")
    stamp = fmt.group(match, _TIME_GROUPS)
    if stamp is not None and parse_custom_time(stamp, fmt.time_format) is None:
        raise ValueError(f"{where}: sample 里的时间 {stamp!r} 无法按 time_format={fmt.time_format!r} 解析")
    if "level" in fmt.pattern.groupindex and _custom_level(fmt, match) is None:
        raise ValueError(f"{where}: sample 里的级别 {match['level']!r} 无法识别，可用 levels 映射")


def set_custom_formats(specs: Any) -> list[CustomFormat]:
    global _custom
    _custom = parse_custom_formats(specs)
    return _custom


def custom_formats() -> list[CustomFormat]:
    return _custom


def match_custom(line: str) -> tuple[CustomFormat, re.Match[str]] | None:
    for fmt in _custom:
        match = fmt.pattern.match(line)
        if match:
            return fmt, match
    return None


def _custom_level(fmt: CustomFormat, match: re.Match[str]) -> str | None:
    raw = match.groupdict().get("level")
    if raw is None:
        return None
    raw = raw.strip()
    return fmt.levels.get(raw.upper()) or normalize_level(raw)


# ---------------------------------------------------------------------------
# 容器包装
# ---------------------------------------------------------------------------

_CRI = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})) (?:stdout|stderr) [FP] ")


def maybe_wrapped(line: str) -> bool:
    """热路径预判：只有以 `{` 开头或形如 `2026-09-30T…` 的行才可能是容器包装。"""
    first = line[:1]
    return first == "{" or (first.isdigit() and line[10:11] == "T")


_DOCKER_KEYS = frozenset({"log", "stream", "time", "attrs"})


def _is_docker_record(record: dict[str, Any]) -> bool:
    """Docker json-file / fluent 转发的容器行，而不是恰好有个 `log` 字段的业务 JSON（如 Serilog）。"""
    if any(k in record for k in _JSON_LEVEL_KEYS) or any(k in record for k in _JSON_MESSAGE_KEYS):
        return False
    if any(k.startswith("@") for k in record):
        return False
    return record.keys() <= _DOCKER_KEYS or record.get("stream") in ("stdout", "stderr")


def unwrap(line: str, record: dict[str, Any] | None = None) -> tuple[str, str] | None:
    """拆出容器运行时包在外面的一层，返回 (应用原始行, 外层时间戳)；不是容器格式返回 None。"""
    first = line[:1]
    if record is None and first == "{":
        record = json_record(line)
    if record is not None:
        inner = record.get("log")
        if isinstance(inner, str) and _is_docker_record(record):
            outer = record.get("time")
            return inner.rstrip("\r\n"), outer if isinstance(outer, str) else ""
        return None
    # CRI 以 `2026-09-30T...` 开头：先用固定位置的字符快速排除普通日志，热路径上不切片
    if first.isdigit() and line[10:11] == "T" and line.find(" std", 19, 48) != -1:
        match = _CRI.match(line)
        if match:
            return line[match.end():], match["ts"]
    return None


# ---------------------------------------------------------------------------
# 各格式的级别识别
# ---------------------------------------------------------------------------

_GLOG = re.compile(r"^(?P<level>[IWEF])\d{4} \d{2}:\d{2}:\d{2}\.\d+\s+\d+\s+[^\s\]]+:\d+\]\s?(?P<body>.*)$")
_GLOG_LEVELS = {"I": "INFO", "W": "WARN", "E": "ERROR", "F": "FATAL"}
_ACCESS = re.compile(
    # 方括号里必须是 CLF 时间 [30/Sep/2026:14:00:00 +0800]，否则普通应用日志里的 [thread] "GET ..." 也会被当成访问日志
    r'^\S+ \S+ \S+ \[\d{2}/[A-Z][a-z]{2}/\d{4}:[^\]]+\] "(?P<method>[A-Z]+) (?P<path>[^" ]+)[^"]*" (?P<status>\d{3}) '
)
_LOGFMT_START = re.compile(r"^\s*[\w.@-]+=")
_LOGFMT_LEVEL = re.compile(r"(?:^|\s)(?:level|lvl|severity|log\.level)=\"?(?P<level>[A-Za-z]+)\"?(?=\s|$)")
_LOGFMT_MSG = re.compile(r'(?:^|\s)(?:msg|message)=(?P<msg>"(?:[^"\\]|\\.)*"|\S+)')
_LOGFMT_CODE = re.compile(r"(?:^|\s)(?P<pair>(?:status|status_code|http_status|code|error_code|err_code)=\"?[\w.-]+\"?)")
_SYSLOG_PRI = re.compile(r"^<(?P<pri>\d{1,3})>")


def _json_level(record: dict[str, Any]) -> tuple[str, str] | None:
    level = None
    for key in _JSON_LEVEL_KEYS:
        value = lookup(record, key)
        if value is None:
            continue
        level = normalize_level(value)
        if level is None:
            return None  # 有级别字段但值认不出：不猜
        break
    if level is None:
        if "@mt" in record or "@m" in record:
            level = "INFO"  # Serilog CLEF：省略 @l 表示 Information
        else:
            # JSON 的正文中可能提到 ERROR；没有级别字段时不猜测其日志级别。
            return None
    message = next((lookup(record, k) for k in _JSON_MESSAGE_KEYS if lookup(record, k) is not None), "")
    if not isinstance(message, str):
        message = json.dumps(message, ensure_ascii=False, sort_keys=True)
    codes = [f"{key}={record[key]}" for key in CODE_FIELDS if isinstance(record.get(key), (str, int))]
    return level, " ".join([*codes, message]).strip()


def _logfmt_level(line: str) -> tuple[str, str] | None:
    if not _LOGFMT_START.match(line):
        return None
    match = _LOGFMT_LEVEL.search(line)
    if not match:
        return None
    level = normalize_level(match["level"])
    if level is None:
        return None
    msg_match = _LOGFMT_MSG.search(line)
    message = msg_match["msg"].strip('"') if msg_match else ""
    codes = [m["pair"].replace('"', "") for m in _LOGFMT_CODE.finditer(line)]
    return level, " ".join([*codes, message]).strip()


def _access_level(line: str) -> tuple[str, str] | None:
    match = _ACCESS.match(line)
    if not match:
        return None
    status = int(match["status"])
    level = "ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO"
    path = match["path"].split("?", 1)[0]
    return level, f"status={status} {match['method']} {path}"


_CONTINUATION_PREFIXES = ("Caused by:", "Suppressed:", "During handling of the above exception",
                          "The above exception was the direct cause")


def is_stack_line(line: str) -> bool:
    """堆栈里的续行：栈帧、`Caused by:`、`... 12 more`。

    包名 / 路径里常有 error、info 之类的单词（`at com.acme.error.Handler.run(...)`），
    不能当成日志级别，否则既会虚增 ERROR 数，也会把一段堆栈拆散。
    """
    first = line[:1]
    if first not in (" ", "\t"):
        # 热路径：绝大多数日志行以数字 / 字母开头，只有少数前缀需要进一步判断
        return first in "CSDT" and line.startswith(_CONTINUATION_PREFIXES)
    stripped = line.lstrip()
    if not stripped:
        return False
    if stripped.startswith(_CONTINUATION_PREFIXES):
        return True
    if stripped.startswith("... ") and stripped.rstrip().endswith(("more", "omitted")):
        return True
    from .stacktrace import is_frame_line

    return is_frame_line(line)


# 调用方已经解析过 JSON 时把结果传进来（None 表示“不是 JSON”），避免同一行被反复 json.loads
UNPARSED: Any = object()


def line_record(line: str) -> dict[str, Any] | None:
    first = line[:1]
    return json_record(line) if first == "{" or (first in " \t" and line.lstrip()[:1] == "{") else None


def level_and_body(line: str, _depth: int = 0, record: Any = UNPARSED) -> tuple[str, str] | None:
    if is_stack_line(line):
        return None
    if _custom:
        custom = match_custom(line)
        if custom:
            fmt, match = custom
            body = fmt.group(match, _MESSAGE_GROUPS)
            if "level" in fmt.pattern.groupindex:
                level = _custom_level(fmt, match)
                if level is None:
                    return None
                return level, (body if body is not None else line[match.end("level"):]).strip()
            # 格式只定义了时间：级别交给内置识别（优先看 message 分组），不再重复匹配自定义格式
            return _builtin_level(body if body is not None else line, _depth)
    return _builtin_level(line, _depth, record)


def _builtin_level(line: str, _depth: int = 0, record: Any = UNPARSED) -> tuple[str, str] | None:
    first = line[:1]
    if record is UNPARSED:
        record = line_record(line)
    if _depth == 0 and (record is not None or maybe_wrapped(line)):
        wrapped = unwrap(line, record)
        if wrapped:
            return level_and_body(wrapped[0], _depth + 1)
    if record is not None:
        return _json_level(record)
    if line[:1] in "IWEF":
        glog = _GLOG.match(line)
        if glog:
            return _GLOG_LEVELS[glog["level"]], glog["body"]
    if line.find('" ', 0, 600) != -1:
        access = _access_level(line)
        if access:
            return access
    if first.isalpha() and line.find("=", 0, 200) != -1:  # logfmt 以键名开头
        logfmt = _logfmt_level(line)
        if logfmt:
            return logfmt
    found = find_text_level(line[:200])
    if found:
        return found[0], line[found[1]:].lstrip(" ]:|-\t")
    pri = _SYSLOG_PRI.match(line)
    if pri and int(pri["pri"]) < 192:
        return _SYSLOG_SEVERITY[int(pri["pri"]) % 8], line[pri.end():].strip()
    return None


# ---------------------------------------------------------------------------
# 格式识别（inspect 用）
# ---------------------------------------------------------------------------


def detect_format(line: str) -> str | None:
    """这一行像哪种格式；用于 inspect 报告识别情况，不参与分析。"""
    if _custom:
        custom = match_custom(line)
        if custom:
            return f"自定义：{custom[0].name}"
    record = json_record(line) if line[:1] == "{" else None
    wrapped = unwrap(line, record)
    if wrapped:
        inner = detect_format(wrapped[0]) if wrapped[0] else None
        kind = "Docker json-file" if record is not None else "CRI / containerd"
        return f"{kind} → {inner}" if inner and inner != "文本" else kind
    if record is not None:
        if "@mt" in record or "@m" in record or "@t" in record:
            return "JSON（Serilog CLEF）"
        if isinstance(record.get("level"), int):
            return "JSON（pino / bunyan）"
        return "JSON"
    if _GLOG.match(line):
        return "glog / klog"
    if _ACCESS.match(line):
        return "nginx / Apache 访问日志"
    if _LOGFMT_START.match(line) and _LOGFMT_LEVEL.search(line):
        return "logfmt"
    if _SYSLOG_PRI.match(line):
        return "syslog"
    from .timefilter import find_timestamp

    found = find_timestamp(line)
    if found:
        return "文本"
    return None
