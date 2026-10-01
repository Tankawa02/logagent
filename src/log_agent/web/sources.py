"""证据跳转：按 `来源:行号` 读出原文和上下文，供报告/证据左右对照。

来源解析复用 evidence.SourceResolver：只认会话登记过的日志文件和源码目录里的文件，
浏览器传来的任意路径（`../../etc/passwd`、目录外的绝对路径）一律解析不到。
读出的内容按请求方的脱敏策略处理，分享链接始终脱敏。
"""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from ..evidence import SourceResolver
from ..logfile import clip_line, open_log, read_text_file
from ..redact import redact_code_lines, redact_log

MAX_CONTEXT = 200
MAX_SPAN = 400
DISPLAY_CHARS = 2000
_MAX_CODE_BYTES = 5 * 1024 * 1024


class SourceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def read_context(
    log_paths: Sequence[str],
    code_dirs: Sequence[str],
    source: str,
    line_start: int,
    line_end: int | None = None,
    *,
    before: int = 20,
    after: int = 20,
    redact: bool = True,
    encoding: str | None = None,
) -> dict[str, Any]:
    if line_start < 1:
        raise SourceError(400, "行号必须从 1 开始")
    line_end = max(line_start, line_end or line_start)
    if line_end - line_start + 1 > MAX_SPAN:
        line_end = line_start + MAX_SPAN - 1
    before, after = (max(0, min(int(n), MAX_CONTEXT)) for n in (before, after))
    target = SourceResolver(log_paths, code_dirs).resolve(source)
    if target is None:
        raise SourceError(404, f"来源不属于本会话的日志或源码：{source}")
    kind, path = target
    lo, hi = max(1, line_start - before), line_end + after
    try:
        if kind == "log":
            lines, total, more = _log_lines(path, lo, hi, redact, encoding)
        else:
            lines, total, more = _code_lines(path, lo, hi, redact)
    except FileNotFoundError as exc:
        raise SourceError(404, f"文件已不存在：{path.name}") from exc
    except (OSError, EOFError) as exc:
        raise SourceError(422, f"读取失败：{type(exc).__name__}: {exc}") from exc
    return {
        "kind": kind,
        "source": source,
        "name": path.name,
        "path": str(path),
        "line_start": line_start,
        "line_end": line_end,
        "first": min(lines) if lines else lo,
        "last": max(lines) if lines else lo - 1,
        "total_lines": total,
        "has_more": more,
        "lines": [{"n": n, "text": text} for n, text in sorted(lines.items())],
    }


def _redact_block(raw: dict[int, str], enabled: bool) -> dict[int, str]:
    if not raw or not enabled:
        return raw
    numbers = sorted(raw)
    joined = redact_log("\n".join(raw[n] for n in numbers), enabled=True).split("\n")
    if len(joined) == len(numbers):  # 跨行规则（私钥块）会合并行，此时退回逐行脱敏
        return dict(zip(numbers, joined, strict=True))
    return {n: redact_log(raw[n], enabled=True) for n in numbers}


def _log_lines(path: Path, lo: int, hi: int, redact: bool, encoding: str | None):
    log = open_log(path, encoding)
    raw: dict[int, str] = {}
    more = False
    with closing(log.iter_lines(lo)) as it:
        for lineno, text in it:
            if lineno > hi:
                more = True
                break
            raw[lineno] = text
    lines = {n: clip_line(t, DISPLAY_CHARS) for n, t in _redact_block(raw, redact).items()}
    return lines, log.total_lines, more


def _code_lines(path: Path, lo: int, hi: int, redact: bool):
    if path.stat().st_size > _MAX_CODE_BYTES:
        raise OSError("文件过大，不在网页中展示")
    # 与 read_code_file 一致：整份文件脱敏后再取区间，私钥块跨越展示边界时也能遮住
    all_lines = redact_code_lines(read_text_file(path).splitlines(), enabled=redact)
    lines = {n: clip_line(all_lines[n - 1], DISPLAY_CHARS) for n in range(lo, min(hi, len(all_lines)) + 1)}
    return lines, len(all_lines), hi < len(all_lines)
