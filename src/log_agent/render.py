"""终端渲染：启动面板、工具生命周期、任务进度、流式正文与底部常驻状态栏。"""

from __future__ import annotations

import re
import time
import warnings
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, ConsoleOptions, Group, RenderableType, RenderResult
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.segment import Segment
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .term import REFRESH_PER_SECOND, console, glyphs

# 工具名 -> (类别, 友好中文名)。类别决定图标与颜色：日志 / 源码 / 其它
_TOOL_META: dict[str, tuple[str, str]] = {
    "log_overview": ("log", "日志概览"),
    "read_log_chunk": ("log", "读取日志"),
    "search_logs": ("log", "搜索日志"),
    "list_code_files": ("code", "浏览源码"),
    "read_code_file": ("code", "读取源码"),
    "grep_code": ("code", "检索源码"),
    "task": ("other", "委派子任务"),
}

_PHASE_BY_KIND = {"log": "正在查看日志", "code": "正在阅读源码", "other": "正在执行工具"}

_MAX_VALUE_CELLS = 48


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
    """收集本轮所有 AI 文本消息（按时间顺序）拼成完整报告。"""
    collected: list[str] = []
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", "")
        if msg_type == "human":
            break
        if msg_type == "ai":
            text = content_to_text(getattr(msg, "content", "")).strip()
            if text:
                collected.append(text)
    return "\n\n".join(reversed(collected))


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
        if arg("path_glob"):
            flags.append(_clip(arg("path_glob"), 16))
        return " ".join(flags)

    parts: list[tuple[str, str]] = []
    if name == "log_overview":
        if arg("path"):
            parts.append((_path_name(arg("path")), "muted"))
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
    else:
        for key, value in list(args.items())[:2]:
            if isinstance(value, (str, int, float)) and str(value):
                parts.append((_clip(f"{key}={value}", 32), "muted"))
    return parts


def summarize_tool_output(name: str, output: Any) -> tuple[str, bool]:
    """把工具的原始输出压缩成一句结果摘要，返回 (摘要, 是否出错)。"""
    raw = getattr(output, "content", output)
    text = content_to_text(raw).strip()
    if getattr(output, "status", "success") == "error":
        return (text.splitlines()[0] if text else "执行失败"), True
    if text.startswith("[错误]"):
        return text.removeprefix("[错误]").strip().splitlines()[0], True
    if text.startswith("[提示]"):
        hints = {
            "search_logs": "无命中",
            "grep_code": "无命中",
            "read_log_chunk": "已到文件末尾",
            "list_code_files": "目录为空",
        }
        return hints.get(name, text.removeprefix("[提示]").strip()), False

    lines = text.splitlines()
    if name == "log_overview":
        parts = []
        total = re.search(r"共 ([\d,]+) 行", text)
        if total:
            parts.append(f"{total.group(1)} 行")
        errors = re.search(r"\b(?:FATAL|ERROR) ([\d,]+)", text)
        parts.append(f"ERROR {errors.group(1)}" if errors else "无 ERROR")
        return f" {glyphs.sep} ".join(parts), False
    if name == "read_log_chunk":
        match = re.search(r"第 (\d+)-(\d+) 行", lines[0] if lines else "")
        if match:
            return f"{int(match.group(2)) - int(match.group(1)) + 1} 行", False
    elif name == "search_logs":
        hits = sum(1 for line in lines if re.match(r"\d+: ", line))
        more = "+" if "命中超过" in text else ""
        return f"命中 {hits}{more} 行", False
    elif name == "list_code_files":
        files = sum(1 for line in lines if line and not line.startswith("..."))
        total = re.search(r"共 (\d+) 个文件", text)
        return (f"{files}/{total.group(1)} 个文件" if total else f"{files} 个文件"), False
    elif name == "read_code_file":
        match = re.search(r"第 (\d+)-(\d+) 行，共 (\d+) 行", lines[0] if lines else "")
        if match:
            start, end, total = (int(g) for g in match.groups())
            if start == 1 and end == total:
                return f"{total} 行", False
            return f"L{start}-{end} / {total} 行", False
    elif name == "grep_code":
        hit_files = set()
        hits = 0
        for line in lines:
            match = re.match(r"(.+?):(\d+): ", line)
            if match:
                hits += 1
                hit_files.add(match.group(1))
        more = "+" if "命中超过" in text else ""
        return f"命中 {hits}{more} 处 {glyphs.sep} {len(hit_files)} 个文件", False
    return "", False


@dataclass
class ToolRun:
    call_id: str
    name: str
    args: dict[str, Any]
    handle: Any
    started: float = field(default_factory=time.perf_counter)

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

    def __init__(self, run: ToolRun, icon: Text, meta: Text) -> None:
        self.run = run
        self.icon = icon
        self.meta = meta

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # 留 1 列余量：经典 conhost 写满最后一列会触发自动换行
        width = max(options.max_width - 1, 20)
        left = Text("  ")
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


def settled_tool_line(run: ToolRun) -> ToolLine:
    duration = format_duration(time.perf_counter() - run.started)
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
    return ToolLine(run, icon, meta)


# ---------------------------------------------------------------------------
# 任务进度（write_todos）
# ---------------------------------------------------------------------------


def _progress_bar(done: int, total: int, width: int = 16) -> Text:
    filled = round(done / total * width) if total else 0
    bar = Text(glyphs.bar_full * filled, style="ok")
    bar.append(glyphs.bar_empty * (width - filled), style="muted")
    return bar


class TodoTracker:
    """跟踪计划变化：新计划画完整面板，之后的状态推进只打印一行增量，避免刷屏。"""

    def __init__(self) -> None:
        self.todos: list[dict[str, Any]] = []

    @property
    def done(self) -> int:
        return sum(1 for t in self.todos if t.get("status") == "completed")

    @property
    def current(self) -> str:
        for todo in self.todos:
            if todo.get("status") == "in_progress":
                return str(todo.get("content", ""))
        return ""

    def update(self, todos: list[dict[str, Any]]) -> RenderableType | None:
        previous = {str(t.get("content", "")): t.get("status") for t in self.todos}
        self.todos = [t for t in todos if isinstance(t, dict)]
        if not self.todos:
            return None

        contents = [str(t.get("content", "")) for t in self.todos]
        if set(contents) != set(previous):
            return self._panel()

        changes = Text("  ")
        for todo in self.todos:
            content = str(todo.get("content", ""))
            status = todo.get("status")
            if previous.get(content) == status:
                continue
            if status == "completed":
                changes.append(f"{glyphs.todo_done} ", style="ok")
                changes.append(_clip(content, 36), style="muted strike")
                changes.append("   ")
            elif status == "in_progress":
                changes.append(f"{glyphs.todo_active} ", style="warn")
                changes.append(_clip(content, 36), style="bold")
                changes.append("   ")
        if not changes.plain.strip():
            return None
        changes.append_text(_progress_bar(self.done, len(self.todos), width=10))
        changes.append(f" {self.done}/{len(self.todos)}", style="muted")
        changes.no_wrap = True
        changes.overflow = "ellipsis"
        return changes

    def _panel(self) -> Panel:
        total = len(self.todos)
        header = _progress_bar(self.done, total)
        header.append(f"  {self.done}/{total} 已完成", style="bold")

        rows: list[Text] = [header, Text("")]
        for todo in self.todos:
            status = todo.get("status", "pending")
            content = str(todo.get("content", ""))
            row = Text()
            if status == "completed":
                row.append(f"{glyphs.todo_done}  ", style="ok")
                row.append(content, style="muted strike")
            elif status == "in_progress":
                row.append(f"{glyphs.todo_active}  ", style="warn")
                row.append(content, style="bold warn")
            else:
                row.append(f"{glyphs.todo_pending}  ", style="muted")
                row.append(content)
            rows.append(row)

        return Panel(
            Group(*rows),
            title=Text("排查计划", style="bold"),
            title_align="left",
            border_style="accent",
            box=glyphs.box,
            padding=(0, 2),
        )


# ---------------------------------------------------------------------------
# 流式正文
# ---------------------------------------------------------------------------


def split_complete_blocks(text: str) -> tuple[str, str]:
    """把已完整的 Markdown 块与还在生成中的尾部拆开（Claude Code 式���量固化）。

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


@dataclass
class ToolRecord:
    name: str
    args: dict[str, Any]
    summary: str
    failed: bool
    seconds: float


@dataclass
class TurnResult:
    """一轮执行的结果，供导出报告与会话累计统计使用。"""

    report: str = ""
    elapsed: float = 0.0
    usage: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "total": 0})
    tools: list[ToolRecord] = field(default_factory=list)
    interrupted: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.interrupted and not self.error


class StreamRenderer:
    """一轮 agent 执行的完整渲染。

    - 底部常驻 Live：流式正文预览 + 运行中的工具 + 当前任务 + 动态状态栏；
      后台刷新线程让计时与动效在等待模型/工具时也持续跳动。
    - 上方永久区：完整 Markdown 块、已完成的工具行（verbose）、计划变化。
    """

    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose
        self.start = time.perf_counter()
        self.spinner = Spinner(glyphs.spinner, style="accent")
        self.tail = MarkdownTail()
        self.todos = TodoTracker()
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

    # ---- Live 视图 ---------------------------------------------------------

    def _phase(self) -> str:
        active = [run for run in self.running if not run.completed]
        if active:
            if len(active) > 1:
                return f"并行执行 {len(active)} 个工具"
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
        meta = Text(sep + format_duration(now - self.start), style="muted")
        approx = self.usage["total"] + self.pending_chunks
        if approx:
            prefix = "~" if self.pending_chunks else ""
            meta.append(f"{sep}{prefix}{approx:,} tokens")
        if self.tool_count:
            meta.append(f"{sep}工具 {self.tool_count}")
        meta.append(f"{sep}Ctrl+C 中断")
        line.append_text(meta)
        line.no_wrap = True
        line.overflow = "ellipsis"
        return line

    def __rich__(self) -> RenderableType:
        now = time.perf_counter()
        parts: list[RenderableType] = []

        running = list(self.running)
        todo_now = self.todos.current

        if self.tail.text.strip():
            reserved = 4 + len(running) + (1 if todo_now else 0)
            self.tail.max_lines = max(1, min(12, console.size.height - reserved))
            parts.extend([self.tail, Text("")])

        for run in running:
            if run.completed:
                parts.append(settled_tool_line(run))
                continue
            icon = self.spinner.render(now)
            icon = icon.copy() if isinstance(icon, Text) else Text(str(icon))
            icon.stylize(f"tool.{run.kind}")
            meta = Text(format_duration(now - run.started), style="muted")
            parts.append(ToolLine(run, icon, meta))

        if todo_now:
            todo = Text(f"  {glyphs.todo_active} ", style="warn")
            todo.append(todo_now, style="bold")
            todo.append(f"  {self.todos.done}/{len(self.todos.todos)}", style="muted")
            todo.no_wrap = True
            todo.overflow = "ellipsis"
            parts.append(todo)

        if running or todo_now:
            parts.append(Text(""))
        parts.append(self._status_line(now))
        return Group(*parts)

    # ---- 永久输出 -----------------------------------------------------------

    def _flush_answer(self, text: str) -> None:
        if not text.strip():
            return
        if not self.printed_answer_rule:
            console.print()
            console.print(Rule(Text("分析结果", style="accent.strong"), style="muted", characters=glyphs.rule))
            self.printed_answer_rule = True
        console.print()
        console.print(Markdown(text))
        self.answer_parts.append(text)
        self.rendered_any = True

    def _record(self, run: ToolRun) -> None:
        error = getattr(run.handle, "error", None)
        if error:
            summary, failed = str(error).splitlines()[0], True
        else:
            summary, failed = summarize_tool_output(run.name, getattr(run.handle, "output", None))
        self.records.append(
            ToolRecord(run.name, dict(run.args), summary, failed, round(time.perf_counter() - run.started, 3))
        )

    def _settle_tools(self) -> None:
        still_running = []
        for run in self.running:
            if run.completed:
                self._record(run)
                if self.verbose:
                    console.print(settled_tool_line(run))
            else:
                still_running.append(run)
        self.running = still_running

    # ---- 事件处理 -----------------------------------------------------------

    def _on_message(self, message_stream: Any) -> None:
        self._settle_tools()
        buffer = ""
        streamed = False
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
        self._flush_answer(final_text.strip())

    def _on_tool(self, tool_stream: Any) -> None:
        name = getattr(tool_stream, "tool_name", "") or ""
        args = getattr(tool_stream, "input", None) or {}
        if not isinstance(args, dict):
            args = {"input": args}
        call_id = getattr(tool_stream, "tool_call_id", None) or f"{name}:{args}"
        if call_id in self.seen_calls:
            return
        self.seen_calls.add(call_id)

        if name == "write_todos":
            update = self.todos.update(args.get("todos") or [])
            if update is not None and self.verbose:
                console.print(update)
            self.rendered_any = True
            return

        self.tool_count += 1
        self.running.append(ToolRun(call_id=call_id, name=name, args=args, handle=tool_stream))
        self.rendered_any = True

    # ---- 入口 ---------------------------------------------------------------

    def run(self, agent: Any, payload: dict[str, Any], config: dict[str, Any] | None = None) -> TurnResult:
        """执行一轮并渲染，返回本轮结果（报告正文、耗时、用量、工具记录、是否中断）。"""
        from langgraph.errors import GraphRecursionError

        final_state: Any = None
        error = ""
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
                self._record(run)
        elapsed = time.perf_counter() - self.start
        print_stats(elapsed, self.usage, self.tool_count, self.interrupted or bool(error))
        return TurnResult(
            report="\n\n".join(self.answer_parts),
            elapsed=round(elapsed, 3),
            usage=dict(self.usage),
            tools=list(self.records),
            interrupted=self.interrupted,
            error=error,
        )
