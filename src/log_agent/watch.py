"""watch 模式的底层：跟随日志新增内容（类似 tail -F），把一波新错误攒成一批再交给 agent。"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .logfile import clip_line, open_log
from .redact import redact_log
from .tools import _LEVEL_ALIAS, _LEVEL_RE

POLL_INTERVAL = 1.0
_MAX_READ = 8 * 1024 * 1024
_MAX_SAMPLES = 5


class FollowError(ValueError):
    pass


def _count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            count += chunk.count(b"\n")
    return count


class LogFollower:
    """从启动时的文件末尾开始，逐次读出新写入的完整行，行号与 read_log_chunk 一致。

    文件被截断或轮转（inode 变化、变小）时从头重新跟随；文件暂时不存在时静默等待它重新出现。
    """

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        meta = open_log(path)
        if meta.gz:
            raise FollowError(f"gzip 压缩的日志不会再增长，无法跟随：{path}")
        if meta.encoding.startswith("utf-16"):
            raise FollowError(f"暂不支持跟随 UTF-16 编码的日志：{path}")
        self.encoding = meta.encoding
        stat = self.path.stat()
        self.inode = stat.st_ino
        self.offset = stat.st_size
        self.lineno = _count_lines(self.path)
        self.rotations = 0
        self._partial = b""

    def _restart(self, inode: int) -> None:
        self.inode = inode
        self.offset = 0
        self.lineno = 0
        self._partial = b""
        self.rotations += 1

    def poll(self) -> list[tuple[int, str]]:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return []
        if stat.st_ino != self.inode or stat.st_size < self.offset:
            self._restart(stat.st_ino)
        if stat.st_size == self.offset:
            return []
        with self.path.open("rb") as f:
            f.seek(self.offset)
            data = f.read(min(stat.st_size - self.offset, _MAX_READ))
        self.offset += len(data)
        parts = (self._partial + data).split(b"\n")
        # 最后一段还没写完换行，留到下次和后续内容拼起来
        self._partial = parts.pop()
        lines = []
        for raw in parts:
            self.lineno += 1
            lines.append((self.lineno, raw.decode(self.encoding, errors="replace").rstrip("\r")))
        return lines


def build_matcher(pattern: str | None) -> Callable[[str], bool]:
    """默认匹配 ERROR / FATAL 级别的行；传了正则就按正则。

    Raises:
        FollowError: 正则写错。
    """
    if pattern:
        try:
            compiled = re.compile(pattern)
        except re.error as exc:
            raise FollowError(f"--pattern 不是合法的正则：{exc}") from exc
        return lambda line: compiled.search(line) is not None

    def is_error(line: str) -> bool:
        match = _LEVEL_RE.search(line[:200])
        return bool(match) and _LEVEL_ALIAS.get(match.group(1), match.group(1)) in ("ERROR", "FATAL")

    return is_error


@dataclass
class _FileHits:
    first: int
    last: int
    count: int = 0


@dataclass
class TriggerBatch:
    """把连续出现的一波触发行攒到一起：安静 debounce 秒，或从第一条起已过 max_wait 秒，就算攒好了。"""

    debounce: float
    max_wait: float
    files: dict[str, _FileHits] = field(default_factory=dict)
    samples: list[tuple[str, int, str]] = field(default_factory=list)
    first_at: float = 0.0
    last_at: float = 0.0

    @property
    def count(self) -> int:
        return sum(hits.count for hits in self.files.values())

    def add(self, path: str, lineno: int, line: str, now: float) -> None:
        if not self.files:
            self.first_at = now
        self.last_at = now
        hits = self.files.setdefault(path, _FileHits(first=lineno, last=lineno))
        hits.last = lineno
        hits.count += 1
        text = line.strip()
        if len(self.samples) < _MAX_SAMPLES and all(text != s[2] for s in self.samples):
            self.samples.append((path, lineno, text))

    def ready(self, now: float) -> bool:
        if not self.files:
            return False
        return now - self.last_at >= self.debounce or now - self.first_at >= self.max_wait

    def drain(self) -> TriggerBatch:
        snapshot = TriggerBatch(self.debounce, self.max_wait, self.files, self.samples, self.first_at, self.last_at)
        self.files, self.samples = {}, []
        return snapshot


def batch_question(batch: TriggerBatch, question: str, matched_by_pattern: bool) -> str:
    what = "匹配监控规则" if matched_by_pattern else "ERROR / FATAL 级别"
    lines = [f"监控期间日志里新出现了 {batch.count} 条{what}的行："]
    for path, hits in batch.files.items():
        span = f"L{hits.first}" if hits.first == hits.last else f"L{hits.first}-L{hits.last}"
        lines.append(f"  - {os.path.basename(path)}：{span}，共 {hits.count} 条")
    lines.append("示例（已脱敏截断）：")
    for path, lineno, text in batch.samples:
        lines.append(f"  {os.path.basename(path)}:{lineno}  {redact_log(clip_line(text, 300))}")
    lines.append("")
    lines.append(
        "请围绕这批新出现的行排查：用 read_log_chunk 读它们前后的上下文，必要时用 trace_request 追相关请求；"
        "更早的日志只在需要对比时再看。"
    )
    lines.append(question)
    return "\n".join(lines)
