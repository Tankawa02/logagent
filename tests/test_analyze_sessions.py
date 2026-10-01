"""analyze 默认把结果存为会话：网页（log-agent serve）能看到，chat -s / 网页续问能带着上下文接着问。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import agent as agent_module
from log_agent import cli
from log_agent.sessions import SessionStore, default_db_path

from .conftest import ScriptedChatModel, tool_call
from .web_fixtures import report_text, spike_log

runner = CliRunner()
REAL_BUILD = agent_module.build_agent  # 导入时取原函数：各用例的 fixture 会把它换成剧本模型


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """每次 build_agent 都给一份新剧本：第一轮 analyze，之后的续问各自完整跑一遍。"""
    log = spike_log(tmp_path / "app.log")
    real = agent_module.build_agent
    seen: list[dict] = []

    def build(**kwargs):
        seen.append(kwargs)
        script = [tool_call("log_overview", f"o{len(seen)}", path=str(log)), AIMessage(content=report_text(log))]
        return real(model=ScriptedChatModel(script=script), checkpointer=kwargs.get("checkpointer"))

    monkeypatch.setattr(agent_module, "build_agent", build)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    return log, seen


def _messages(db: Path, thread: str) -> list:
    """和 chat 恢复历史一样走 get_state：checkpoint 里 messages 是增量通道，直接读表拿不到完整列表。"""
    from langgraph.checkpoint.sqlite import SqliteSaver

    conn = sqlite3.connect(str(db), check_same_thread=False)
    try:
        agent = REAL_BUILD(model=ScriptedChatModel(script=[AIMessage(content="")]), checkpointer=SqliteSaver(conn))
        return agent.get_state({"configurable": {"thread_id": thread}}).values.get("messages", [])
    finally:
        conn.close()


def test_analyze_saves_session_by_default(scripted, code_repo: Path) -> None:
    log, seen = scripted
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-c", str(code_repo), "-q", "为什么支付失败",
                                     "--timezone", "+08:00", "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    assert "已保存为会话" in result.output and "log-agent serve" in result.output
    db = default_db_path()
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        [info] = store.list()
        assert info.name.startswith("analyze-")
        assert info.logs == [str(log)] and info.code == [str(code_repo)]
        assert info.turns == 1 and info.title == "为什么支付失败"
        assert info.settings["origin"] == "analyze" and info.settings["timezone"] == "+08:00"
        payload = store.turn(info.name, 1)
    assert payload["status"] == "ok" and payload["structured_status"] == "valid"
    assert payload["evidence_check"]["verified"] == 2  # 存档的是和终端同一份核对结果
    assert seen[0]["checkpointer"] is not None
    # 对话 checkpoint 按会话名保存，chat -s / 网页续问能接上
    assert [m.type for m in _messages(db, info.name)][0] == "human"


def test_analyze_no_save_and_env(scripted, monkeypatch: pytest.MonkeyPatch) -> None:
    log, seen = scripted
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "--no-save", "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    assert "已保存为会话" not in result.output
    assert seen[0]["checkpointer"] is None
    monkeypatch.setenv("LOG_AGENT_NO_SAVE", "1")
    assert runner.invoke(cli.app, ["analyze", "-l", str(log), "--max-steps", "20"]).exit_code == 0
    assert not default_db_path().exists() or not SessionStore(sqlite3.connect(str(default_db_path()))).list()


def test_analyze_named_session_and_collision(scripted, tmp_path: Path) -> None:
    log, _ = scripted
    db = tmp_path / "s.db"
    first = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "incident-42", "--db", str(db), "--max-steps", "20"])
    assert first.exit_code == 0, first.output
    again = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "incident-42", "--db", str(db)])
    assert again.exit_code == 2 and "已存在" in again.output
    # 自动命名同一秒内重复运行时追加序号，不覆盖
    for _ in range(2):
        assert runner.invoke(cli.app, ["analyze", "-l", str(log), "--db", str(db), "--max-steps", "20"]).exit_code == 0
    with sqlite3.connect(str(db)) as conn:
        names = {s.name for s in SessionStore(conn).list()}
    assert "incident-42" in names and len(names) == 3


def test_analyze_save_failure_does_not_break_analysis(scripted, tmp_path: Path) -> None:
    log, _ = scripted
    bad = tmp_path / "is-a-dir.db"
    bad.mkdir()
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "--db", str(bad), "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    assert "不保存为会话" in result.output


def test_analyze_respects_config_no_save(scripted, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log, seen = scripted
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".log-agent.toml").write_text("[analyze]\nno_save = true\n", encoding="utf-8")
    monkeypatch.chdir(project)
    assert runner.invoke(cli.app, ["analyze", "-l", str(log), "--max-steps", "20"]).exit_code == 0
    assert seen[0]["checkpointer"] is None


def test_web_follow_up_continues_analyze_context(scripted, code_repo: Path) -> None:
    """网页续问 analyze 会话：沿用 checkpoint，只发问题本身，不再重发一遍日志清单上下文。"""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from log_agent.web.app import WebConfig, create_app

    log, _ = scripted
    assert runner.invoke(cli.app, ["analyze", "-l", str(log), "-c", str(code_repo), "-s", "incident",
                                   "--max-steps", "20"]).exit_code == 0
    db = default_db_path()
    client = TestClient(create_app(WebConfig(db_path=db, token="t", agent_factory=cli._web_agent_factory)))
    headers = {"Authorization": "Bearer t", "X-Log-Agent-Request": "1"}
    listed = client.get("/api/sessions", headers=headers).json()
    assert listed[0]["name"] == "incident" and listed[0]["origin"] == "analyze"
    with client.stream("POST", "/api/sessions/incident/chat", json={"question": "10:05 之前有征兆吗？"},
                       headers=headers) as response:
        events = [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]
    assert events[-1]["type"] == "RUN_FINISHED"
    humans = [m.content for m in _messages(db, "incident") if m.type == "human"]
    assert len(humans) == 2 and humans[1] == "10:05 之前有征兆吗？"
    assert "用户问题：" in humans[0]  # 第一轮是 analyze 带日志 / 源码清单的完整上下文
    detail = client.get("/api/sessions/incident", headers=headers).json()
    assert [t["turn"] for t in detail["turn_list"]] == [1, 2]
