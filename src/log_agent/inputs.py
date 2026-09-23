"""把命令行里的 `-l` 参数解析成真实存在的日志文件列表。

- 通配符由程序自己展开：Windows 的 cmd / PowerShell 不会像 bash 那样替你展开 `app*.log`。
- `-` 表示从标准输入读取，内容先落盘到 ~/.log-agent/stdin/，工具才能按行号跳读、重复搜索。
"""

from __future__ import annotations

import glob
import shutil
import sys
from datetime import datetime
from pathlib import Path

STDIN_MARK = "-"


class LogInputError(ValueError):
    pass


def data_dir() -> Path:
    path = Path.home() / ".log-agent"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _has_glob(value: str) -> bool:
    return any(ch in value for ch in "*?[")


def save_stdin(stream=None, directory: Path | None = None) -> Path:
    """把标准输入完整写入文件并返回路径。按字节拷贝，不做任何编码转换。"""
    source = stream if stream is not None else sys.stdin.buffer
    target_dir = directory or (data_dir() / "stdin")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"stdin-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.log"
    with target.open("wb") as out:
        shutil.copyfileobj(source, out, length=1024 * 1024)
    return target


def resolve_log_inputs(values: list[str], stdin=None) -> list[str]:
    """展开通配符、处理 stdin、去重，返回绝对路径列表（保持用户给出的顺序）。

    Raises:
        LogInputError: 文件不存在、通配符无匹配、stdin 为空等情况。
    """
    if not values:
        raise LogInputError("至少需要一个 -l 日志文件")

    resolved: list[str] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path.expanduser().resolve())
        if key not in seen:
            seen.add(key)
            resolved.append(key)

    for raw in values:
        if raw == STDIN_MARK:
            source = stdin if stdin is not None else sys.stdin
            if source is sys.stdin and sys.stdin.isatty():
                raise LogInputError("-l - 需要通过管道传入日志，例如：kubectl logs pod | log-agent analyze -l -")
            buffer = getattr(source, "buffer", source)
            saved = save_stdin(buffer)
            if saved.stat().st_size == 0:
                saved.unlink(missing_ok=True)
                raise LogInputError("标准输入为空，没有读到任何日志内容")
            add(saved)
            continue

        expanded = str(Path(raw).expanduser())
        if _has_glob(raw):
            matches = sorted(p for p in glob.glob(expanded, recursive=True) if Path(p).is_file())
            if not matches:
                raise LogInputError(f"通配符没有匹配到任何文件: {raw}")
            for match in matches:
                add(Path(match))
            continue

        path = Path(expanded)
        if not path.exists():
            raise LogInputError(f"日志文件不存在: {raw}")
        if not path.is_file():
            raise LogInputError(f"不是文件: {raw}")
        add(path)

    return resolved
