"""终端通用文本格式、路径、面板与统计行。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .term import console, glyphs

_MAX_VALUE_CELLS = 48


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


def display_path(path: str, max_width: int) -> str:
    """面板里展示的路径：家目录缩成 ~；仍然太长时从中间省略目录，始终保留完整的文件名。

    直接交给 Rich 折行会把文件名拆成 `app.l` / `og` 两行，恰好把最关键的部分弄得最难认。
    """
    home = str(Path.home())
    if home not in ("", "/") and (path == home or path.startswith(home + os.sep)):
        path = "~" + path[len(home) :]
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


def info_panel(
    rows: list[tuple[str, Text | str]], title: str, subtitle: str = "", footer: list[Text] | None = None
) -> Panel:
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
