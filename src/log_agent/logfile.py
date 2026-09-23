"""日志文件读取底座：编码探测、.gz 透明解压、稀疏行索引、超长行截断。

所有日志工具都通过 `open_log()` 拿到一个带缓存的 `LogFile`，避免每次调用都
从头探测编码、从第一行数到目标行。
"""

from __future__ import annotations

import codecs
import gzip
import io
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

# 每隔多少行记录一次字节偏移。1 万行一个点：GB 级日志索引也只有几百个整数。
INDEX_STEP = 10_000

# 单行返回给模型的最大字符数。超长 JSON / SQL 行会被截断，避免一行撑爆上下文。
# 业务日志里路由、降级原因这类关键字段常埋在长 JSON 的后半段，500 太容易截掉。
MAX_LINE_CHARS = 1000
# 搜索命中行本身是证据，给更大的额度，并以命中位置为中心截取。
HIT_LINE_CHARS = 2000

_SNIFF_BYTES = 64 * 1024

# 用户通过 --encoding 或 LOG_AGENT_ENCODING 强制指定时生效
_forced_encoding: str | None = os.environ.get("LOG_AGENT_ENCODING") or None


def set_forced_encoding(encoding: str | None) -> None:
    global _forced_encoding
    if encoding:
        codecs.lookup(encoding)  # 非法编码名立刻报错，而不是等到读文件时
    _forced_encoding = encoding or None
    _CACHE.clear()


def _is_gzip(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(2) == b"\x1f\x8b"
    except OSError:
        return False


def _open_binary(path: Path, gz: bool) -> io.BufferedIOBase:
    if gz:
        return gzip.open(path, "rb")  # type: ignore[return-value]
    return path.open("rb")


def detect_encoding(sample: bytes) -> str:
    """按 BOM → UTF-8 → GB18030 → latin-1 的顺序猜编码。

    GB18030 是 GBK 的超集，中文 Windows 上 Java/.NET 服务默认写出的日志就是它。
    PowerShell 5 的 `>` 重定向会写 UTF-16LE（带 BOM），这里也能识别。
    """
    if sample.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    if not sample:
        return "utf-8"

    # 采样可能在多字节字符中间截断，用增量解码器且 final=False 忽略尾部残缺
    for candidate in ("utf-8", "gb18030"):
        try:
            codecs.getincrementaldecoder(candidate)().decode(sample, final=False)
            return candidate
        except UnicodeDecodeError:
            continue
    return "latin-1"


def clip_line(text: str, limit: int = MAX_LINE_CHARS, focus: int | None = None) -> str:
    """截断超长行。给出 focus（命中位置）时，截取命中点附近的窗口，避免关键字段被截掉。"""
    if len(text) <= limit:
        return text
    if focus is None or focus < limit * 3 // 4:
        return f"{text[:limit]} …(本行共 {len(text)} 字，已截断)"
    start = max(0, focus - limit // 3)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    tail = " …" if end < len(text) else ""
    return f"… {text[start:end]}{tail}(本行共 {len(text)} 字，已截断，显示第 {start + 1}-{end} 字)"


@dataclass
class LogFile:
    path: Path
    gz: bool
    encoding: str
    size: int
    mtime_ns: int
    # checkpoints[k] = 第 k*INDEX_STEP+1 行的字节偏移（未压缩流中的偏移）
    checkpoints: list[int] = field(default_factory=lambda: [0])
    total_lines: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def byte_oriented(self) -> bool:
        # UTF-16 不能按 b"\n" 切行，只能走文本模式线性扫描
        return not self.encoding.startswith("utf-16")

    def _decode(self, raw: bytes) -> str:
        return raw.decode(self.encoding, errors="replace").rstrip("\r\n")

    def iter_lines(self, start_line: int = 1) -> Iterator[tuple[int, str]]:
        """从 start_line 开始逐行产出 (行号, 文本)，顺路补全稀疏索引。"""
        start_line = max(1, start_line)
        if not self.byte_oriented:
            yield from self._iter_text_mode(start_line)
            return

        with self.lock:
            slot = min((start_line - 1) // INDEX_STEP, len(self.checkpoints) - 1)
            offset = self.checkpoints[slot]
        lineno = slot * INDEX_STEP + 1

        with _open_binary(self.path, self.gz) as f:
            f.seek(offset)
            pos = offset
            for raw in f:
                if (lineno - 1) % INDEX_STEP == 0:
                    slot = (lineno - 1) // INDEX_STEP
                    with self.lock:
                        if slot == len(self.checkpoints):
                            self.checkpoints.append(pos)
                pos += len(raw)
                if lineno >= start_line:
                    yield lineno, self._decode(raw)
                lineno += 1
            with self.lock:
                self.total_lines = lineno - 1

    def _iter_text_mode(self, start_line: int) -> Iterator[tuple[int, str]]:
        with _open_binary(self.path, self.gz) as raw:
            text = io.TextIOWrapper(raw, encoding=self.encoding, errors="replace", newline=None)
            lineno = 0
            for lineno, line in enumerate(text, start=1):
                if lineno >= start_line:
                    yield lineno, line.rstrip("\r\n")
            self.total_lines = lineno

    def count_lines(self) -> int:
        if self.total_lines is None:
            for _ in self.iter_lines(1):
                pass
        return self.total_lines or 0


_CACHE: dict[str, LogFile] = {}
_CACHE_LOCK = threading.Lock()


def open_log(path: str | os.PathLike[str]) -> LogFile:
    """拿到日志文件句柄（带缓存）。文件被改写（大小或修改时间变化）时自动失效重建。

    Raises:
        FileNotFoundError: 文件不存在或不是普通文件。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(str(path))
    stat = p.stat()
    key = str(p.resolve())

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and cached.size == stat.st_size and cached.mtime_ns == stat.st_mtime_ns:
            return cached

    gz = _is_gzip(p)
    if _forced_encoding:
        encoding = _forced_encoding
    else:
        with _open_binary(p, gz) as f:
            encoding = detect_encoding(f.read(_SNIFF_BYTES))

    log = LogFile(path=p, gz=gz, encoding=encoding, size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    with _CACHE_LOCK:
        _CACHE[key] = log
    return log


def read_text_file(path: Path) -> str:
    """读取源码等小文本文件：同样走编码探测，避免 GBK 源码注释变乱码。

    --encoding 只针对日志，源码和日志常常不是同一种编码，所以这里始终自动探测。
    """
    data = path.read_bytes()
    encoding = detect_encoding(data[:_SNIFF_BYTES])
    return data.decode(encoding, errors="replace")
