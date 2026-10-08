"""报告结果模型、正文解析、Markdown 展示与证据提示。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown
from rich.segment import Segment
from rich.text import Text

from .render_common import content_to_text
from .report import extract_analysis, visible_report
from .term import console, glyphs

# 短文本先作为旁白等待消息结束；长文本、标题或一句话结论视为报告。
_NARRATION_MAX_CHARS = 300

_HEADING = re.compile(r"^\s{0,3}#{1,6}\s", re.MULTILINE)

_MD_NOISE = re.compile(r"^[\s>#*\-+]+|[*`_]+")

# 容忍加粗、引用块等 Markdown 包装。
_SUMMARY_LINE = re.compile(
    r"^\s*(?:>\s*)?\**\s*一句话结论\s*\**\s*[:：]\s*\**\s*(?P<text>.+?)\s*\**\s*"
    r"(?:[（(]\s*可信度\s*[:：]?\s*(?P<level>高|中|低)\s*[)）])?\s*\**\s*$"
)

_CONFIDENCE_STYLE = {"高": "ok", "中": "warn", "低": "err"}


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
        text = visible_report(self.text)
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
    # 相对本轮开始的秒数，供 Web trace 画瀑布图；旧记录没有这个字段
    started: float | None = None


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
    # 主代理每次模型调用的时间与用量（见 StreamRenderer._on_message）
    llm_calls: list[dict] = field(default_factory=list)

    analysis: dict | None = field(default=None, init=False)
    structured_status: str = field(default="missing", init=False)
    # 证据回查结果（见 evidence.check_analysis）；没有结构化证据或未提供来源时为 None
    evidence_check: dict | None = field(default=None, init=False)

    def __post_init__(self):
        self.report, self.analysis, self.structured_status = extract_analysis(self.report)
        if self.analysis:
            self.summary = self.analysis["conclusion"]
            self.confidence = {"high": "高", "medium": "中", "low": "低"}[self.analysis["confidence"]]

    @property
    def finding(self) -> bool | None:
        """Only validated explicit assessments can drive automation."""
        if not self.ok or not self.analysis:
            return None
        return {"finding": True, "clear": False, "unknown": None}[self.analysis["assessment"]]

    @property
    def ok(self) -> bool:
        return not self.interrupted and not self.error


def print_evidence_check(check: dict | None, limit: int = 3) -> None:
    """终端里用一两行交代证据回查结果；全部通过时只给一行弱提示。"""
    if not check:
        return
    from .evidence import summary_line

    line = summary_line(check)
    if check["status"] == "verified":
        console.print(Text(f"{glyphs.ok} 证据核对：{line}", style="muted"))
        return
    style = "warn" if check["mismatch"] else "muted"
    console.print(Text(f"{glyphs.notice} 证据核对：{line}", style=style))
    flagged = [it for it in check["items"] if it["status"] in ("mismatch", "unresolved")]
    for item in flagged[:limit]:
        mark = glyphs.fail if item["status"] == "mismatch" else "?"
        ref = f"{item['source']}:{item['line_start']}" + (
            f"-{item['line_end']}" if item["line_end"] != item["line_start"] else ""
        )
        console.print(Text(f"    {mark} 问题 {item['issue']} · {ref}  {item['note']}", style=style))
    if len(flagged) > limit:
        console.print(Text(f"    … 另有 {len(flagged) - limit} 条，详见导出报告", style="muted"))
