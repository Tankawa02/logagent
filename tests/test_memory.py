from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.agent import SYSTEM_PROMPT, build_agent
from log_agent.memory import (
    FEATURE_HINT_AFTER_SESSIONS,
    MemorySession,
    MemoryStore,
    default_memory_path,
    project_key,
    similar,
)

from .conftest import ScriptedChatModel, tool_call

runner = CliRunner()


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "memory.db")
    yield s
    s.close()


def _session(store: MemoryStore, session: str = "s1", project: str | None = "/repo", mode: str = "suggest"):
    return MemorySession(store=store, mode=mode, project=project, session=session)


def test_project_key_uses_git_root(tmp_path: Path) -> None:
    repo = tmp_path / "svc"
    (repo / ".git").mkdir(parents=True)
    (repo / "src" / "app").mkdir(parents=True)
    assert project_key([str(repo / "src" / "app")]) == str(repo.resolve())
    assert project_key([]) is None


def test_similarity_is_forgiving_but_not_loose() -> None:
    assert similar("老通道指 Nexmo", "老通道指的是 Nexmo")
    assert not similar("老通道指 Nexmo", "prod 的短信都走 gateway-b")


def test_scopes_and_dedupe(store: MemoryStore) -> None:
    store.add("以后先写结论", "preference", None)
    store.add("老通道指 Nexmo", "term", "/repo")
    store.add("prod 走 gateway-b", "fact", "/other")
    assert [m.text for m in store.memories("/repo")] == ["以后先写结论", "老通道指 Nexmo"]

    memory, updated = store.add("老通道指的是 Nexmo", "term", "/repo")
    assert updated and memory.text == "老通道指的是 Nexmo"
    assert len(store.memories("/repo")) == 2


def test_single_mention_waits_until_second_session(store: MemoryStore) -> None:
    first = _session(store, "s1")
    first.suggest("老通道指 Nexmo", "term", "mention")
    assert first.finish_turn("老通道那边怎么了") == []
    first.suggest("老通道指的是 Nexmo", "term", "mention")
    first.finish_turn("再看看老通道")
    assert first.session_due() == [], "同一会话里重复出现不算"

    second = _session(store, "s2")
    second.suggest("老通道就是 Nexmo", "term", "mention")
    assert second.finish_turn("老通道又出问题了") == [], "重复出现的候选攒到会话结束再问"
    due = second.session_due()
    assert len(due) == 1 and due[0].sessions == ["s1", "s2"]


def test_explicit_trigger_without_model_suggestion(store: MemoryStore) -> None:
    mem = _session(store, mode="explicit")
    immediate = mem.finish_turn("记住：老通道指 Nexmo")
    assert [c.text for c in immediate] == ["老通道指 Nexmo"]
    assert immediate[0].signal == "explicit" and immediate[0].project == "/repo"

    preference = mem.finish_turn("以后都先写结论，再写证据")
    assert preference[0].kind == "preference" and preference[0].project is None


def test_correction_is_immediate_and_limited_per_turn(store: MemoryStore) -> None:
    mem = _session(store)
    mem.suggest("老通道指 Nexmo", "term", "correction")
    mem.suggest("新通道指 Twilio", "term", "mention")
    assert "足够" in mem.suggest("第三条", "fact", "mention")
    immediate = mem.finish_turn("不对，老通道是 Nexmo，新通道是 Twilio")
    assert [c.text for c in immediate] == ["老通道指 Nexmo"]


def test_rejections_and_deletions_block_resuggestion(store: MemoryStore) -> None:
    mem = _session(store)
    mem.suggest("老通道指 Nexmo", "term", "correction")
    store.reject(mem.finish_turn("不对")[0], permanent=False)
    mem.suggest("老通道指的是 Nexmo", "term", "correction")
    assert mem.finish_turn("不对") == []

    memory, _ = store.add("网关在 gateway-b", "fact", "/repo")
    store.remove(memory.id)
    mem.suggest("网关在 gateway-b", "fact", "explicit")
    assert mem.finish_turn("记住网关在 gateway-b") == []


def test_existing_memory_is_not_suggested_again(store: MemoryStore) -> None:
    store.add("老通道指 Nexmo", "term", "/repo")
    mem = _session(store)
    mem.suggest("老通道指 Nexmo", "term", "correction")
    assert mem.finish_turn("不对") == []


def test_stale_single_mentions_expire(tmp_path: Path) -> None:
    path = tmp_path / "memory.db"
    store = MemoryStore(path)
    mem = _session(store)
    mem.suggest("一次性的说法", "fact", "mention")
    mem.finish_turn("随口一提")
    with store.conn:
        store.conn.execute("UPDATE memory_candidates SET last_seen = '2000-01-01 00:00:00'")
    store.close()
    reopened = MemoryStore(path)
    assert reopened._candidates(None, all_projects=True) == []
    reopened.close()


def test_saved_text_is_redacted(store: MemoryStore) -> None:
    memory, _ = store.add("测试手机号 13800138000 用于回归", "fact", "/repo")
    assert "13800138000" not in memory.text and "138****8000" in memory.text


def test_prompt_section(store: MemoryStore) -> None:
    store.add("老通道指 Nexmo", "term", "/repo")
    store.add("以后先写结论", "preference", None)
    section = _session(store).prompt_section()
    assert section is not None and "suggest_memory" in section
    assert section.index("以后先写结论") < section.index("老通道指 Nexmo"), "偏好排在前面"

    explicit = _session(store, mode="explicit").prompt_section()
    assert explicit is not None and "suggest_memory" not in explicit
    assert _session(MemoryStore(store.path.parent / "empty.db"), mode="explicit").prompt_section() is None


_seen_prompts: list[str] = []
_seen_tools: list[list[str]] = []


class _RecordingModel(ScriptedChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _RecordingModel:
        _seen_tools.append([getattr(t, "name", None) or t.get("name") for t in tools])
        return self

    @staticmethod
    def _record(messages: list[BaseMessage]) -> None:
        _seen_prompts.append(next((m.text for m in messages if isinstance(m, SystemMessage)), ""))

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        self._record(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        self._record(messages)
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def test_agent_injects_memory_and_records_suggestions(store: MemoryStore) -> None:
    _seen_prompts.clear()
    _seen_tools.clear()
    store.add("以后先写结论", "preference", None)
    mem = _session(store)
    model = _RecordingModel(
        script=[tool_call("suggest_memory", "m1", text="老通道指 Nexmo", kind="term", signal="correction"), AIMessage(content="ok")]
    )
    agent = build_agent(model=model, memory=mem)
    agent.invoke({"messages": [{"role": "user", "content": "不对，老通道是 Nexmo"}]})

    assert _seen_prompts[0].startswith(SYSTEM_PROMPT.strip()[:40])
    assert "以后先写结论" in _seen_prompts[0] and "<user_memory>" in _seen_prompts[0]
    assert "suggest_memory" in _seen_tools[0]
    assert [c.text for c in mem.finish_turn("不对，老通道是 Nexmo")] == ["老通道指 Nexmo"]


def test_agent_without_memory_is_unchanged() -> None:
    _seen_prompts.clear()
    _seen_tools.clear()
    build_agent(model=_RecordingModel(script=[AIMessage(content="ok")])).invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert _seen_prompts[0] == SYSTEM_PROMPT
    assert "suggest_memory" not in _seen_tools[0]


def test_chat_confirms_correction_and_uses_it_next_session(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import agent as agent_module

    real_build = agent_module.build_agent
    scripts = [
        [tool_call("suggest_memory", "m1", text="老通道指 Nexmo", kind="term", signal="correction"), AIMessage(content="好的")],
        [AIMessage(content="第二次会话")],
    ]
    monkeypatch.setattr(
        agent_module, "build_agent",
        lambda **kw: real_build(model=_RecordingModel(script=scripts.pop(0)), checkpointer=kw.get("checkpointer"), memory=kw.get("memory")),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    db = tmp_path / "sessions.db"
    _seen_prompts.clear()

    first = runner.invoke(
        cli.app, ["chat", "-l", str(sample_log), "--db", str(db)], input="不对，老通道是 Nexmo\ny\n/memory\nexit\n"
    )
    assert first.exit_code == 0, first.output
    assert "可能值得记住" in first.output and "已记住" in first.output
    assert "老通道指 Nexmo" in first.output

    second = runner.invoke(cli.app, ["chat", "-l", str(sample_log), "--db", str(db)], input="老通道怎么了\nexit\n")
    assert second.exit_code == 0, second.output
    assert "老通道指 Nexmo" in _seen_prompts[-1]

    listed = runner.invoke(cli.app, ["memory", "list"])
    assert "老通道指 Nexmo" in listed.output
    removed = runner.invoke(cli.app, ["memory", "rm", "1"])
    assert "已删除" in removed.output
    assert "老通道" not in runner.invoke(cli.app, ["memory", "list"]).output


def test_memory_commands_and_feature_hint(sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    added = runner.invoke(cli.app, ["memory", "add", "报告先写结论", "--global"])
    assert added.exit_code == 0 and "已记住" in added.output
    edited = runner.invoke(cli.app, ["memory", "edit", "1", "报告先写结论，再给证据"])
    assert "已更新" in edited.output
    assert runner.invoke(cli.app, ["memory", "add", "x", "--kind", "bogus"]).exit_code == 2
    runner.invoke(cli.app, ["memory", "rm", "1"])

    store = MemoryStore(default_memory_path())
    store.set_meta("runs", str(FEATURE_HINT_AFTER_SESSIONS - 1))
    store.close()
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    from log_agent import agent as agent_module

    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=ScriptedChatModel(script=[AIMessage(content="ok")])))
    shown = runner.invoke(cli.app, ["analyze", "-l", str(sample_log)])
    assert "log-agent memory add" in shown.output
    again = runner.invoke(cli.app, ["analyze", "-l", str(sample_log)])
    assert "log-agent memory add" not in again.output, "功能提示只出现一次"
