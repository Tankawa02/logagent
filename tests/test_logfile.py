from __future__ import annotations

import gzip
import os
from pathlib import Path

import pytest

from log_agent import logfile
from log_agent.logfile import INDEX_STEP, clip_line, detect_encoding, open_log

TEXT = "2026-06-09 10:00:00 ERROR 订单支付失败\n第二行 中文内容\n"


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (TEXT.encode("utf-8"), "utf-8"),
        (b"\xef\xbb\xbf" + TEXT.encode("utf-8"), "utf-8-sig"),
        (TEXT.encode("gbk"), "gb18030"),
        (TEXT.encode("utf-16"), "utf-16"),
        (b"", "utf-8"),
        (b"caf\xe9 \x81\xff", "latin-1"),
    ],
)
def test_detect_encoding(data: bytes, expected: str) -> None:
    assert detect_encoding(data) == expected


def test_detect_encoding_tolerates_cut_multibyte_char() -> None:
    data = ("中" * 100).encode("utf-8")[:-1]
    assert detect_encoding(data) == "utf-8"


@pytest.mark.parametrize("encoding", ["utf-8", "gbk", "utf-16"])
def test_reads_non_utf8_logs(tmp_path: Path, encoding: str) -> None:
    path = tmp_path / "x.log"
    path.write_bytes(TEXT.encode(encoding))
    lines = list(open_log(path).iter_lines(1))
    assert lines == [(1, "2026-06-09 10:00:00 ERROR 订单支付失败"), (2, "第二行 中文内容")]


def test_crlf_is_stripped(tmp_path: Path) -> None:
    path = tmp_path / "win.log"
    path.write_bytes(b"a\r\nb\r\n")
    assert [t for _, t in open_log(path).iter_lines()] == ["a", "b"]


def test_gzip_is_transparent(tmp_path: Path) -> None:
    path = tmp_path / "x.log.gz"
    with gzip.open(path, "wb") as f:
        f.write(TEXT.encode("utf-8"))
    log = open_log(path)
    assert log.gz
    assert [t for _, t in log.iter_lines(2)] == ["第二行 中文内容"]


def test_sparse_index_matches_linear_scan(tmp_path: Path) -> None:
    total = INDEX_STEP * 3 + 17
    path = tmp_path / "big.log"
    path.write_text("".join(f"line {i}\n" for i in range(1, total + 1)), encoding="utf-8")
    log = open_log(path)
    assert log.count_lines() == total
    assert len(log.checkpoints) == 4
    for start in (1, INDEX_STEP, INDEX_STEP + 1, INDEX_STEP * 2 + 5, total):
        assert next(log.iter_lines(start)) == (start, f"line {start}")


def test_cache_invalidates_when_file_changes(tmp_path: Path) -> None:
    path = tmp_path / "x.log"
    path.write_text("one\n", encoding="utf-8")
    first = open_log(path)
    assert open_log(path) is first
    path.write_text("one\ntwo\n", encoding="utf-8")
    os.utime(path, ns=(first.mtime_ns + 10**9, first.mtime_ns + 10**9))
    second = open_log(path)
    assert second is not first
    assert second.count_lines() == 2


def test_forced_encoding(tmp_path: Path) -> None:
    path = tmp_path / "x.log"
    path.write_bytes("中文".encode("gbk"))
    logfile.set_forced_encoding("gbk")
    assert open_log(path).encoding == "gbk"
    with pytest.raises(LookupError):
        logfile.set_forced_encoding("no-such-codec")


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_log(tmp_path / "missing.log")


def test_clip_line() -> None:
    assert clip_line("abc", 5) == "abc"
    clipped = clip_line("x" * 20, 5)
    assert clipped.startswith("xxxxx …") and "20" in clipped
