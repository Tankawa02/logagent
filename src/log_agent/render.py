"""终端渲染：启动面板、工具生命周期、任务进度、流式正文与底部常驻状态栏。"""

from __future__ import annotations

import os
import re
import time
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.segment import Segment
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .citations import CitationLinker, LinkedMarkdown
from .netguard import describe_api_error, retry_watch
from .subtrace import SubagentTracker, SubCall
from .term import REFRESH_PER_SECOND, console, glyphs

# 工具名 -> (类别, 友好中文名)。类别决定图标与颜色：日志 / 源码 / 其它
_TOOL_META: dict[str, tuple[str, str]] = {
    "log_overview": ("log", "日志概览"),
    "compare_windows": ("log", "窗口对比"),
    "read_log_chunk": ("log", "读取日志"),
    "search_logs": ("log", "搜索日志"),
    "trace_request": ("log", "追踪请求"),
    "list_code_files": ("code", "浏览源码"),
    "read_code_file": ("code", "读取源码"),
    "grep_code": ("code", "检索源码"),
    "read_file": ("other", "读取文件"),
    "task": ("other", "委派子任务"),
    "suggest_memory": ("other", "建议记忆"),
}

_PHASE_BY_KIND = {"log": "正在查看日志", "code": "正在阅读源码", "other": "正在执行工具"}

_MAX_VALUE_CELLS = 48

# 底部 Live 区里每个运行中的子代理最多展示最近几步，避免并行委派时把状态栏挤出屏幕。
_LIVE_SUBCALLS = 3


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------


def format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def content_to_text(content: Any) -> str:
    """把消息 content 统一转成纯文本（兼容字符串与结构化内容块列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return str(content) if content else ""


def shorten_path(value: str, keep: int = 3) -> str:
    """把长路径截成 …/最后几段，同时兼容 `/`、`\\` 以及两者混用的 Windows 路径。"""
    raw = str(value).rstrip("/\\")
    sep = "\\" if "\\" in raw else "/"
    parts = [p for p in re.split(r"[\\/]+", raw) if p]
    if len(parts) <= keep + 1:
        return str(value)
    return glyphs.ellipsis + sep + sep.join(parts[-keep:])


def _path_name(value: str) -> str:
    parts = [p for p in re.split(r"[\\/]+", str(value).rstrip("/\\")) if p]
    return parts[-1] if parts else str(value)


def _clip(value: str, limit: int = _MAX_VALUE_CELLS) -> str:
    text = Text(value)
    if text.cell_len <= limit:
        return value
    text.truncate(limit, overflow="ellipsis")
    return text.plain


def shimmer(label: str, now: float) -> Text:
    """一道高光从左往右扫过文字（Claude Code 式“思考中”动效），只用 16 色，任何终端都能显示。"""
    text = Text()
    span = len(label) + 8
    head = (now * 14) % span - 4
    for index, char in enumerate(label):
        distance = abs(index - head)
        if distance < 1:
            style = "shimmer.peak"
        elif distance < 2.5:
            style = "shimmer.edge"
        else:
            style = "shimmer.base"
        text.append(char, style=style)
    return text


# ---------------------------------------------------------------------------
# 启动信息面板 / 统计行
# ---------------------------------------------------------------------------


def display_path(path: str, max_width: int) -> str:
    """面板里展示的路径：家目录缩成 ~；仍然太长时从中间省略目录，始终保留完整的文件名。

    直接交给 Rich 折行会把文件名拆成 `app.l` / `og` 两行，恰好把最关键的部分弄得最难认。
    """
    home = str(Path.home())
    if home not in ("", "/") and (path == home or path.startswith(home + os.sep)):
        path = "~" + path[len(home):]
    if cell_len(path) <= max_width:
        return path
    sep = "\\" if "\\" in path and "/" not in path else "/"
    head, _, name = path.rpartition(sep)
    if not head:
        return path
    parts = head.split(sep)
    anchor = sep.join(parts[:1]) + sep
    kept: list[str] = []
    budget = max_width - cell_len(anchor) - cell_len(glyphs.ellipsis) - cell_len(name) - 2
    for part in reversed(parts[1:]):
        if cell_len(part) + 1 > budget:
            break
        kept.insert(0, part)
        budget -= cell_len(part) + 1
    middle = sep.join([glyphs.ellipsis, *kept])
    return f"{anchor}{middle}{sep}{name}"


def info_panel(rows: list[tuple[str, Text | str]], title: str, subtitle: str = "", footer: list[Text] | None = None) -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="accent.strong", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in rows:
        grid.add_row(label, value if isinstance(value, Text) else Text(value))

    body: RenderableType = grid
    if footer:
        body = Group(grid, Text(""), *footer)

    heading = Text.assemble((f"{glyphs.code} ", "accent.strong"), (title, "bold"))
    if subtitle:
        heading.append(f" {glyphs.sep} {subtitle}", style="muted")
    return Panel.fit(body, title=heading, title_align="left", border_style="accent", box=glyphs.box, padding=(1, 2))


def usage_from_message(msg: Any) -> dict[str, int]:
    usage = getattr(msg, "usage_metadata", None) or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
    }


def collect_usage(messages: list[Any]) -> dict[str, int]:
    """汇总本轮（自上一条 human 消息之后）所有 AI 消息的 token 用量。"""
    totals = {"input": 0, "output": 0, "total": 0}
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", "")
        if msg_type == "human":
            break
        if msg_type == "ai":
            for key, value in usage_from_message(msg).items():
                totals[key] += value
    return totals


def collect_ai_texts(messages: list[Any]) -> str:
    """收集本轮所有 AI 文本消息（按时间顺序）拼成完整报告。

    跟着工具调用的短文本是过程旁白，不算报告正文；只有旁白、没有别的文本时才退而用它们。
    """
    collected: list[str] = []
    narration: list[str] = []
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", "")
        if msg_type == "human":
            break
        if msg_type == "ai":
            text = content_to_text(getattr(msg, "content", "")).strip()
            if not text:
                continue
            if getattr(msg, "tool_calls", None) and not looks_like_report(text):
                narration.append(text)
            else:
                collected.append(text)
    return "\n\n".join(reversed(collected or narration))


_TRAIL_MAX_STEPS = 8


def tool_trail(records: list[ToolRecord]) -> Text | None:
    """非 verbose 模式下，把主代理的工具过程折叠成一行，例如：查了 6 步：日志概览 → 搜索日志 ×3 → 委派子任务。

    连续的同名工具合并计数；子代理内部的步骤计入总步数，但不单独列出。
    """
    main = [r for r in records if not r.subagent]
    if not main:
        return None
    steps: list[list] = []
    for record in main:
        label = _TOOL_META.get(record.name, ("other", record.name))[1]
        if steps and steps[-1][0] == label:
            steps[-1][1] += 1
        else:
            steps.append([label, 1])
    shown = [f"{label} ×{count}" if count > 1 else label for label, count in steps[:_TRAIL_MAX_STEPS]]
    if len(steps) > _TRAIL_MAX_STEPS:
        shown.append(glyphs.ellipsis)
    trail = Text(f"查了 {len(records)} 步：", style="muted")
    trail.append(" → ".join(shown), style="muted")
    trail.append("   加 -v 查看每一步", style="muted")
    trail.no_wrap = True
    trail.overflow = "ellipsis"
    return trail


def print_stats(elapsed: float, usage: dict[str, int], tool_count: int, interrupted: bool = False) -> None:
    sep = f" {glyphs.sep} "
    title = Text()
    if interrupted:
        title.append(f"{glyphs.fail} 已中断", style="warn")
    else:
        title.append(f"{glyphs.ok} 完成", style="ok")
    title.append(sep + format_duration(elapsed), style="muted")
    if tool_count:
        title.append(f"{sep}工具 {tool_count} 次", style="muted")
    if usage["total"] > 0:
        title.append(
            f"{sep}{glyphs.up}{usage['input']:,} {glyphs.down}{usage['output']:,}{sep}共 {usage['total']:,} tokens",
            style="muted",
        )
    else:
        title.append(f"{sep}tokens 未知", style="muted")
    console.print()
    console.print(Rule(title, style="muted", align="right", characters=glyphs.rule))


# ---------------------------------------------------------------------------
# 工具调用
# ---------------------------------------------------------------------------


def _tool_parts(name: str, args: dict[str, Any]) -> list[tuple[str, str]]:
    """挑出最能说明“这一步在干什么”的参数，返回 [(文本, 样式)]。"""
    def arg(key: str) -> str:
        value = args.get(key)
        return "" if value in (None, "") else str(value)

    def num(key: str, default: int) -> int:
        try:
            return int(args.get(key) or default)
        except (TypeError, ValueError):
            return default

    def search_flags() -> str:
        flags = []
        if args.get("regex") is False:
            flags.append("文本")
        if args.get("ignore_case"):
            flags.append("忽略大小写")
        if num("context", 0):
            flags.append(f"±{num('context', 0)}")
        if name == "search_logs" and (arg("since") or arg("until")):
            flags.append(f"{arg('since') or '…'}~{arg('until') or '…'}")
        if arg("path_glob"):
            flags.append(_clip(arg("path_glob"), 16))
        return " ".join(flags)

    def window() -> str:
        since, until = arg("since"), arg("until")
        if not since and not until:
            return ""
        return f"{since or '…'}~{until or '…'}"

    parts: list[tuple[str, str]] = []
    if name == "log_overview":
        if arg("path"):
            parts.append((_path_name(arg("path")), "muted"))
        if window():
            parts.append((window(), "accent"))
    elif name == "compare_windows":
        if arg("path"):
            parts.append((_path_name(arg("path")), "muted"))
        parts.append((f"{arg('baseline_since')}~{arg('baseline_until')}", "accent"))
        parts.append(("vs " + (window() or "全文"), "accent"))
    elif name == "read_log_chunk":
        if arg("path"):
            parts.append((_path_name(arg("path")), "muted"))
        start = num("start_line", 1)
        count = num("num_lines", 200)
        parts.append((f"L{start}-{start + count - 1}", "accent"))
    elif name == "search_logs":
        if arg("path"):
            parts.append((_path_name(arg("path")), "muted"))
        if arg("pattern"):
            parts.append((f'"{_clip(arg("pattern"))}"', "accent"))
        if search_flags():
            parts.append((search_flags(), "muted"))
    elif name == "trace_request":
        if arg("key"):
            parts.append((f'"{_clip(arg("key"))}"', "accent"))
        paths = args.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        if len(paths) == 1:
            parts.append((_path_name(paths[0]), "muted"))
        elif paths:
            parts.append((f"{len(paths)} 份日志", "muted"))
        if window():
            parts.append((window(), "muted"))
    elif name == "list_code_files":
        if arg("code_dir"):
            parts.append((shorten_path(arg("code_dir"), keep=2), "muted"))
        if arg("path_glob"):
            parts.append((_clip(arg("path_glob"), 24), "accent"))
    elif name == "read_code_file":
        if arg("rel_path"):
            parts.append((_clip(arg("rel_path")), "accent"))
        start = num("start_line", 1)
        end = num("end_line", 0)
        if start > 1 or end:
            parts.append((f"L{start}-{end}" if end else f"L{start}-", "accent"))
        if arg("code_dir"):
            parts.append((_path_name(arg("code_dir")), "muted"))
    elif name == "grep_code":
        if arg("pattern"):
            parts.append((f'"{_clip(arg("pattern"))}"', "accent"))
        if search_flags():
            parts.append((search_flags(), "muted"))
        if arg("code_dir"):
            parts.append((_path_name(arg("code_dir")), "muted"))
    elif name == "read_file":
        path = arg("file_path")
        segments = [p for p in path.split("/") if p]
        if len(segments) >= 3 and segments[0] == "skills":
            parts.append((f"skill {segments[2]}", "accent"))
            if segments[3:] != ["SKILL.md"]:
                parts.append((_clip("/".join(segments[3:]), 32), "muted"))
        elif segments[:1] == ["large_tool_results"]:
            parts.append(("转存的工具结果", "accent"))
        elif path:
            parts.append((_clip(path), "muted"))
        if num("offset", 0):
            parts.append((f"offset {num('offset', 0)}", "muted"))
    elif name == "task":
        if arg("subagent_type"):
            parts.append((arg("subagent_type"), "accent"))
        if arg("description"):
            parts.append((_clip(" ".join(arg("description").split()), 40), "muted"))
    elif name == "suggest_memory":
        if arg("text"):
            parts.append((_clip(" ".join(arg("text").split()), 40), "muted"))
    else:
        for key, value in list(args.items())[:2]:
            if isinstance(value, (str, int, float)) and str(value):
                parts.append((_clip(f"{key}={value}", 32), "muted"))
    return parts


_HINT_SUMMARIES = {
    "no_match": "无命中",
    "eof": "已到文件末尾",
    "empty": "内容为空",
    "empty_window": "时间窗口内无日志",
    "no_timestamp": "无时间戳",
}


def _summarize_meta(name: str, meta: dict[str, Any]) -> str:
    sep = f" {glyphs.sep} "
    if name == "log_overview":
        parts = [f"{meta.get('total_lines', 0):,} 行"]
        if meta.get("window"):
            parts.append(f"窗口内 {meta.get('window_lines', 0):,} 行")
        errors = meta.get("errors", 0)
        parts.append(f"ERROR {errors:,}" if errors else "无 ERROR")
        if meta.get("cached"):
            parts.append("缓存")
        return sep.join(parts)
    if name == "compare_windows":
        summary = f"ERROR {meta.get('base_errors', 0):,} → {meta.get('target_errors', 0):,}"
        new = meta.get("new_signatures", 0)
        return f"{summary}{sep}新出现 {new} 类" if new else summary
    if name == "read_log_chunk":
        return f"{meta['end'] - meta['start'] + 1} 行"
    if name == "search_logs":
        return f"命中 {meta.get('hits', 0)}{'+' if meta.get('truncated') else ''} 行"
    if name == "trace_request":
        hits = f"命中 {meta.get('hits', 0)}{'+' if meta.get('truncated') else ''} 行"
        total = meta.get("total_files", 1)
        return f"{hits}{sep}{meta.get('files', 0)}/{total} 份日志" if total > 1 else hits
    if name == "list_code_files":
        shown, total = meta.get("shown", 0), meta.get("total", 0)
        return f"{shown}/{total} 个文件" if total > shown else f"{shown} 个文件"
    if name == "read_code_file":
        start, end, total = meta["start"], meta["end"], meta["total_lines"]
        return f"{total} 行" if start == 1 and end == total else f"L{start}-{end} / {total} 行"
    if name == "grep_code":
        more = "+" if meta.get("truncated") else ""
        return f"命中 {meta.get('hits', 0)}{more} 处{sep}{meta.get('files', 0)} 个文件"
    return ""


def summarize_tool_output(name: str, output: Any) -> tuple[str, bool]:
    """把工具输出压缩成一句结果摘要，返回 (摘要, 是否出错)。

    优先读取工具附带的结构化 artifact（见 tools.ToolOutput），不解析给模型看的正文。
    """
    text = content_to_text(getattr(output, "content", output)).strip()
    first_line = text.splitlines()[0] if text else ""
    if getattr(output, "status", "success") == "error":
        return first_line or "执行失败", True
    if name == "task":
        return (f"返回 {len(text):,} 字取证结果" if text else "子代理无输出"), not text

    artifact = getattr(output, "artifact", None)
    if not isinstance(artifact, dict):
        # 第三方工具或旧版消息没有 artifact，只按前缀判断状态
        if first_line.startswith("[错误]"):
            return first_line.removeprefix("[错误]").strip(), True
        return "", False

    status = artifact.get("status")
    if status == "error":
        return str(artifact.get("message") or first_line or "执行失败"), True
    if status == "hint":
        return _HINT_SUMMARIES.get(str(artifact.get("kind")), str(artifact.get("message") or "")), False
    try:
        return _summarize_meta(name, artifact), False
    except (KeyError, TypeError):
        return "", False


@dataclass
class ToolRun:
    call_id: str
    name: str
    args: dict[str, Any]
    handle: Any
    started: float = field(default_factory=time.perf_counter)
    note: str = ""

    @property
    def kind(self) -> str:
        return _TOOL_META.get(self.name, ("other", self.name))[0]

    @property
    def label(self) -> str:
        return _TOOL_META.get(self.name, ("other", self.name))[1]

    @property
    def completed(self) -> bool:
        return bool(getattr(self.handle, "completed", False))


class ToolLine:
    """单行工具展示：左侧“图标 + 名称 + 关键参数”，右侧“结果 · 耗时”。

    宽度不够时只截断参数部分，结果与耗时永远完整可见；绝不折行，Live 高度才稳定。
    """

    def __init__(self, run: ToolRun, icon: Text, meta: Text, nested: bool = False) -> None:
        self.run = run
        self.icon = icon
        self.meta = meta
        self.nested = nested

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # 留 1 列余量：经典 conhost 写满最后一列会触发自动换行
        width = max(options.max_width - 1, 20)
        left = Text("  ")
        if self.nested:
            left.append(f"  {glyphs.branch} ", style="muted")
        left.append_text(self.icon)
        left.append(" ")
        left.append(self.run.label, style="bold")
        for value, style in _tool_parts(self.run.name, self.run.args):
            left.append("  ")
            left.append(value, style=style)

        meta = self.meta
        room = width - meta.cell_len - 2
        if room < 12:
            meta = Text("")
            room = width
        if left.cell_len > room:
            left.truncate(room, overflow="ellipsis")
        line = left
        if meta.plain:
            line = Text.assemble(left, "  ", meta)
        line.no_wrap = True
        yield line


def _tool_icon(kind: str) -> Text:
    icon = {"log": glyphs.log, "code": glyphs.code}.get(kind, glyphs.other)
    return Text(icon, style=f"tool.{kind}")


def settled_tool_line(run: ToolRun, nested: bool = False) -> ToolLine:
    ended = getattr(run.handle, "ended", None)
    duration = format_duration((ended or time.perf_counter()) - run.started)
    error = getattr(run.handle, "error", None)
    if error:
        summary, failed = str(error).splitlines()[0], True
    else:
        summary, failed = summarize_tool_output(run.name, getattr(run.handle, "output", None))

    meta = Text()
    if summary:
        meta.append(_clip(summary, 40), style="err" if failed else "muted")
        meta.append(f" {glyphs.sep} ", style="muted")
    meta.append(duration, style="muted")

    icon = Text(glyphs.fail, style="err") if failed else Text(glyphs.ok, style="ok")
    icon.append(" ")
    icon.append_text(_tool_icon(run.kind))
    return ToolLine(run, icon, meta, nested=nested)


# 超过这么长、或出现标题 / 一句话结论，就当作正式报告边写边输出；更短的先憋住，等消息结束再判断是不是旁白
_NARRATION_MAX_CHARS = 300
_HEADING = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)
_MD_NOISE = re.compile(r"^[\s>#*\-+]+|[*`_]+")


def looks_like_report(text: str) -> bool:
    if len(text) > _NARRATION_MAX_CHARS or _HEADING.search(text):
        return True
    return any(parse_summary_line(line) for line in text.split("\n"))


def condense_note(text: str) -> str:
    """把工具调用前的旁白压成一行：取第一句非空内容，去掉 Markdown 记号。"""
    for line in text.splitlines():
        line = _MD_NOISE.sub("", line).strip()
        if line:
            return line
    return ""


def note_line(note: str, nested: bool = False) -> Text:
    line = Text("  ")
    if nested:
        line.append(f"  {glyphs.branch} ", style="muted")
    line.append(f"{glyphs.notice} ", style="accent")
    line.append(note, style="muted")
    line.no_wrap = True
    line.overflow = "ellipsis"
    return line


def _sub_run(call: SubCall) -> ToolRun:
    return ToolRun(call_id="", name=call.name, args=call.args, handle=call, started=call.started)


# ---------------------------------------------------------------------------
# 流式正文
# ---------------------------------------------------------------------------


def split_complete_blocks(text: str) -> tuple[str, str]:
    """把已完整的 Markdown 块与还在生成中的尾部拆开（Claude Code 式增量固化）。

    以空行作为块边界，且绝不在未闭合的 ``` / ~~~ 代码围栏内部切分。
    返回 (可固化部分, 剩余未完成部分)。
    """
    lines = text.split("\n")
    in_fence = False
    last_safe = -1
    for i, line in enumerate(lines[:-1]):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence and stripped == "":
            last_safe = i
    if last_safe < 0:
        return "", text
    return "\n".join(lines[:last_safe]), "\n".join(lines[last_safe + 1 :])


class MarkdownTail:
    """把“正在生成中的半个块”按真实 Markdown 样式渲染，只显示末尾若干行。

    预览与最终固化使用同一套渲染，块写完落到上方时不会有样式跳变，观感连续。
    渲染结果按 (文本, 宽度) 缓存，Live 的高频刷新不会重复解析 Markdown。
    """

    def __init__(self) -> None:
        self.text = ""
        self.max_lines = 6
        self._cache_key: tuple[str, int] | None = None
        self._cache_lines: list[list[Segment]] = []

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        text = self.text
        width = options.max_width
        if (text, width) != self._cache_key:
            lines = console.render_lines(Markdown(text), options.update(height=None), pad=False)
            while lines and not "".join(seg.text for seg in lines[-1]).strip():
                lines.pop()
            self._cache_key = (text, width)
            self._cache_lines = lines
        for line in self._cache_lines[-self.max_lines :]:
            yield from line
            yield Segment.line()


# ---------------------------------------------------------------------------
# 主渲染器
# ---------------------------------------------------------------------------


# 报告开头的一句话结论，例如 "一句话结论：Router.select 未判空（可信度：高）"；容忍加粗、引用块等包装
_SUMMARY_LINE = re.compile(
    r"^\s*(?:>\s*)?\**\s*一句话结论\s*\**\s*[:：]\s*\**\s*(?P<text>.+?)\s*\**\s*"
    r"(?:[（(]\s*可信度\s*[:：]?\s*(?P<level>高|中|低)\s*[)）])?\s*\**\s*$"
)
_CONFIDENCE_STYLE = {"高": "ok", "中": "warn", "低": "err"}
NO_FINDING_PREFIX = "未发现异常"


def parse_summary_line(line: str) -> tuple[str, str] | None:
    """识别一句话结论行，返回 (结论, 可信度)；可信度缺失时为空字符串。"""
    match = _SUMMARY_LINE.match(line)
    if not match:
        return None
    text = match.group("text").strip().strip("*").strip()
    return (text, match.group("level") or "") if text else None


def summary_banner(text: str, level: str) -> Text:
    banner = Text()
    banner.append(f"{glyphs.notice} ", style="accent.strong")
    banner.append(text, style="bold")
    if level:
        banner.append(f"  {glyphs.sep}  ", style="muted")
        banner.append(f"可信度 {level}", style=_CONFIDENCE_STYLE[level])
    return banner


@dataclass
class ToolRecord:
    name: str
    args: dict[str, Any]
    summary: str
    failed: bool
    seconds: float
    subagent: str = ""
    note: str = ""


@dataclass
class TurnResult:
    """一轮执行的结果，供导出报告与会话累计统计使用。"""

    report: str = ""
    elapsed: float = 0.0
    usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0})
    tools: list[ToolRecord] = field(default_factory=list)
    interrupted: bool = False
    error: str = ""
    summary: str = ""
    confidence: str = ""
    budget_hit: bool = False

    @property
    def finding(self) -> bool | None:
        """是否发现了问题；没有一句话结论时无法判定，返回 None。"""
        if not self.summary:
            return None
        return not self.summary.startswith(NO_FINDING_PREFIX)

    @property
    def ok(self) -> bool:
        return not self.interrupted and not self.error


class StreamRenderer:
    """一轮 agent 执行的完整渲染。

    - 底部常驻 Live：流式正文预览 + 运行中的工具 + 动态状态栏；
      后台刷新线程让计时与动效在等待模型/工具时也持续跳动。
    - 上方永久区：完整 Markdown 块、已完成的工具行（verbose）。
    """

    def __init__(self, verbose: bool, linker: CitationLinker | None = None, budget: Any = None) -> None:
        self.verbose = verbose
        self.linker = linker
        self.budget = budget
        self.summary: tuple[str, str] | None = None
        self.start = time.perf_counter()
        self.spinner = Spinner(glyphs.spinner, style="accent")
        self.tail = MarkdownTail()
        self.running: list[ToolRun] = []
        self.seen_calls: set[str] = set()
        self.tool_count = 0
        self.usage = {"input": 0, "output": 0, "total": 0}
        self.pending_chunks = 0
        self.writing = False
        self.rendered_any = False
        self.printed_answer_rule = False
        self.interrupted = False
        self.answer_parts: list[str] = []
        self.records: list[ToolRecord] = []
        self.pending_note = ""
        self.tracker = SubagentTracker()

    # ---- 统计（主代理 + 子代理）--------------------------------------------

    def _total_usage(self) -> dict[str, int]:
        sub = self.tracker.usage
        return {key: self.usage[key] + sub[key] for key in self.usage}

    def _total_tools(self) -> int:
        return self.tool_count + self.tracker.tool_count

    # ---- Live 视图 ---------------------------------------------------------

    def _phase(self) -> str:
        active = [run for run in self.running if not run.completed]
        if active:
            tasks = [run for run in active if run.name == "task"]
            if len(active) > 1:
                if len(tasks) == len(active):
                    return f"{len(tasks)} 个子代理并行取证"
                return f"并行执行 {len(active)} 个工具"
            if tasks:
                return "子代理取证中"
            return _PHASE_BY_KIND.get(active[0].kind, "正在执行工具")
        if self.writing:
            return "正在撰写"
        if self.rendered_any:
            return "继续推理"
        return "思考中"

    def _status_line(self, now: float) -> Text:
        line = Text("  ")
        frame = self.spinner.render(now)
        line.append_text(frame if isinstance(frame, Text) else Text(str(frame)))
        line.append(" ")
        line.append_text(shimmer(self._phase() + glyphs.ellipsis, now))

        sep = f"  {glyphs.sep}  "
        retry_note = retry_watch.active_note()
        if retry_note:
            line.append(sep)
            line.append(retry_note, style="warn")
        meta = Text(sep + format_duration(now - self.start), style="muted")
        approx = self._total_usage()["total"] + self.pending_chunks
        if approx:
            prefix = "~" if self.pending_chunks else ""
            meta.append(f"{sep}{prefix}{approx:,} tokens")
        tools = self._total_tools()
        if tools:
            meta.append(f"{sep}工具 {tools}")
        meta.append(f"{sep}Ctrl+C 中断")
        line.append_text(meta)
        line.no_wrap = True
        line.overflow = "ellipsis"
        return line

    def __rich__(self) -> RenderableType:
        now = time.perf_counter()
        parts: list[RenderableType] = []

        running = list(self.running)

        tool_lines: list[RenderableType] = []
        for run in running:
            if run.note:
                tool_lines.append(note_line(run.note))
            if run.completed:
                tool_lines.append(settled_tool_line(run))
                continue
            tool_lines.append(self._running_line(run, now))
            if run.name == "task":
                tool_lines.extend(self._live_subcalls(run, now))

        if self.tail.text.strip():
            reserved = 4 + len(tool_lines)
            self.tail.max_lines = max(1, min(12, console.size.height - reserved))
            parts.extend([self.tail, Text("")])

        parts.extend(tool_lines)

        if running:
            parts.append(Text(""))
        parts.append(self._status_line(now))
        return Group(*parts)

    def _running_line(self, run: ToolRun, now: float, nested: bool = False) -> ToolLine:
        icon = self.spinner.render(now)
        icon = icon.copy() if isinstance(icon, Text) else Text(str(icon))
        icon.stylize(f"tool.{run.kind}")
        meta = Text(format_duration(now - run.started), style="muted")
        return ToolLine(run, icon, meta, nested=nested)

    def _live_subcalls(self, task: ToolRun, now: float) -> list[RenderableType]:
        calls = self.tracker.children(task.call_id)
        hidden = max(0, len(calls) - _LIVE_SUBCALLS)
        lines: list[RenderableType] = []
        if hidden:
            more = Text(f"    {glyphs.branch} ", style="muted")
            more.append(f"{glyphs.ellipsis} 已执行 {hidden} 步", style="muted")
            more.no_wrap = True
            lines.append(more)
        for call in calls[hidden:]:
            run = _sub_run(call)
            lines.append(settled_tool_line(run, nested=True) if call.completed else self._running_line(run, now, nested=True))
        return lines

    # ---- 永久输出 -----------------------------------------------------------

    def _flush_answer(self, text: str) -> None:
        if not text.strip():
            return
        if not self.printed_answer_rule:
            console.print()
            console.print(Rule(Text("分析结果", style="accent.strong"), style="muted", characters=glyphs.rule))
            self.printed_answer_rule = True
        for part in self._split_summary(text):
            console.print()
            console.print(part if isinstance(part, Text) else self._markdown(part))
        self.answer_parts.append(text)
        self.rendered_any = True

    def _markdown(self, text: str) -> Markdown:
        if self.linker is None or not self.linker.enabled:
            return Markdown(text)
        return LinkedMarkdown(self.linker.apply(text))

    def _split_summary(self, text: str) -> list[str | Text]:
        """把本轮第一处"一句话结论"行换成高亮横幅，其余部分照常按 Markdown 渲染。"""
        if self.summary is not None:
            return [text]
        lines = text.split("\n")
        for i, line in enumerate(lines):
            parsed = parse_summary_line(line)
            if parsed is None:
                continue
            self.summary = parsed
            before, after = "\n".join(lines[:i]).strip(), "\n".join(lines[i + 1 :]).strip()
            return [p for p in (before, summary_banner(*parsed), after) if p]
        return [text]

    def _record(self, run: ToolRun, subagent: str = "") -> None:
        error = getattr(run.handle, "error", None)
        if error:
            summary, failed = str(error).splitlines()[0], True
        else:
            summary, failed = summarize_tool_output(run.name, getattr(run.handle, "output", None))
        ended = getattr(run.handle, "ended", None) or time.perf_counter()
        self.records.append(
            ToolRecord(
                run.name, dict(run.args), summary, failed, round(ended - run.started, 3), subagent, run.note,
            )
        )

    def _settle(self, run: ToolRun) -> None:
        self._record(run)
        if self.verbose:
            if run.note:
                console.print(note_line(run.note))
            console.print(settled_tool_line(run))
        if run.name != "task":
            return
        subagent = str(run.args.get("subagent_type") or "general-purpose")
        for call in self.tracker.children(run.call_id):
            sub = _sub_run(call)
            self._record(sub, subagent)
            if self.verbose:
                console.print(settled_tool_line(sub, nested=True))

    def _settle_tools(self) -> None:
        still_running = []
        for run in self.running:
            if run.completed:
                self._settle(run)
            else:
                still_running.append(run)
        self.running = still_running

    # ---- 事件处理 -----------------------------------------------------------

    def _on_message(self, message_stream: Any) -> None:
        self._settle_tools()
        self.pending_note = ""
        buffer = ""
        streamed = False
        # 工具调用前模型常先说一句"先看看整体分布"：流式阶段还不知道后面有没有工具调用，
        # 所以短文本先只放在 Live 预览里，消息结束后再决定是旁白还是报告正文。
        holding = True
        self.writing = False
        for delta in message_stream.text:
            if not delta:
                continue
            if not streamed:
                self._settle_tools()
            streamed = True
            self.writing = True
            self.rendered_any = True
            buffer += delta
            self.pending_chunks += 1
            if holding and looks_like_report(buffer):
                holding = False
            if holding:
                self.tail.text = buffer
                continue
            flushable, remainder = split_complete_blocks(buffer)
            if flushable.strip():
                self.tail.text = remainder
                self._flush_answer(flushable)
                buffer = remainder
            else:
                self.tail.text = buffer

        output = getattr(message_stream, "output", None)
        final_text = buffer if streamed else content_to_text(getattr(output, "content", ""))
        self.tail.text = ""
        self.writing = False
        self.pending_chunks = 0
        if output is not None:
            for key, value in usage_from_message(output).items():
                self.usage[key] += value
        final_text = final_text.strip()
        if holding and getattr(output, "tool_calls", None) and not looks_like_report(final_text):
            self.pending_note = condense_note(final_text)
            return
        self._flush_answer(final_text)

    def _on_tool(self, tool_stream: Any) -> None:
        name = getattr(tool_stream, "tool_name", "") or ""
        args = getattr(tool_stream, "input", None) or {}
        if not isinstance(args, dict):
            args = {"input": args}
        call_id = getattr(tool_stream, "tool_call_id", None) or f"{name}:{args}"
        if call_id in self.seen_calls:
            return
        self.seen_calls.add(call_id)

        self.tool_count += 1
        # 旁白只挂在这一批的第一个工具上，并行的其它工具不重复显示
        self.running.append(ToolRun(call_id=call_id, name=name, args=args, handle=tool_stream, note=self.pending_note))
        self.pending_note = ""
        self.rendered_any = True

    # ---- 入口 ---------------------------------------------------------------

    def _with_tracker(self, config: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(config or {})
        handlers = [self.tracker] + ([self.budget] if self.budget is not None else [])
        callbacks = merged.get("callbacks")
        if callbacks is None:
            merged["callbacks"] = handlers
        elif isinstance(callbacks, list):
            merged["callbacks"] = [*callbacks, *handlers]
        else:
            callbacks = callbacks.copy()
            for handler in handlers:
                callbacks.add_handler(handler, inherit=True)
            merged["callbacks"] = callbacks
        return merged

    def run(self, agent: Any, payload: dict[str, Any], config: dict[str, Any] | None = None) -> TurnResult:
        """执行一轮并渲染，返回本轮结果（报告正文、耗时、用量、工具记录、是否中断）。"""
        from langgraph.errors import GraphRecursionError

        final_state: Any = None
        error = ""
        config = self._with_tracker(config)
        if self.budget is not None:
            self.budget.reset()
        retry_watch.reset()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*v3 streaming protocol on Pregel is experimental.*")
            live = Live(
                self,
                console=console,
                refresh_per_second=REFRESH_PER_SECOND,
                vertical_overflow="crop",
                transient=True,
            )
            try:
                with live:
                    with agent.stream_events(payload, config=config, version="v3") as stream:
                        for kind, item in stream.interleave("messages", "tool_calls"):
                            if kind == "messages":
                                self._on_message(item)
                            elif kind == "tool_calls":
                                self._on_tool(item)
                        final_state = stream.output
                    self._settle_tools()
            except KeyboardInterrupt:
                self.interrupted = True
                # 已经流出来但还没固化的半个块也保留下来，用户能看到中断前的内容
                self._flush_answer(self.tail.text)
                self.tail.text = ""
            except GraphRecursionError:
                self._flush_answer(self.tail.text)
                self.tail.text = ""
                error = "已达到最大推理步数"
                console.print()
                console.print(
                    Text(
                        f"{glyphs.fail} {error}，agent 可能在反复搜索。可以换个更具体的问题，"
                        "或用 --max-steps 调大上限。",
                        style="warn",
                    )
                )
            except Exception as exc:
                message = describe_api_error(exc)
                if message is None:
                    raise
                self._flush_answer(self.tail.text)
                self.tail.text = ""
                error = message
                console.print()
                console.print(Text(f"{glyphs.fail} {message}", style="err"))
                if self.answer_parts:
                    console.print(Text("已输出的部分内容会照常保存。", style="muted"))

        if not self.interrupted and not error:
            if not self.printed_answer_rule and isinstance(final_state, dict):
                text = collect_ai_texts(final_state.get("messages", []))
                if text:
                    self._flush_answer(text)
            if not self.printed_answer_rule:
                console.print(Text("未获取到模型输出。", style="muted"))
            if self.usage["total"] == 0 and isinstance(final_state, dict):
                self.usage = collect_usage(final_state.get("messages", []))

        for run in self.running:
            if run.completed:
                self._settle(run)
        elapsed = time.perf_counter() - self.start
        usage = self._total_usage()
        trail = None if self.verbose else tool_trail(self.records)
        if trail is not None:
            console.print()
            console.print(trail)
        budget_hit = self.budget is not None and self.budget.wrapped
        if budget_hit:
            from .budget import format_tokens

            console.print(Text(
                f"{glyphs.notice} 接近 tokens 预算 {format_tokens(self.budget.limit)}，"
                "已让 agent 停止取证、基于现有证据收尾；需要查得更深可以调大 --budget。",
                style="warn",
            ))
        print_stats(elapsed, usage, self._total_tools(), self.interrupted or bool(error))
        return TurnResult(
            report="\n\n".join(self.answer_parts),
            elapsed=round(elapsed, 3),
            usage=usage,
            tools=list(self.records),
            interrupted=self.interrupted,
            error=error,
            summary=self.summary[0] if self.summary else "",
            confidence=self.summary[1] if self.summary else "",
            budget_hit=budget_hit,
        )
