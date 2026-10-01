from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.export import build_payload, to_markdown
from log_agent.render import TurnResult
from log_agent.sessions import SessionStore

from .test_chat_commands import _patch

runner = CliRunner()


def run_chat(tmp_path: Path, inputs: str, *args: str):
    return runner.invoke(
        cli.app, ["chat", "--db", str(tmp_path / "sessions.db"), "--session", "case", "--memory", "off", *args],
        input=inputs,
    )


def test_report_keeps_sources_settings_and_generation_time(
    tmp_path: Path, sample_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [AIMessage(content="一句话结论：超时（可信度：中）")])
    extra = tmp_path / "extra.log"
    extra.write_text("ERROR unrelated\n", encoding="utf-8")
    first, changed, resumed = [tmp_path / f"{name}.json" for name in ("first", "changed", "resumed")]
    out = run_chat(
        tmp_path,
        f"为什么\n/save {first}\n/add-log {extra}\n/window 10:00~10:01\n/save {changed}\nexit\n",
        "-l", str(sample_log), "--timezone", "+08:00", "--budget", "20k",
    )
    assert out.exit_code == 0, out.output
    original = json.loads(first.read_text(encoding="utf-8"))
    assert json.loads(changed.read_text(encoding="utf-8")) == original
    assert original["logs"] == [str(sample_log)]
    assert original["settings"]["since"] is None
    copied = []
    monkeypatch.setattr("log_agent.clipboard.copy_text", lambda text: copied.append(text) or "test")
    out = run_chat(tmp_path, f"/save {resumed}\n/copy\n/settings\nexit\n")
    assert out.exit_code == 0, out.output
    assert "上次问题：为什么" in out.output
    assert json.loads(resumed.read_text(encoding="utf-8")) == original
    assert copied == [original["report"] + "\n"]
    assert "10:00" in out.output and "+08:00" in out.output and "20k" in out.output


def test_new_session_clears_answer_and_retry_even_within_same_second(
    tmp_path: Path, sample_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [AIMessage(content="old answer")])
    out_path = tmp_path / "wrong.json"
    out = run_chat(tmp_path, f"问题\n/new\n/save {out_path}\n/copy\n/retry\n/new\nexit\n", "-l", str(sample_log))
    assert out.exit_code == 0, out.output
    assert not out_path.exists()
    assert "还没有可以保存的回答" in out.output
    assert "还没有可以复制的回答" in out.output
    assert "还没有可以重答的问题" in out.output
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        assert len(SessionStore(conn).list()) == 3


def test_range_commands_change_tools_and_notify_model(
    tmp_path: Path, sample_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from log_agent.timefilter import default_window

    seen = _patch(monkeypatch, [AIMessage(content="first"), AIMessage(content="second"), AIMessage(content="third")])
    windows = []
    real_run = cli.StreamRenderer.run

    def capture(self, *args, **kwargs):
        windows.append(default_window().describe())
        return real_run(self, *args, **kwargs)

    monkeypatch.setattr(cli.StreamRenderer, "run", capture)
    out = run_chat(
        tmp_path,
        "问题一\n/window 10:00:03~10:00:04\n/baseline 09:00~09:30\n问题二\n"
        "/window bad\n/window off\n/baseline off\n问题三\nexit\n",
        "-l", str(sample_log),
    )
    assert out.exit_code == 0, out.output
    assert windows == ["开头 → 结尾", "10:00:03 → 10:00:04", "开头 → 结尾"]
    assert "10:00:03" in seen[1] and "09:00" in seen[1]
    assert "全文" in seen[2] and "基线：未设置" in seen[2]
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        store = SessionStore(conn)
        assert store.get("case").settings["since"] is None
        assert store.get("case").settings["baseline"] is None


def test_settings_restore_with_explicit_overrides(
    tmp_path: Path, sample_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch(monkeypatch, [AIMessage(content="answer")])
    out = run_chat(
        tmp_path, "第一问\nexit\n", "-l", str(sample_log), "--since", "10:00", "--until", "11:00",
        "--timezone", "+08:00", "--baseline", "09:00~09:30", "--budget", "20k", "-m", "openai:saved",
    )
    assert out.exit_code == 0, out.output
    (tmp_path / ".log-agent.toml").write_text('timezone = "-05:00"\nbudget = "40k"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    out = run_chat(tmp_path, "/retry 补充\nexit\n", "-l", str(sample_log), "--since", "10:30")
    assert out.exit_code == 0, out.output
    assert "重新回答" in seen[-1] and "第一问" in seen[-1] and "10:30" in seen[-1]
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        info = SessionStore(conn).get("case")
        assert info.settings["since"] == "10:30"
        assert info.settings["until"] == "11:00"
        assert info.settings["timezone"] == "+08:00"
        assert info.settings["budget"] == "20k"
        assert info.model == "openai:saved"
    monkeypatch.setenv("LOG_AGENT_TIMEZONE", "+09:00")
    assert run_chat(tmp_path, "exit\n").exit_code == 0
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        assert SessionStore(conn).get("case").settings["timezone"] == "+09:00"


def test_remove_log_disambiguates_and_keeps_one_file(
    tmp_path: Path, sample_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _patch(monkeypatch, [AIMessage(content="answer")])
    nested = tmp_path / "other"
    nested.mkdir()
    other = nested / sample_log.name
    other.write_text("ERROR second\n", encoding="utf-8")
    # 不同目录同名；在另一个 cwd 运行避免相对路径恰好命中其一。
    monkeypatch.chdir(nested.parent.parent)
    out = run_chat(
        tmp_path, f"/remove-log {sample_log.name}\n/remove-log {other}\n/remove-log {sample_log}\n问题\nexit\n",
        "-l", str(sample_log), "-l", str(other),
    )
    assert out.exit_code == 0, out.output
    assert "没有唯一匹配" in out.output and "至少需要保留" in out.output
    assert str(other) not in seen[-1] and str(sample_log) in seen[-1]
    assert other.exists()


def test_old_database_and_checkpoint_remain_usable(
    tmp_path: Path, sample_log: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch, [AIMessage(content="一句话结论：旧回答（可信度：低）")])
    assert run_chat(tmp_path, "旧问题\nexit\n", "-l", str(sample_log)).exit_code == 0
    # 模拟旧版 schema：没有设置和逐轮快照，但保留已有 checkpoint。
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        conn.execute("DROP TABLE log_agent_turns")
        conn.execute("DROP TABLE log_agent_session_settings")
    output = tmp_path / "legacy.json"
    out = run_chat(tmp_path, f"/save {output}\nexit\n")
    assert out.exit_code == 0, out.output
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["report"] == "一句话结论：旧回答（可信度：低）"
    assert data["provenance"] == "legacy_unknown" and data["logs"] == []
    assert "生成时间：未知" in to_markdown(data)
    assert "旧版会话未保存逐轮统计" in to_markdown(data)
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        assert SessionStore(conn).get("case").turns == 1


def test_store_snapshots_are_isolated_and_deleted() -> None:
    with sqlite3.connect(":memory:") as conn:
        store = SessionStore(conn)
        store.touch("one", [], [], "m", {"timezone": "UTC"})
        store.touch("two", [], [], "m")
        payload = build_payload(TurnResult(report="one"), question="q", logs=[], code=[], model="m")
        store.record_turn("one", "q", 1, payload)
        payload["report"] = "changed"
        assert store.last_turn("one")["report"] == "one"
        assert store.last_turn("two") is None
        assert store.delete("one")
        assert store.last_turn("one") is None
        assert conn.execute("SELECT count(*) FROM log_agent_session_settings").fetchone()[0] == 0


def test_path_completion_with_spaces(tmp_path: Path) -> None:
    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from log_agent.chat_input import make_completer

    (tmp_path / "app log.txt").write_text("", encoding="utf-8")
    text = f'/add-log "{tmp_path}/app'
    items = list(make_completer().get_completions(Document(text), CompleteEvent()))
    assert any(item.text == " log.txt" for item in items)


def test_new_session_uses_updated_state_for_model_and_export(tmp_path, sample_log, monkeypatch):
    seen = _patch(monkeypatch, [AIMessage(content="old answer"), AIMessage(content="new answer")])
    output = tmp_path / "new.json"
    out = run_chat(
        tmp_path,
        f"旧问题\n/window 10:00~10:01\n/new\n新问题\n/SAVE {output}\n/QUIT\n",
        "-l", str(sample_log),
    )
    assert out.exit_code == 0, out.output
    assert str(sample_log) in seen[-1] and "10:00" in seen[-1]
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["question"] == "新问题"
    assert report["report"] == "new answer"
    assert report["settings"]["since"] == "10:00"
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        store = SessionStore(conn)
        new_session, = [entry for entry in store.list() if entry.name != "case"]
        assert store.last_turn("case")["question"] == "旧问题"
        assert store.last_turn(new_session.name)["question"] == "新问题"
        assert f"-s {new_session.name}" in out.output
