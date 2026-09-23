"""自定义工具：让 agent 能安全地读取日志文件、检索源码（全部只读、纯 Python、跨平台）。

每个工具返回 `ToolOutput`：它本身是 str（发给模型的正文，失败以 `[错误]`、无结果以 `[提示]`
开头，方便模型理解），同时带 `status` 与 `meta` 结构化字段。注册给 agent 时经
`as_langchain_tools()` 包装，`meta` 作为 ToolMessage.artifact 传给展示层，不进入模型上下文。
"""

from __future__ import annotations

import fnmatch
import functools
import os
import re
import shutil
import subprocess
import time
from collections import Counter, deque
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from .logfile import HIT_LINE_CHARS, MAX_LINE_CHARS, clip_line, open_log, read_text_file
from .redact import is_enabled as redact_enabled
from .redact import redact_code, redact_log
from .timefilter import TimeWindow, WindowTracker, default_window, find_timestamp, parse_window

Status = Literal["ok", "hint", "error"]


class ToolOutput(str):
    """工具结果：字符串内容 + 结构化状态。继承 str，直接调用工具函数时用法与普通字符串一致。"""

    status: Status
    meta: dict[str, Any]

    def __new__(cls, text: str, status: Status = "ok", **meta: Any) -> ToolOutput:
        obj = super().__new__(cls, text)
        obj.status = status
        obj.meta = meta
        return obj

    @property
    def artifact(self) -> dict[str, Any]:
        return {"status": self.status, **self.meta}


def _ok(text: str, **meta: Any) -> ToolOutput:
    return ToolOutput(text, "ok", **meta)


def _hint(message: str, kind: str, **meta: Any) -> ToolOutput:
    return ToolOutput(f"[提示] {message}", "hint", kind=kind, message=message, **meta)


def _err(message: str) -> ToolOutput:
    return ToolOutput(f"[错误] {message}", "error", message=message)

# 搜索时跳过的目录
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".idea", ".mypy_cache", ".pytest_cache",
}

# 只检索这些代码 / 文本扩展名（白名单），其它一律跳过。
# 比黑名单更可靠：真实仓库里的二进制类型五花八门，列举永远不全。
CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".scala", ".groovy",
    ".go", ".rs", ".rb", ".php", ".pl", ".pm",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx",
    ".cs", ".swift", ".m", ".mm", ".dart", ".lua", ".r",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".md", ".txt", ".env", ".properties", ".gradle",
    ".vue", ".svelte", ".astro",
    ".tf", ".tfvars", ".dockerfile",
}

CODE_FILENAMES = {
    "dockerfile", "makefile", "rakefile", "gemfile", "procfile",
    ".gitignore", ".dockerignore", ".env",
}

MAX_SEARCH_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_CHUNK_LINES = 1000
MAX_CODE_LINES = 400
MAX_CONTEXT_LINES = 10

# Windows 保留设备名：os.walk 可能列出同名文件/junction，传给 relpath/abspath 会被解析成
# \\.\nul 之类的设备路径并抛 ValueError，直接跳过。
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _is_code_file(name: str) -> bool:
    lower = name.lower()
    if lower in CODE_FILENAMES:
        return True
    return Path(name).suffix.lower() in CODE_EXTENSIONS


def _safe_relpath(root: str, name: str, base: Path) -> str | None:
    """纯词法计算相对路径，不触碰文件系统，也不会把 Windows 保留名解析成设备路径。"""
    if os.name == "nt" and name.split(".")[0].lower() in _WINDOWS_RESERVED:
        return None
    try:
        return str((Path(root) / name).relative_to(base))
    except ValueError:
        return None


def _compile(pattern: str, regex: bool, ignore_case: bool) -> tuple[re.Pattern[str], str]:
    """编译搜索模式。正则无效时自动退化为普通文本搜索，而不是直接报错让模型重试。"""
    flags = re.IGNORECASE if ignore_case else 0
    if not regex:
        return re.compile(re.escape(pattern), flags), ""
    try:
        return re.compile(pattern, flags), ""
    except re.error:
        return re.compile(re.escape(pattern), flags), "（正则无效，已按普通文本搜索）"


def _open_log_or_error(path: str):
    try:
        return open_log(path), None
    except FileNotFoundError:
        return None, _err(f"日志文件不存在: {path}")
    except OSError as exc:
        return None, _err(f"无法打开日志: {exc}")


def _resolve_window(since: str, until: str) -> tuple[TimeWindow, ToolOutput | None]:
    """显式传了边界就用显式的，否则沿用 CLI 设置的默认窗口。"""
    if since or until:
        try:
            return parse_window(since, until), None
        except ValueError as exc:
            return TimeWindow(), _err(str(exc))
    return default_window(), None


NO_TIMESTAMP_NOTE = "（日志中没有识别到时间戳，已忽略时间窗口，按全文处理）"


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


# ---------------------------------------------------------------------------
# 日志工具
# ---------------------------------------------------------------------------

_LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|SEVERE|ERROR|ERR|WARNING|WARN|INFO|DEBUG|TRACE)\b")
_LEVEL_ALIAS = {"CRITICAL": "FATAL", "SEVERE": "FATAL", "ERR": "ERROR", "WARNING": "WARN"}
_LEVEL_ORDER = ("FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE")
_EXC_RE = re.compile(r"\b((?:[a-z_][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error))\b")
_NORMALIZE = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"\d+"), "#"),
]


def _error_signature(line: str, level_end: int) -> str:
    """把一条错误日志归一化成"签名"：去掉时间戳、数字、id，同类错误才能聚到一起。"""
    body = line[level_end:].lstrip(" ]:|-\t")
    for pattern, repl in _NORMALIZE:
        body = pattern.sub(repl, body)
    return body[:160].strip()


class _OverviewStats:
    def __init__(self) -> None:
        self.levels: Counter[str] = Counter()
        self.signatures: Counter[str] = Counter()
        self.first_seen: dict[str, int] = {}
        self.exceptions: Counter[str] = Counter()
        self.exc_first: dict[str, int] = {}
        self.first_ts = ""
        self.last_ts = ""
        self.total = 0
        self.window_first = 0
        self.window_last = 0
        self.window_lines = 0
        self.saw_timestamp = False


def _scan_overview(log, window: TimeWindow) -> _OverviewStats:
    """扫描全文统计概览。时间范围总是按全文统计，级别/错误/异常只统计窗口内的行。"""
    stats = _OverviewStats()
    tracker = WindowTracker(window)
    for lineno, line in log.iter_lines(1):
        stats.total = lineno
        if len(line) > 4000:
            line = line[:4000]
        found = find_timestamp(line)
        if found:
            stats.saw_timestamp = True
            if not stats.first_ts:
                stats.first_ts = found[0]
            stats.last_ts = found[0]
        # 概览要报告全文行数与时间范围，所以越过窗口后也不提前结束
        if window and not tracker.accept(line):
            continue
        stats.window_lines += 1
        stats.window_first = stats.window_first or lineno
        stats.window_last = lineno
        level_match = _LEVEL_RE.search(line[:200])
        if level_match:
            level = _LEVEL_ALIAS.get(level_match.group(1), level_match.group(1))
            stats.levels[level] += 1
            if level in ("FATAL", "ERROR"):
                sig = _error_signature(line, level_match.end())
                if sig:
                    stats.signatures[sig] += 1
                    stats.first_seen.setdefault(sig, lineno)
        for exc in set(_EXC_RE.findall(line)):
            stats.exceptions[exc] += 1
            stats.exc_first.setdefault(exc, lineno)
    return stats


def log_overview(path: str, since: str = "", until: str = "") -> ToolOutput:
    """快速了解整份日志：行数、大小、编码、时间范围、各级别数量、高频错误与异常类型。

    建议作为排查的第一步调用，先掌握全局再决定搜索什么。大文件会扫描一遍全文，
    同时建立行索引，之后的 read_log_chunk 跳读会更快。

    Args:
        path: 日志文件路径。
        since: 可选，只统计该时间及之后的日志，如 `2026-06-09 14:00`、`14:00`。
        until: 可选，只统计到该时间为止（按给出的精度包含整段，`14:05` 包含 14:05:59）。
    """
    log, error = _open_log_or_error(path)
    if error:
        return error
    window, error = _resolve_window(since, until)
    if error:
        return error

    # 输出里的错误样例经过脱敏，所以脱敏开关也是缓存键的一部分
    key = ("overview", window, redact_enabled())
    with log.scan_lock:
        cached = log.scan_cache.get(key)
        if cached is not None:
            return ToolOutput(str(cached), cached.status, **{**cached.meta, "cached": True})
        result = _build_overview(log, path, window)
        if result.status != "error":
            log.scan_cache[key] = result
        return result


def _build_overview(log, path: str, window: TimeWindow) -> ToolOutput:
    note = ""
    try:
        stats = _scan_overview(log, window)
        if window and not stats.saw_timestamp:
            window, note = TimeWindow(), NO_TIMESTAMP_NOTE
            stats = _scan_overview(log, window)
    except OSError as exc:
        return _err(f"读取失败: {exc}")

    if stats.total == 0:
        return _hint(f"日志为空: {path}", "empty")
    time_range = f"{stats.first_ts} → {stats.last_ts}" if stats.first_ts else ""
    if window and stats.window_lines == 0:
        return _hint(
            f"时间窗口 {window.describe()} 内没有日志（日志时间范围：{time_range}，共 {stats.total:,} 行）。",
            "empty_window",
            total_lines=stats.total,
        )

    levels = stats.levels
    meta = [f"大小 {_human_size(log.size)}", f"共 {stats.total:,} 行", f"编码 {log.encoding}"]
    if log.gz:
        meta.append("gzip 压缩")
    out = [f"--- 日志概览 {log.path.name} ---", " · ".join(meta)]
    if note:
        out.append(note)
    if time_range:
        out.append(f"时间范围：{time_range}")
    if window:
        out.append(
            f"时间窗口：{window.describe()}，对应 L{stats.window_first}-L{stats.window_last}，"
            f"共 {stats.window_lines:,} 行（以下统计只含窗口内）"
        )
    signatures, first_seen = stats.signatures, stats.first_seen
    exceptions, exc_first = stats.exceptions, stats.exc_first
    if levels:
        ordered = [f"{name} {levels[name]:,}" for name in _LEVEL_ORDER if levels[name]]
        out.append("级别分布：" + " · ".join(ordered))
    else:
        out.append("级别分布：未识别到标准日志级别")

    if signatures:
        out.append("")
        out.append("高频错误（数字 / id 已归一化，按次数排序）：")
        for i, (sig, count) in enumerate(signatures.most_common(10), start=1):
            out.append(f"  {i}. x{count}  首次 L{first_seen[sig]}  {redact_log(clip_line(sig, 200))}")
    if exceptions:
        out.append("")
        out.append("异常类型：")
        for exc, count in exceptions.most_common(10):
            out.append(f"  {exc}  x{count}  首次 L{exc_first[exc]}")
    return _ok(
        "\n".join(out),
        total_lines=stats.total,
        errors=levels["FATAL"] + levels["ERROR"],
        warnings=levels["WARN"],
        window=window.describe() if window else None,
        window_lines=stats.window_lines if window else None,
        top_errors=[
            {"signature": redact_log(clip_line(sig, 200)), "count": count, "first_line": first_seen[sig]}
            for sig, count in signatures.most_common(5)
        ],
    )


def compare_windows(
    path: str, baseline_since: str, baseline_until: str, since: str = "", until: str = ""
) -> ToolOutput:
    """对比同一份日志里两个时间段的错误分布：基线（正常时段） vs 目标（出问题的时段）。

    一次返回两段的级别分布（次数和占比）、目标时段新出现的错误、明显增多的错误、减少或消失的错误。
    回答"为什么突然变多""和平时比有什么不同""发布前后有什么变化"时优先用它，
    比分别调两次 log_overview 再自己比对更准（已按每行出现率归一化，两段长度 / 流量不同也能比）。

    Args:
        path: 日志文件路径。
        baseline_since: 基线时段起点，如 `13:00` 或 `2026-06-09 13:00`。
        baseline_until: 基线时段终点。
        since: 目标时段起点；不传时沿用本次运行的时间窗口，没有窗口就是全文。
        until: 目标时段终点。
    """
    from .compare import render_comparison

    log, error = _open_log_or_error(path)
    if error:
        return error
    try:
        base_window = parse_window(baseline_since, baseline_until)
    except ValueError as exc:
        return _err(f"基线时段有误：{exc}")
    if not base_window.since or not base_window.until:
        return _err("基线时段需要同时给出 baseline_since 和 baseline_until")
    target_window, error = _resolve_window(since, until)
    if error:
        return error
    try:
        base = _scan_overview(log, base_window)
        target = _scan_overview(log, target_window)
    except OSError as exc:
        return _err(f"读取失败: {exc}")
    if not base.saw_timestamp:
        return _hint(f"日志里没有识别到时间戳，无法按时间段对比: {path}", "no_timestamp")
    if base.window_lines == 0:
        return _hint(
            f"基线时段 {base_window.describe()} 内没有日志（日志时间范围：{base.first_ts} → {base.last_ts}）。",
            "empty_window",
        )
    if target.window_lines == 0:
        return _hint(f"目标时段 {target_window.describe()} 内没有日志。", "empty_window")

    target_desc = target_window.describe() if target_window else "全文"
    text = render_comparison(
        log.path.name, base_window.describe(), target_desc, base, target,
        lambda sig: redact_log(clip_line(sig, 200)),
    )
    base_errors = base.levels["FATAL"] + base.levels["ERROR"]
    target_errors = target.levels["FATAL"] + target.levels["ERROR"]
    new_count = sum(1 for sig in target.signatures if sig not in base.signatures)
    return _ok(text, base_errors=base_errors, target_errors=target_errors, new_signatures=new_count)


def read_log_chunk(path: str, start_line: int = 1, num_lines: int = 200) -> ToolOutput:
    """读取日志文件的指定行区间（带行号）。

    日志通常很大，不要试图一次读完。单行超过 500 字会被截断。

    Args:
        path: 日志文件路径。
        start_line: 起始行号（从 1 开始）。
        num_lines: 读取的行数，默认 200，最多 1000。
    """
    log, error = _open_log_or_error(path)
    if error:
        return error
    start_line = max(1, int(start_line))
    num_lines = max(1, min(int(num_lines), MAX_CHUNK_LINES))
    end_line = start_line + num_lines - 1

    lines: list[str] = []
    try:
        for lineno, text in log.iter_lines(start_line):
            if lineno > end_line:
                break
            lines.append(f"{lineno}: {clip_line(text)}")
    except OSError as exc:
        return _err(f"读取失败: {exc}")

    if not lines:
        total = f"（文件共 {log.total_lines:,} 行）" if log.total_lines is not None else ""
        return _hint(f"第 {start_line} 行之后没有内容{total}。", "eof", total_lines=log.total_lines)
    last = start_line + len(lines) - 1
    total = f"，共 {log.total_lines:,} 行" if log.total_lines is not None else ""
    header = f"--- {path} 第 {start_line}-{last} 行{total} ---"
    return _ok(header + "\n" + redact_log("\n".join(lines)), start=start_line, end=last, total_lines=log.total_lines)


class _SearchResult:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.hits = 0
        self.truncated = False
        self.saw_timestamp = False


def _scan_search(
    log, compiled: re.Pattern[str], context: int, start_line: int, end_line: int, max_results: int, window: TimeWindow
) -> _SearchResult:
    result = _SearchResult()
    out = result.lines
    tracker = WindowTracker(window)
    before: deque[tuple[int, str]] = deque(maxlen=context or 1)
    after_left = 0
    last_printed = 0
    for lineno, text in log.iter_lines(start_line):
        if end_line and lineno > end_line:
            break
        if window and not tracker.accept(text):
            # 窗口外的行既不算命中也不作为上下文
            before.clear()
            after_left = 0
            if tracker.done:
                break
            continue
        match = compiled.search(text)
        if match:
            if result.hits >= max_results:
                result.truncated = True
                break
            result.hits += 1
            if context:
                first = before[0][0] if before else lineno
                if out and first > last_printed + 1:
                    out.append("--")
                out.extend(f"{no}- {clip_line(t)}" for no, t in before)
                before.clear()
            out.append(f"{lineno}: {clip_line(text, HIT_LINE_CHARS, focus=match.start())}")
            last_printed = lineno
            after_left = context
        elif after_left:
            out.append(f"{lineno}- {clip_line(text)}")
            last_printed = lineno
            after_left -= 1
        elif context:
            before.append((lineno, text))
    result.saw_timestamp = tracker.saw_timestamp
    return result


def search_logs(
    path: str,
    pattern: str,
    regex: bool = True,
    ignore_case: bool = False,
    context: int = 0,
    start_line: int = 1,
    end_line: int = 0,
    since: str = "",
    until: str = "",
    max_results: int = 50,
) -> ToolOutput:
    """在日志中搜索关键字/正则，返回命中行（`行号: 内容`）及可选的上下文行（`行号- 内容`）。

    用于快速定位 ERROR、Exception、Traceback、特定 request id 等。

    Args:
        path: 日志文件路径。
        pattern: 搜索模式。默认按正则解释，如 `ERROR|Exception`。
        regex: 为 False 时按普通文本搜索。搜索含 `[ ] ( ) . * ?` 的原文（如 `[ERROR]`、
            `foo(bar)`）时务必传 False，否则会被当成正则元字符。
        ignore_case: 是否忽略大小写。
        context: 每个命中额外返回前后各几行上下文（0-10），省去再调 read_log_chunk。
        start_line: 只搜索从该行开始的内容。
        end_line: 只搜索到该行为止，0 表示到文件末尾。
        since: 可选，只搜索该时间及之后的日志，如 `2026-06-09 14:00`、`14:00`。
            没有时间戳的行（如堆栈）沿用上一条日志的时间。
        until: 可选，只搜索到该时间为止（按给出的精度包含整段）。
        max_results: 最多返回的命中数。
    """
    log, error = _open_log_or_error(path)
    if error:
        return error
    window, error = _resolve_window(since, until)
    if error:
        return error
    compiled, note = _compile(pattern, regex, ignore_case)
    context = max(0, min(int(context), MAX_CONTEXT_LINES))
    max_results = max(1, int(max_results))
    start_line = max(1, int(start_line))
    end_line = int(end_line or 0)

    try:
        found = _scan_search(log, compiled, context, start_line, end_line, max_results, window)
        if window and not found.saw_timestamp:
            window, note = TimeWindow(), note + NO_TIMESTAMP_NOTE
            found = _scan_search(log, compiled, context, start_line, end_line, max_results, window)
    except OSError as exc:
        return _err(f"搜索失败: {exc}")

    window_note = f"（时间窗口 {window.describe()}）" if window else ""
    if not found.hits:
        return _hint(f"未匹配到 '{pattern}'{window_note}{note}。", "no_match", hits=0)
    body = redact_log("\n".join(found.lines))
    tail = (
        f"\n... 命中超过 {max_results} 条，仅显示前 {max_results} 条（可缩小行范围、时间窗口或换更精确的模式）。"
        if found.truncated
        else ""
    )
    header = f"{window_note}{note}"
    return _ok(
        (f"{header}\n" if header else "") + body + tail,
        hits=found.hits,
        truncated=found.truncated,
        window=window.describe() if window else None,
    )


# 命中行之后紧跟的无时间戳行（Java 堆栈、多行 SQL）视为同一条日志，一并带出
_TRACE_CONTINUATION = 30
MAX_TRACE_LINES = 500


class _TraceEntry:
    __slots__ = ("stamp", "file_index", "lineno", "lines")

    def __init__(self, stamp: Any, file_index: int, lineno: int, line: str) -> None:
        self.stamp = stamp
        self.file_index = file_index
        self.lineno = lineno
        self.lines = [line]


def _scan_trace(
    log, file_index: int, label: str, compiled: re.Pattern[str], window: TimeWindow, limit: int
) -> tuple[list[_TraceEntry], bool, bool]:
    """返回 (命中条目, 是否截断, 是否见到过时间戳)。"""
    tracker = WindowTracker(window)
    entries: list[_TraceEntry] = []
    stamp: Any = None
    saw_timestamp = False
    follow = 0
    for lineno, text in log.iter_lines(1):
        if window and not tracker.accept(text):
            follow = 0
            if tracker.done:
                break
            continue
        found = find_timestamp(text)
        if found:
            stamp = found[1]
            saw_timestamp = True
        match = compiled.search(text)
        if match:
            if len(entries) >= limit:
                return entries, True, saw_timestamp or tracker.saw_timestamp
            entries.append(
                _TraceEntry(stamp, file_index, lineno, f"{label}:{lineno}  {clip_line(text, HIT_LINE_CHARS, focus=match.start())}")
            )
            # 整份日志都没有时间戳时不做"续行"判断，否则每个命中都会拖出后面 30 行
            follow = _TRACE_CONTINUATION if found else 0
        elif follow and not found:
            entries[-1].lines.append(f"{label}:{lineno}- {clip_line(text)}")
            follow -= 1
        else:
            follow = 0
    return entries, False, saw_timestamp or tracker.saw_timestamp


def _trace_sort_key(entries: list[_TraceEntry]) -> Callable[[_TraceEntry], tuple]:
    from datetime import datetime

    # 有的日志只有时刻（syslog 的 `Jun 9 14:02:03` 之类），和带日期的混在一起时统一按一天中的时刻排
    time_only = any(e.stamp is not None and not isinstance(e.stamp, datetime) for e in entries)

    def key(entry: _TraceEntry) -> tuple:
        stamp = entry.stamp
        if stamp is None:
            return (0, 0, entry.file_index, entry.lineno)
        value = stamp.time() if time_only and isinstance(stamp, datetime) else stamp
        return (1, value, entry.file_index, entry.lineno)

    return key


def _trace_labels(paths: list[str]) -> list[str]:
    names = [Path(p).name or p for p in paths]
    # 不同目录下的同名日志（如两台机器的 app.log）用完整路径区分，否则引用行号会串
    return [p if names.count(n) > 1 else n for p, n in zip(paths, names, strict=True)]


def trace_request(
    paths: list[str],
    key: str,
    regex: bool = False,
    ignore_case: bool = False,
    since: str = "",
    until: str = "",
    max_lines: int = 200,
) -> ToolOutput:
    """跨一份或多份日志追踪同一个请求：找出所有包含该标识的行，按时间合并排序，得到完整链路。

    适合"这个请求为什么降级 / 失败 / 走了某个分支"这类问题：传入 traceId、requestId、订单号、
    手机号等标识，一次拿到请求进来 → 各次尝试 → 最终结果的时间线，不必多次 search_logs 自己拼。
    命中行后面紧跟的堆栈等无时间戳行会一并带出（`文件:行号-`）。

    Args:
        paths: 日志文件路径列表；只有一份日志时也传列表。
        key: 请求标识。默认按普通文本匹配；要同时追多个标识（如 traceId 和它派生的子请求 id）
            时传 `regex=True` 并写成 `id1|id2`。
        regex: 为 True 时把 key 当正则。
        ignore_case: 是否忽略大小写。
        since: 可选，只看该时间及之后的日志。
        until: 可选，只看到该时间为止。
        max_lines: 最多返回的命中行数（按时间先后截取，不含带出的堆栈行）。
    """
    if isinstance(paths, str):
        paths = [paths]
    paths = [str(p) for p in paths if str(p).strip()]
    if not paths:
        return _err("至少需要传入一份日志路径。")
    if not str(key).strip():
        return _err("key 不能为空。")
    window, error = _resolve_window(since, until)
    if error:
        return error
    compiled, note = _compile(key, regex, ignore_case)
    max_lines = max(1, min(int(max_lines), MAX_TRACE_LINES))

    logs = []
    for path in paths:
        log, error = _open_log_or_error(path)
        if error:
            return error
        logs.append(log)

    labels = _trace_labels(paths)
    entries: list[_TraceEntry] = []
    per_file: list[int] = []
    truncated = False
    any_timestamp = False
    try:
        for index, (log, label) in enumerate(zip(logs, labels, strict=True)):
            found, cut, saw = _scan_trace(log, index, label, compiled, window, max_lines)
            if window and not saw:
                # 这份日志没有时间戳：时间窗口对它无意义，按全文追踪
                found, cut, saw = _scan_trace(log, index, label, compiled, TimeWindow(), max_lines)
                note += f"（{label} 中没有识别到时间戳，已忽略时间窗口）"
            entries.extend(found)
            per_file.append(len(found))
            truncated = truncated or cut
            any_timestamp = any_timestamp or saw
    except OSError as exc:
        return _err(f"追踪失败: {exc}")

    window_note = f"（时间窗口 {window.describe()}）" if window else ""
    if not entries:
        return _hint(f"{len(paths)} 份日志中都没有 '{key}'{window_note}{note}。", "no_match", hits=0, files=0)

    entries.sort(key=_trace_sort_key(entries))
    if len(entries) > max_lines:
        entries = entries[:max_lines]
        truncated = True

    matched_files = sum(1 for count in per_file if count)
    header = [f"追踪 '{key}'：{len(paths)} 份日志中 {matched_files} 份命中，共 {sum(per_file)} 行{window_note}{note}"]
    if len(paths) > 1:
        header.append("各日志命中：" + "，".join(f"{label} {count} 行" for label, count in zip(labels, per_file, strict=True)))
    if not any_timestamp:
        header.append("（日志中没有识别到时间戳，以下按文件顺序排列）")
    header.append("--- 按时间排序 ---")
    body = "\n".join(line for entry in entries for line in entry.lines)
    tail = (
        f"\n... 命中较多，仅显示最早的 {len(entries)} 行（可缩小时间窗口，或换更精确的标识）。" if truncated else ""
    )
    return _ok(
        "\n".join(header) + "\n" + redact_log(body) + tail,
        hits=len(entries),
        files=matched_files,
        total_files=len(paths),
        truncated=truncated,
        window=window.describe() if window else None,
    )


# ---------------------------------------------------------------------------
# 源码工具
# ---------------------------------------------------------------------------

_FILE_LIST_TTL = 60.0
_file_list_cache: dict[str, tuple[float, list[str]]] = {}


def _code_files(base: Path) -> list[str]:
    """列出 base 下全部代码文件的相对路径（已排序），60 秒内复用结果。"""
    key = str(base.resolve())
    cached = _file_list_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _FILE_LIST_TTL:
        return cached[1]
    results: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not _is_code_file(name):
                continue
            rel = _safe_relpath(root, name, base)
            if rel is not None:
                results.append(rel)
    results.sort()
    _file_list_cache[key] = (now, results)
    return results


def list_code_files(code_dir: str, path_glob: str = "", max_files: int = 300) -> ToolOutput:
    """列出源码目录下的代码文件（自动跳过 node_modules、.git 等无关目录）。

    Args:
        code_dir: 源码目录路径。
        path_glob: 可选的路径过滤，如 `*.java`、`src/order/*`。
        max_files: 最多列出的文件数量。
    """
    base = Path(code_dir).expanduser()
    if not base.is_dir():
        return _err(f"代码目录不存在: {code_dir}")
    files = _code_files(base)
    if path_glob:
        files = [f for f in files if _glob_match(f, path_glob)]
    if not files:
        return _hint(f"{code_dir} 下没有匹配的代码文件。", "empty")
    shown = files[:max_files]
    tail = f"\n... 共 {len(files)} 个文件，已达上限 {max_files}，可用 path_glob 缩小范围。" if len(files) > max_files else ""
    return _ok("\n".join(shown) + tail, shown=len(shown), total=len(files))


def _glob_match(rel: str, pattern: str) -> bool:
    normalized = rel.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(normalized.rsplit("/", 1)[-1], pattern)


def read_code_file(code_dir: str, rel_path: str, start_line: int = 1, end_line: int = 0) -> ToolOutput:
    """读取源码文件的指定行区间，每行带行号（报告里引用 `文件:行号` 时以此为准）。

    不指定 end_line 时最多返回 400 行；大文件请结合 grep_code 的行号按区间读取。

    Args:
        code_dir: 源码根目录。
        rel_path: 相对于根目录的文件路径。
        start_line: 起始行号（从 1 开始）。
        end_line: 结束行号（含），0 表示从 start_line 起读 400 行。
    """
    base = Path(code_dir).expanduser().resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base):
        return _err(f"非法路径（越界）: {rel_path}")
    if not target.is_file():
        return _err(f"文件不存在: {rel_path}")
    try:
        size = target.stat().st_size
    except OSError as exc:
        return _err(f"无法访问文件: {exc}")
    if size > MAX_SEARCH_FILE_SIZE:
        return _err(
            f"文件过大（{_human_size(size)}，上限 {_human_size(MAX_SEARCH_FILE_SIZE)}），"
            f"已拒绝读取。请用 grep_code 定位行号后再按区间读取。"
        )
    try:
        all_lines = read_text_file(target).splitlines()
    except OSError as exc:
        return _err(f"读取失败: {exc}")

    total = len(all_lines)
    if total == 0:
        return _hint(f"文件为空: {rel_path}", "empty")
    start = max(1, int(start_line))
    if start > total:
        return _hint(f"第 {start} 行超出文件范围（共 {total} 行）。", "eof", total_lines=total)
    end = int(end_line or 0)
    end = min(total, end if end >= start else start + MAX_CODE_LINES - 1)
    end = min(end, start + MAX_CODE_LINES * 2 - 1)

    width = len(str(end))
    body = "\n".join(
        f"{no:>{width}} | {clip_line(all_lines[no - 1], MAX_LINE_CHARS)}" for no in range(start, end + 1)
    )
    header = f"--- {rel_path} 第 {start}-{end} 行，共 {total} 行 ---"
    tail = f"\n... 还有 {total - end} 行未显示，用 start_line={end + 1} 继续读取。" if end < total else ""
    return _ok(header + "\n" + redact_code(body) + tail, start=start, end=end, total_lines=total)


def _grep_with_rg(base: Path, compiled_src: str, literal: bool, ignore_case: bool, path_glob: str, max_results: int) -> list[str] | None:
    """用 ripgrep 加速检索；rg 不可用、正则语法不兼容或出错时返回 None，交给纯 Python 实现。"""
    rg = shutil.which("rg")
    if not rg or os.environ.get("LOG_AGENT_NO_RG"):
        return None
    args = [
        rg, "--no-heading", "--line-number", "--color", "never", "--no-messages",
        "--sort", "path", "--max-filesize", "5M", "--max-columns", str(MAX_LINE_CHARS + 50),
    ]
    if literal:
        args.append("--fixed-strings")
    if ignore_case:
        args.append("--ignore-case")
    for ext in sorted(CODE_EXTENSIONS):
        args += ["--iglob", f"*{ext}"]
    for name in sorted(CODE_FILENAMES):
        args += ["--iglob", name]
    for skip in sorted(SKIP_DIRS):
        args += ["--glob", f"!{skip}/"]
    # path_glob 不交给 rg：rg 的多个正向 glob 是并集，会绕过上面的扩展名白名单，改为下面逐行过滤
    # 必须显式给出搜索路径：stdin 不是终端时 rg 会改为搜索 stdin
    args += ["-e", compiled_src, "--", "."]

    try:
        proc = subprocess.Popen(
            args, cwd=base, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        )
    except OSError:
        return None

    matches: list[str] = []
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            m = re.match(r"^(.*?):(\d+):(.*)$", raw.rstrip("\r\n"))
            if not m:
                continue
            rel, lineno, text = m.groups()
            rel = rel.removeprefix("./").removeprefix(".\\")
            if path_glob and not _glob_match(rel, path_glob):
                continue
            matches.append(f"{rel}:{lineno}: {clip_line(text)}")
            if len(matches) > max_results:
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    # 退出码 2 = rg 报错（通常是正则语法与 Python 不兼容），回退
    if proc.returncode == 2 and not matches:
        return None
    return matches


def grep_code(
    code_dir: str,
    pattern: str,
    regex: bool = True,
    ignore_case: bool = False,
    path_glob: str = "",
    max_results: int = 80,
) -> ToolOutput:
    """在源码目录中递归搜索关键字/正则，返回 `文件:行号: 内容`。

    用于把日志里的报错信息（函数名、错误字符串、异常类名）关联回源码位置。

    Args:
        code_dir: 源码根目录。
        pattern: 搜索模式，默认按正则解释。
        regex: 为 False 时按普通文本搜索（搜索含括号、点号的原文时建议 False）。
        ignore_case: 是否忽略大小写。
        path_glob: 可选的路径过滤，如 `*.java`、`src/order/*`。
        max_results: 最多返回的命中条数。
    """
    base = Path(code_dir).expanduser()
    if not base.is_dir():
        return _err(f"代码目录不存在: {code_dir}")
    compiled, note = _compile(pattern, regex, ignore_case)
    literal = not regex or bool(note)

    matches = _grep_with_rg(base, pattern, literal, ignore_case, path_glob, max_results)
    if matches is None:
        matches = []
        for rel in _code_files(base):
            if path_glob and not _glob_match(rel, path_glob):
                continue
            fpath = base / rel
            try:
                if fpath.stat().st_size > MAX_SEARCH_FILE_SIZE:
                    continue
                content = read_text_file(fpath)
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), start=1):
                if compiled.search(line):
                    matches.append(f"{rel}:{i}: {clip_line(line)}")
                    if len(matches) > max_results:
                        break
            if len(matches) > max_results:
                break

    if not matches:
        return _hint(f"源码中未匹配到 '{pattern}'{note}。", "no_match", hits=0)
    shown = matches[:max_results]
    truncated = len(matches) > max_results
    tail = f"\n... 命中超过 {max_results} 条，仅显示前 {max_results} 条（可用 path_glob 缩小范围）。" if truncated else ""
    files = {line.split(":", 1)[0] for line in shown}
    return _ok(
        (f"{note}\n" if note else "") + redact_code("\n".join(shown)) + tail,
        hits=len(shown),
        files=len(files),
        truncated=truncated,
    )


ALL_TOOLS: list[Callable[..., ToolOutput]] = [
    log_overview,
    compare_windows,
    read_log_chunk,
    search_logs,
    trace_request,
    list_code_files,
    read_code_file,
    grep_code,
]


def _with_artifact(func: Callable[..., ToolOutput]):
    """包装成 LangChain 工具：正文给模型，结构化 meta 作为 artifact 只给展示层。"""
    from langchain_core.tools import tool

    @functools.wraps(func)
    def runner(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
        result = func(*args, **kwargs)
        if isinstance(result, ToolOutput):
            return str(result), result.artifact
        return str(result), {"status": "ok"}

    return tool(runner, response_format="content_and_artifact")


def as_langchain_tools() -> list[Any]:
    return [_with_artifact(func) for func in ALL_TOOLS]
