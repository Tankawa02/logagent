"""analyze 默认把结果存为会话：网页（log-agent serve）能看到，chat -s / 网页续问能带着上下文接着问。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import typer
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
    messages = _messages(db, info.name)
    assert [m.type for m in messages] == ["human", "ai", "tool", "ai"]
    assert "order_id" in messages[-1].text


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


@pytest.mark.parametrize("failure", ["put", "put_writes"])
@pytest.mark.parametrize("fail_on", [False, True])
def test_checkpoint_write_failure_keeps_completed_analysis(scripted, monkeypatch, failure, fail_on) -> None:
    from langgraph.checkpoint.sqlite import SqliteSaver

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk is full")

    monkeypatch.setattr(SqliteSaver, failure, fail)
    log, _ = scripted
    args = ["--fail-on", "medium"] if fail_on else []
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "failed-save", "--max-steps", "20", *args])
    assert result.exit_code == (3 if fail_on else 0), result.output
    assert "会话保存失败" in result.output
    with sqlite3.connect(str(default_db_path())) as conn:
        payload = SessionStore(conn).turn("failed-save", 1)
    assert payload["status"] == "ok" and payload["structured_status"] == "valid"


def test_unwritable_database_falls_back_before_agent_build(scripted, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("readonly database")

    monkeypatch.setattr(SessionStore, "reserve", fail)
    log, seen = scripted
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    assert "不保存为会话" in result.output.replace("\n", "")
    assert seen[0]["checkpointer"] is None


def test_export_failure_still_records_turn(scripted, tmp_path) -> None:
    log, _ = scripted
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "bad-export", "-o", str(tmp_path),
                                     "--max-steps", "20"])
    assert result.exit_code != 0
    with sqlite3.connect(str(default_db_path())) as conn:
        assert SessionStore(conn).turn("bad-export", 1)["status"] == "ok"
    assert _messages(default_db_path(), "bad-export")


@pytest.mark.parametrize("override", [False, True])
def test_chat_restores_analyze_settings_and_origin(scripted, override) -> None:
    from log_agent import redact

    log, seen = scripted
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "custom-name", "--no-redact",
                                     "--base-url", "https://initial.example/v1", "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    args = ["--base-url", "https://override.example/v1", "--redact"] if override else []
    result = runner.invoke(cli.app, ["chat", "-s", "custom-name", *args], input="exit\n")
    assert result.exit_code == 0, result.output
    expected = "https://override.example/v1" if override else "https://initial.example/v1"
    assert seen[-1]["base_url"] == expected
    assert redact.is_enabled() is override
    with sqlite3.connect(str(default_db_path())) as conn:
        info = SessionStore(conn).get("custom-name")
    assert info.settings["origin"] == "analyze"
    assert info.settings["base_url"] == expected
    assert info.settings["no_redact"] is not override


def test_concurrent_analyze_names_are_reserved_before_registration(tmp_path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor

    db = tmp_path / "reserved.db"
    with sqlite3.connect(str(db)) as conn:
        SessionStore(conn)
    monkeypatch.setattr(cli, "_analyze_session_name", lambda: "analyze-fixed")

    def reserve(_):
        saved = cli._open_analyze_session(db, None)
        assert saved is not None
        return saved

    with ThreadPoolExecutor(max_workers=4) as pool:
        sessions = list(pool.map(reserve, range(4)))
    try:
        assert {s.name for s in sessions} == {"analyze-fixed", "analyze-fixed-2", "analyze-fixed-3", "analyze-fixed-4"}
        with pytest.raises(typer.Exit):
            cli._open_analyze_session(db, "analyze-fixed")
    finally:
        for saved in sessions:
            saved.close()
    with sqlite3.connect(str(db)) as conn:
        assert SessionStore(conn).list() == []


@pytest.mark.parametrize("stage", ["memory", "agent", "registration"])
@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_failed_setup_releases_analyze_name(scripted, monkeypatch, stage, error) -> None:
    from log_agent import memory_cli

    log, _ = scripted

    def fail(*args, **kwargs):
        raise error("setup failed")

    target, attr = {"memory": (memory_cli, "open_session"), "agent": (agent_module, "build_agent"),
                    "registration": (SessionStore, "touch")}[stage]
    with monkeypatch.context() as patch:
        patch.setattr(target, attr, fail)
        result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "retryable"])
        assert result.exit_code != 0
    with sqlite3.connect(str(default_db_path())) as conn:
        assert SessionStore(conn).get("retryable") is None
    retry = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "retryable", "--max-steps", "20"])
    assert retry.exit_code == 0, retry.output


def test_registered_analyze_is_not_released_on_failure(tmp_path) -> None:
    saved = cli._open_analyze_session(tmp_path / "db", "registered")
    saved.register([], [], "test", {})
    saved.close()
    with sqlite3.connect(str(tmp_path / "db")) as conn:
        assert SessionStore(conn).get("registered") is not None


def test_registration_database_failure_releases_placeholder(tmp_path, monkeypatch) -> None:
    saved = cli._open_analyze_session(tmp_path / "db", "retryable")

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("registration failed")

    monkeypatch.setattr(saved.store, "touch", fail)
    saved.register([], [], "test", {})
    assert not saved.ok
    saved.close()
    with sqlite3.connect(str(tmp_path / "db")) as conn:
        assert SessionStore(conn).get("retryable") is None


def test_analyze_export_excludes_connection_metadata(scripted, tmp_path) -> None:
    log, _ = scripted
    output = tmp_path / "report.json"
    url = "https://user:secret@private.example/v1"
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "private", "--base-url", url,
                                     "-o", str(output), "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    assert "base_url" not in json.loads(output.read_text(encoding="utf-8"))["settings"]
    with sqlite3.connect(str(default_db_path())) as conn:
        store = SessionStore(conn)
        assert store.get("private").settings["base_url"] == url
        assert "base_url" not in store.turn("private", 1)["settings"]


def test_analyze_no_redact_is_redacted_on_share(scripted, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from log_agent.web.app import WebConfig, create_app

    log, _ = scripted
    secret = "sk-" + "a" * 32
    real = REAL_BUILD
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real(
        model=ScriptedChatModel(script=[AIMessage(content=report_text(log).replace("order_id", secret))]),
        checkpointer=kw.get("checkpointer"),
    ))
    result = runner.invoke(cli.app, ["analyze", "-l", str(log), "-s", "raw", "--no-redact",
                                     "--base-url", "https://private.example/v1", "--max-steps", "20"])
    assert result.exit_code == 0, result.output
    client = TestClient(create_app(WebConfig(db_path=default_db_path(), token="t", redact_owner=False)))
    headers = {"Authorization": "Bearer t", "X-Log-Agent-Request": "1"}
    assert secret in client.get("/api/sessions/raw/turns/1", headers=headers).text
    token = client.post("/api/sessions/raw/shares", json={}, headers=headers).json()["token"]
    for suffix in ["", "/turns/1", "/export?turn=1&format=json", "/export?turn=1&format=markdown"]:
        response = client.get(f"/api/share/{token}{suffix}")
        assert response.status_code == 200
        assert secret not in response.text
        assert "private.example" not in response.text
    with sqlite3.connect(str(default_db_path())) as conn:
        assert secret in SessionStore(conn).turn("raw", 1)["report"]
