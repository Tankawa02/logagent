"""工具调用的参数、结果摘要和单行展示。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from .render_common import _clip, _path_name, content_to_text, format_duration, shorten_path
from .render_report import ToolRecord
from .subtrace import SubCall
from .term import glyphs

# 工具名 -> (类别, 友好中文名)，类别决定图标与颜色。
_TOOL_META: dict[str, tuple[str, str]] = {
    "log_overview": ("log", "日志概览"),
    "compare_windows": ("log", "窗口对比"),
    "read_log_chunk": ("log", "读取日志"),
    "search_logs": ("log", "搜索日志"),
    "trace_request": ("log", "追踪请求"),
    "list_code_files": ("code", "浏览源码"),
    "read_code_file": ("code", "读取源码"),
    "grep_code": ("code", "检索源码"),
    "recent_changes": ("code", "提交记录"),
    "show_commit": ("code", "查看提交"),
    "blame_lines": ("code", "代码追溯"),
    "read_file": ("other", "读取文件"),
    "task": ("other", "委派子任务"),
    "suggest_memory": ("other", "建议记忆"),
}

_TRAIL_MAX_STEPS = 8

_HINT_SUMMARIES = {
    "no_match": "无命中",
    "eof": "已到文件末尾",
    "empty": "内容为空",
    "empty_window": "时间窗口内无日志",
    "no_timestamp": "无时间戳",
}


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
    elif name == "recent_changes":
        span = " → ".join(v for v in (arg("since"), arg("until")) if v)
        if span:
            parts.append((_clip(span, 40), "accent"))
        if arg("path"):
            parts.append((_clip(arg("path"), 32), "accent"))
        if arg("code_dir"):
            parts.append((_path_name(arg("code_dir")), "muted"))
    elif name == "show_commit":
        if arg("commit"):
            parts.append((_clip(arg("commit"), 16), "accent"))
        if arg("path"):
            parts.append((_clip(arg("path"), 32), "accent"))
        if arg("code_dir"):
            parts.append((_path_name(arg("code_dir")), "muted"))
    elif name == "blame_lines":
        if arg("rel_path"):
            parts.append((_clip(arg("rel_path")), "accent"))
        start = num("start_line", 1)
        end = num("end_line", 0)
        parts.append((f"L{start}-{end}" if end > start else f"L{start}", "accent"))
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


def _summarize_meta(name: str, meta: dict[str, Any]) -> str:
    sep = f" {glyphs.sep} "
    if name == "log_overview":
        parts = [f"{meta.get('total_lines', 0):,} 行"]
        if meta.get("window"):
            parts.append(f"窗口内 {meta.get('window_lines', 0):,} 行")
        errors = meta.get("errors", 0)
        parts.append(f"ERROR {errors:,}" if errors else "无 ERROR")
        if meta.get("chains"):
            parts.append(f"异常链 {meta['chains']} 类")
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
    if name == "recent_changes":
        if not meta.get("commits"):
            return "无提交"
        more = "+" if meta.get("truncated") else ""
        return f"{meta['commits']}{more} 个提交{sep}最近 {str(meta.get('newest', ''))[:16]}"
    if name == "show_commit":
        return f"{meta.get('files', 0)} 个文件{sep}+{meta.get('additions', 0)} -{meta.get('deletions', 0)}"
    if name == "blame_lines":
        newest = str(meta.get("newest", ""))[:16]
        text = f"{meta.get('commits', 0)} 个提交" + (f"{sep}最近 {newest}" if newest else "")
        return text + (f"{sep}工作区与 HEAD 不同" if meta.get("drifted") else "")
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


def _sub_run(call: SubCall) -> ToolRun:
    return ToolRun(call_id="", name=call.name, args=call.args, handle=call, started=call.started)
