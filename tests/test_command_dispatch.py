"""命令可以脱离模型循环执行，且失败命令不能污染当前会话。"""
from __future__ import annotations

import sqlite3

import pytest

from log_agent.chat_commands import dispatch
from log_agent.chat_session import ChatSession
from log_agent.citations import CitationLinker
from log_agent.sessions import SessionStore
from log_agent.timefilter import default_window


@pytest.fixture
def state(sample_log):
    with sqlite3.connect(":memory:") as conn:
        state = ChatSession(
            store=SessionStore(conn), session="case", log_paths=[str(sample_log)], code_paths=[],
            model="test", settings={"since": None, "until": None, "baseline": None},
            linker=CitationLinker([str(sample_log)], []), mem=None,
            last_question="为什么失败", last={"report": "原回答"}, first_turn=False,
            suggestions=["候选问题"], source_note="待同步的来源",
        )
        state.persist()
        yield state


@pytest.mark.parametrize("command", ["/unknown", "/window bad", "/baseline bad", "/remove-log missing.log"])
def test_invalid_command_preserves_session(state, command):
    settings = dict(state.settings)
    linker = state.linker
    result = dispatch(state, command)
    assert result.question is None and not result.exit
    assert state.settings == settings
    assert state.store.get("case").settings == settings
    assert state.linker is linker
    assert state.source_note == "待同步的来源"
    assert state.suggestions == ["候选问题"]
    assert state.last_question == "为什么失败"
    assert state.last == {"report": "原回答"}
    assert not default_window()


def test_retry_preserves_argument_case_and_pending_context(state):
    result = dispatch(state, "/ReTrY  请检查 RequestID=AbC  ")
    assert result.question == "为什么失败"
    assert result.retry_prefix.endswith("补充要求：请检查 RequestID=AbC")
    assert state.source_note == "待同步的来源"
    assert state.last == {"report": "原回答"}


@pytest.mark.parametrize("command", ["/remember Keep Case", "/memory review", "/forget 3"])
def test_registered_memory_commands_delegate_without_submitting_question(state, monkeypatch, command):
    calls = []
    monkeypatch.setattr("log_agent.memory_cli.handle_slash", lambda *args: calls.append(args) or True)
    result = dispatch(state, command)
    name, _, arg = command.partition(" ")
    assert calls == [(None, name, arg)]
    assert result.question is None and not result.exit


@pytest.mark.parametrize("command", ["/EXIT", "/quit"])
def test_exit_aliases_preserve_report(state, command):
    assert dispatch(state, command).exit
    assert state.last == {"report": "原回答"}
