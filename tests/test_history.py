from __future__ import annotations

import json
import sqlite3

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.chat_state import parse_turn_selection
from log_agent.sessions import SessionStore

from .test_report import report_text
from .test_session_workflow import _patch, run_chat


def test_history_round_selection_and_resume(tmp_path, sample_log, monkeypatch):
    seen = _patch(monkeypatch, [AIMessage(content=report_text(body="早期证据 unique-answer")),
                                AIMessage(content="最后回答")])
    first = run_chat(tmp_path, "第一问\n第二问\nexit\n", "-l", str(sample_log), "--since", "10:00")
    assert first.exit_code == 0, first.output
    selected, latest = tmp_path / "old report.json", tmp_path / "latest.json"
    resumed = run_chat(tmp_path, f'/history unique-answer\n/show 1\n/show 999\n/show x\n'
                       f'/save --turn 999 {tmp_path / "missing.json"}\n'
                       f'/save-ticket --turn 1 "{selected}"\n/save {latest}\nexit\n')
    assert resumed.exit_code == 0, resumed.output
    assert "第一问" in resumed.output and "早期证据" in resumed.output
    assert "该轮次不存在" in resumed.output and "正整数" in resumed.output
    old = json.loads(selected.read_text(encoding="utf-8"))
    assert old["question"] == "第一问" and old["settings"]["since"] == "10:00"
    assert old["analysis"] and old["view"] == "ticket"
    assert json.loads(latest.read_text(encoding="utf-8"))["question"] == "第二问"
    assert not (tmp_path / "missing.json").exists()
    assert len(seen) == 2  # History commands do not invoke the model.
    result = CliRunner().invoke(cli.app, ["sessions", "list", "--db", str(tmp_path / "sessions.db"),
                                         "--search", "UNIQUE-ANSWER"])
    assert result.exit_code == 0 and "case" in result.output


def test_migration_numbering_and_session_isolation():
    with sqlite3.connect(":memory:") as conn:
        store = SessionStore(conn)
        store.touch("old", [], [], "m")
        conn.execute("DROP TABLE log_agent_turns")
        conn.execute("CREATE TABLE log_agent_turns(id INTEGER PRIMARY KEY, name TEXT, payload TEXT)")
        conn.execute("UPDATE log_agent_sessions SET turns=5 WHERE name='old'")
        for i in (4, 5):
            conn.execute("INSERT INTO log_agent_turns(name,payload) VALUES (?,?)",
                         ("old", json.dumps({"question": f"q{i}", "report": "saved"})))
        store = SessionStore(conn)
        assert [n for n, _ in store.history("old")] == [4, 5]
        assert store.turn("old", 1) is None
        store.record_turn("old", "q6", 0, {"question": "q6"})
        assert store.turn("old", 6) == {"question": "q6"}
        store.touch("other", [], [], "m")
        store.record_turn("other", "new", 0, {"question": "new"})
        assert store.turn("other", 1) == {"question": "new"}
        assert [n for n, _ in SessionStore(conn).history("old")] == [4, 5, 6]


@pytest.mark.parametrize("arg", ["--turn", "--turn -1", "--turn 0", "--turn abc", "--turn 1.5"])
def test_bad_turn_selection(arg):
    with pytest.raises(ValueError):
        parse_turn_selection(arg)


def test_paths_with_spaces_and_backslashes():
    assert parse_turn_selection('--turn 3 "C:\\logs\\old report.json"') == (3, 'C:\\logs\\old report.json')
    assert parse_turn_selection('old report.md') == (None, 'old report.md')


def test_session_search_matches_unicode_without_decoding_reports(monkeypatch):
    from log_agent import sessions

    with sqlite3.connect(":memory:") as conn:
        store = SessionStore(conn)
        store.touch("matching", [], [], "m")
        store.record_turn("matching", "q", 0, {"report": "Straße 超时 100%_done", "summary": None})
        store.record_turn("matching", "q", 0, {"report": "STRASSE"})
        store.touch("other", [], [], "m")
        store.record_turn("other", "q", 0, {"report": "ordinary", "settings": {"value": "hidden"}})
        original = json.loads

        def guarded(raw, *args, **kwargs):
            assert '"report"' not in raw, "search must not decode report payloads"
            return original(raw, *args, **kwargs)

        monkeypatch.setattr(sessions.json, "loads", guarded)
        for query in ("STRASSE", "超时", "100%_done"):
            assert [s.name for s in store.search(query)] == ["matching"]
        assert not store.search("hidden")
        assert not store.search("' OR 1=1 --")
        assert len(store.search("")) == 2
        assert [s.name for s in store.search("OTHER")] == ["other"]


@pytest.mark.parametrize("number", ["²", "①", "١", "１", "01", "+1", "0", "-1", "1.0",
                                     str(2**63), "9" * 5000])
def test_show_and_save_reject_same_invalid_numbers(number, tmp_path, sample_log, monkeypatch):
    from log_agent.chat_state import parse_turn_number

    with pytest.raises(ValueError):
        parse_turn_number(number)
    with pytest.raises(ValueError):
        parse_turn_selection(f"--turn {number}")
    _patch(monkeypatch, [])
    result = run_chat(tmp_path, f"/show {number}\n/history\nexit\n", "-l", str(sample_log))
    assert result.exit_code == 0, result.output
    assert "用法：/show" in result.output
    assert "没有匹配的历史报告" in result.output


def test_search_skips_malformed_turn_without_hiding_metadata():
    with sqlite3.connect(":memory:") as conn:
        store = SessionStore(conn)
        store.touch("broken-session", [], [], "m")
        conn.execute("INSERT INTO log_agent_turns(name, payload, turn_number) VALUES (?, ?, ?)",
                     ("broken-session", '{"report": broken', 1))
        store.touch("healthy", [], [], "m")
        store.record_turn("healthy", "q", 0, {"report": "searchable"})
        assert [s.name for s in store.search("BROKEN-SESSION")] == ["broken-session"]
        assert [s.name for s in store.search("searchable")] == ["healthy"]
        assert store.search("absent") == []


@pytest.mark.parametrize("field", ["question", "summary", "report"])
@pytest.mark.parametrize("value", [0, False, True, 42, 1.0, 1e-7, 10**30, None, [], {},
                                   [True, None], {"key": False}, "Straße"])
def test_search_field_conversion_matches_history(field, value):
    with sqlite3.connect(":memory:") as conn:
        store = SessionStore(conn)
        store.touch("session", [], [], "m")
        store.record_turn("session", "q", 0, {field: value})
        queries = ["0", "1", "true", "false", "none", "42", "1.0", "1e-07", "key", "strasse",
                   str(value).casefold(), "not-present"]
        for query in queries:
            assert bool(store.search(query)) == bool(store.history("session", query)), (field, value, query)
