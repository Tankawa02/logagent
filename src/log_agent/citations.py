"""把报告里的 `文件:行号` 引用变成终端里可点击的链接（OSC 8）。

只改终端显示：导出的 Markdown / JSON 保持模型原文，不写入本机绝对路径。
链接格式由 LOG_AGENT_LINKS 决定：
    auto（默认）  VS Code / Cursor 终端里用 vscode:// 跳到对应行，其它终端用 file:// 打开文件
    vscode        总是用 vscode://file/<路径>:<行号>
    file          总是用 file://（大多数终端不支持行号，只能打开文件）
    off           不生成链接
输出不是终端（管道、重定向）时 auto 等同于 off。
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from markdown_it import MarkdownIt
from markdown_it.common.normalize_url import validateLink
from rich.markdown import Markdown

from .term import console

# 反引号里的 `路径:行号` 或 `路径:起-止`；前面紧跟 "[" 的已经是链接文字，跳过
_CITATION = re.compile(r"(?<!\[)`([^`\s:]+):(\d+)(?:\s*[-–~]\s*\d+)?`")
_FENCE = re.compile(r"^\s*(```|~~~)")


def link_mode() -> str:
    mode = (os.environ.get("LOG_AGENT_LINKS") or "auto").strip().lower()
    if mode not in {"auto", "vscode", "file", "off"}:
        mode = "auto"
    if mode == "auto":
        if not console.is_terminal:
            return "off"
        return "vscode" if os.environ.get("TERM_PROGRAM", "").lower() == "vscode" else "file"
    return mode


def _url(path: Path, line: int, mode: str) -> str:
    if mode == "vscode":
        return f"vscode://file/{quote(path.as_posix(), safe='/:')}:{line}"
    return path.as_uri()


def _validate_link(url: str) -> bool:
    return url.startswith(("file:", "vscode:")) or validateLink(url)


class LinkedMarkdown(Markdown):
    """markdown-it 默认拒绝 file: 链接（会原样输出 [文字](url)），这里额外放行 file: 与 vscode:。

    解析方式与 rich.markdown.Markdown 完全一致，只替换了链接校验。
    """

    def __init__(self, markup: str, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        parser = MarkdownIt().enable("strikethrough").enable("table")
        parser.validateLink = _validate_link
        self.markup = markup
        self.parsed = parser.parse(markup)


class CitationLinker:
    """根据本次的日志与源码目录，把引用解析成真实文件并生成链接。"""

    def __init__(self, log_paths: Sequence[str], code_dirs: Sequence[str], mode: str | None = None) -> None:
        self.mode = mode or link_mode()
        self.code_dirs = [Path(p) for p in code_dirs]
        self._logs: dict[str, Path | None] = {}
        for raw in log_paths:
            path = Path(raw)
            # 同名日志来自不同目录时无法判断指哪一份，不生成链接
            self._logs[path.name] = None if path.name in self._logs else path
        self._cache: dict[str, Path | None] = {}

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def resolve(self, ref: str) -> Path | None:
        if ref not in self._cache:
            self._cache[ref] = self._resolve(ref)
        return self._cache[ref]

    def _resolve(self, ref: str) -> Path | None:
        candidate = Path(ref)
        if candidate.is_absolute():
            return candidate if candidate.is_file() else None
        if ref in self._logs:
            return self._logs[ref]
        parts = Path(ref.replace("\\", "/")).parts
        for root in self.code_dirs:
            target = root.joinpath(*parts)
            if target.is_file():
                return target.resolve()
            # 模型有时会带上仓库目录名本身，例如 `payment-svc/app/order.py`
            if len(parts) > 1 and parts[0] == root.name:
                target = root.joinpath(*parts[1:])
                if target.is_file():
                    return target.resolve()
        return None

    def apply(self, markdown: str) -> str:
        """给 Markdown 里代码块之外的引用加上链接，无法解析的保持原样。"""
        if not self.enabled or "`" not in markdown:
            return markdown
        lines = markdown.split("\n")
        in_fence = False
        for i, line in enumerate(lines):
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            if not in_fence and "`" in line:
                lines[i] = _CITATION.sub(self._link, line)
        return "\n".join(lines)

    def _link(self, match: re.Match[str]) -> str:
        path = self.resolve(match.group(1))
        if path is None:
            return match.group(0)
        return f"[{match.group(0)}]({_url(path, int(match.group(2)), self.mode)})"
