from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.render import ToolRecord, tool_trail
from log_agent.suggest import suggest_questions

from .conftest import ScriptedChatModel, tool_call

runner = CliRunner()


_SEEN: list[str] = []


class RecordingModel(ScriptedChatModel):
    """把每次调用时最后一条用户消息记到 _SEEN，用来断言 chat 实际发给模型的内容。"""

    def _remember(self, messages: list[BaseMessage]) -> None:
        humans = [m for m in messages if isinstance(m, HumanMessage)]
        if humans:
            _SEEN.append(str(humans[-1].content))

    def _generate(self, messages, stop=None, run_manager=None, **kwargs: Any):
        self._remember(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs: Any):
        self._remember(messages)
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def _patch(monkeypatch: pytest.MonkeyPatch, script: list[AIMessage]) -> list[str]:
    from log_agent import agent as agent_module

    _SEEN.clear()
    real_build = agent_module.build_agent
    monkeypatch.setattr(
        agent_module, "build_agent",
        lambda **kw: real_build(model=RecordingModel(script=script), checkpointer=kw.get("checkpointer")),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    return _SEEN


def _chat(sample_log: Path, tmp_path: Path, keys: str, *extra: str):
    return runner.invoke(
        cli.app, ["chat", "-l", str(sample_log), "--db", str(tmp_path / "s.db"), *extra], input=keys,
    )


# ---------------------------------------------------------------------------
# 候选问题
# ---------------------------------------------------------------------------


def test_suggest_questions_ranks_errors(sample_log: Path, tmp_path: Path) -> None:
    questions = suggest_questions([str(sample_log)])
    assert len(questions) == 1
    assert "出现了 2 次" in questions[0] and f"{sample_log.name}:4" in questions[0]

    quiet = tmp_path / "quiet.log"
    quiet.write_text("2026-06-09 10:00:00 INFO  ok\n", encoding="utf-8")
    assert suggest_questions([str(quiet)]) == []


def test_chat_number_picks_suggestion(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, [AIMessage(content="回答一"), AIMessage(content="回答二")])
    result = _chat(sample_log, tmp_path, "1\n1\nexit\n")
    assert result.exit_code == 0, result.output
    assert "输入编号直接提问" in result.output
    assert "出现了 2 次" in seen[0]
    # 候选只在第一问前有效，之后输入的数字按普通问题发送
    assert seen[-1] == "1"


def test_resumed_chat_skips_suggestions(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, [AIMessage(content="回答")])
    assert _chat(sample_log, tmp_path, "问题\nexit\n", "-s", "demo").exit_code == 0
    again = runner.invoke(cli.app, ["chat", "-s", "demo", "--db", str(tmp_path / "s.db")], input="exit\n")
    assert again.exit_code == 0, again.output
    assert "输入编号直接提问" not in again.output


# ---------------------------------------------------------------------------
# /retry
# ---------------------------------------------------------------------------


def test_chat_retry(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _patch(monkeypatch, [AIMessage(content="第一次"), AIMessage(content="第二次")])
    result = _chat(sample_log, tmp_path, "/retry\n为什么失败\n/retry 重点看 10:00:05\nexit\n")
    assert result.exit_code == 0, result.output
    assert "还没有可以重答的问题" in result.output
    retry = seen[-1]
    assert retry.startswith("请重新回答我上一个问题")
    assert "补充要求：重点看 10:00:05" in retry and retry.endswith("为什么失败")


# ---------------------------------------------------------------------------
# /add-log、/add-code
# ---------------------------------------------------------------------------


def test_chat_add_sources(
    sample_log: Path, code_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "gateway.log"
    other.write_text("2026-06-09 10:00:00 INFO  gw\n", encoding="utf-8")
    seen = _patch(monkeypatch, [AIMessage(content="一"), AIMessage(content="二")])
    keys = "\n".join([
        "问题一",
        "/add-log",
        f"/add-log {tmp_path / 'nope.log'}",
        f"/add-log {sample_log}",
        f"/add-log {other}",
        f"/add-code {tmp_path / 'nope'}",
        f"/add-code {code_repo}",
        "问题二",
        "/sources",
        "exit",
    ]) + "\n"
    result = _chat(sample_log, tmp_path, keys)
    assert result.exit_code == 0, result.output
    assert "用法：/add-log" in result.output
    assert "已经在当前会话里了" in result.output
    assert "源码目录不存在" in result.output
    assert "已追加日志文件" in result.output and "已追加源码目录" in result.output
    note = seen[-1]
    assert "我新增了日志文件" in note and str(other.resolve()) in note
    assert "我新增了源码目录" in note and str(code_repo.resolve()) in note
    assert note.endswith("问题二")

    import sqlite3

    from log_agent.sessions import SessionStore

    conn = sqlite3.connect(str(tmp_path / "s.db"))
    info = SessionStore(conn).latest()
    conn.close()
    assert info is not None and str(other.resolve()) in info.logs and str(code_repo.resolve()) in info.code


def test_add_log_before_first_question_goes_into_context(
    sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "gateway.log"
    other.write_text("2026-06-09 10:00:00 INFO  gw\n", encoding="utf-8")
    seen = _patch(monkeypatch, [AIMessage(content="一")])
    result = _chat(sample_log, tmp_path, f"/add-log {other}\n问题\nexit\n")
    assert result.exit_code == 0, result.output
    assert str(other.resolve()) in seen[0] and "我新增了" not in seen[0]


# ---------------------------------------------------------------------------
# 工具过程折叠
# ---------------------------------------------------------------------------


def _rec(name: str, subagent: str = "") -> ToolRecord:
    return ToolRecord(name, {}, "", False, 0.1, subagent)


def test_tool_trail_merges_repeats() -> None:
    trail = tool_trail([
        _rec("log_overview"), _rec("search_logs"), _rec("search_logs"), _rec("task"), _rec("grep_code", "code-investigator"),
    ])
    assert trail is not None
    assert trail.plain.startswith("查了 5 步：日志概览 → 搜索日志 ×2 → 委派子任务")
    assert tool_trail([]) is None


def test_trail_shown_only_without_verbose(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = [tool_call("log_overview", "c1", path=str(sample_log)), AIMessage(content="结论")]
    _patch(monkeypatch, script)
    quiet = runner.invoke(cli.app, ["analyze", "-l", str(sample_log)])
    assert quiet.exit_code == 0, quiet.output
    assert "查了 1 步：日志概览" in quiet.output

    _patch(monkeypatch, script)
    loud = runner.invoke(cli.app, ["analyze", "-l", str(sample_log), "-v"])
    assert loud.exit_code == 0, loud.output
    assert "查了 1 步" not in loud.output


# ---------------------------------------------------------------------------
# sessions list --search
# ---------------------------------------------------------------------------


def test_sessions_search(tmp_path: Path) -> None:
    import sqlite3

    from log_agent.sessions import SessionStore

    db = tmp_path / "s.db"
    conn = sqlite3.connect(str(db))
    store = SessionStore(conn)
    store.touch("payment-bug", ["/logs/pay.log"], [], "m")
    store.touch("gateway", ["/logs/Gateway.log"], [], "m")
    conn.close()

    hit = runner.invoke(cli.app, ["sessions", "list", "--db", str(db), "--search", "GATEWAY"], env={"COLUMNS": "150"})
    assert hit.exit_code == 0, hit.output
    assert "gateway" in hit.output and "payment-bug" not in hit.output
    miss = runner.invoke(cli.app, ["sessions", "list", "--db", str(db), "-S", "zzz"])
    assert "没有匹配 'zzz' 的会话" in miss.output
