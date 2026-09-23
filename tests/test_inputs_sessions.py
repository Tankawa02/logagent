from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from log_agent.inputs import LogInputError, resolve_log_inputs
from log_agent.sessions import SessionStore, describe_source_change


def test_expands_globs_and_dedupes(tmp_path: Path) -> None:
    for name in ("b.log", "a.log", "c.txt"):
        (tmp_path / name).write_text("x\n", encoding="utf-8")
    result = resolve_log_inputs([str(tmp_path / "*.log"), str(tmp_path / "a.log")])
    assert [Path(p).name for p in result] == ["a.log", "b.log"]


def test_missing_and_empty_glob(tmp_path: Path) -> None:
    with pytest.raises(LogInputError, match="不存在"):
        resolve_log_inputs([str(tmp_path / "missing.log")])
    with pytest.raises(LogInputError, match="通配符"):
        resolve_log_inputs([str(tmp_path / "*.nothing")])
    with pytest.raises(LogInputError, match="不是文件"):
        resolve_log_inputs([str(tmp_path)])


def test_stdin_is_saved_to_file() -> None:
    data = b"ERROR from pipe\n"
    paths = resolve_log_inputs(["-"], stdin=io.BytesIO(data))
    assert len(paths) == 1
    assert Path(paths[0]).read_bytes() == data


def test_empty_stdin_is_rejected() -> None:
    with pytest.raises(LogInputError, match="为空"):
        resolve_log_inputs(["-"], stdin=io.BytesIO(b""))


def test_session_store_roundtrip(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "s.db")
    store = SessionStore(conn)
    store.touch("s1", ["/a.log"], [], "openai:gpt-4.1")
    store.record_turn("s1", "为什么 500？", 120)
    store.record_turn("s1", "第二个问题", 80)
    info = store.get("s1")
    assert info is not None
    assert (info.turns, info.total_tokens, info.title) == (2, 200, "为什么 500？")
    assert [s.name for s in store.list()] == ["s1"]
    assert store.delete("s1") is True
    assert store.get("s1") is None
    assert store.delete("s1") is False


def test_describe_source_change(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "s.db")
    store = SessionStore(conn)
    store.touch("s1", ["/old.log"], ["/repo"], "m")
    old = store.get("s1")
    assert old is not None
    assert describe_source_change(old, ["/old.log"], ["/repo"]) == ""
    note = describe_source_change(old, ["/new.log"], [])
    assert "/new.log" in note and "未提供源码" in note
