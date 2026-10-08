from __future__ import annotations

import sqlite3
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
    clean_text,
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

    memory, updated = store.add("老通道指 Nexmo", "term", "/repo")
    assert not updated and memory.text == "老通道指 Nexmo"
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


def test_confirm_keys_x_is_permanent_and_upper_n_is_plain_no(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import memory_cli

    mem = _session(store)
    mem.suggest("老通道指 Nexmo", "term", "correction")
    mem.suggest("新通道指 Twilio", "term", "correction")
    candidates = mem.finish_turn("不对")
    assert [c.text for c in candidates] == ["老通道指 Nexmo", "新通道指 Twilio"]
    answers = iter(["X", "N"])
    monkeypatch.setattr(memory_cli.console, "input", lambda *_a, **_k: next(answers))
    memory_cli.confirm(mem, candidates)

    rows = dict(store.conn.execute("SELECT text, until FROM memory_rejections").fetchall())
    assert rows["老通道指 Nexmo"] is None
    assert rows["新通道指 Twilio"] is not None
    assert store.memories(all_projects=True) == []


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


def test_similar_facts_require_explicit_replacement(store: MemoryStore) -> None:
    old, _ = store.add("生产环境短信统一走 gateway-a", "fact", "/repo")
    new_text = "生产环境短信统一走 gateway-b"
    candidate = store.record_candidate(new_text, "fact", "/repo", "correction", "s1")
    assert candidate is not None
    other, updated = store.add("测试环境短信统一走 gateway-a", "fact", "/repo")
    assert not updated and other.id != old.id
    assert store.get(old.id).text.endswith("gateway-a")
    replaced = store.accept(candidate, replace_id=old.id)
    assert replaced.id == old.id and replaced.text == new_text
    assert store.get(other.id).text.startswith("测试环境")
    assert store._candidate(candidate.id) is None


def test_candidates_keep_distinct_facts_and_scopes(store: MemoryStore) -> None:
    text = "生产环境短信统一走 gateway-a"
    global_candidate = store.record_candidate(text, "fact", None, "mention", "s1")
    local = store.record_candidate(text, "fact", "/repo", "mention", "s1")
    other = store.record_candidate(text.replace("生产", "测试"), "fact", "/repo", "mention", "s2")
    assert len({global_candidate.id, local.id, other.id}) == 3
    store.add(text, "fact", "/repo")
    assert store._candidate(global_candidate.id) is not None
    assert store._candidate(other.id) is not None
    assert store._candidate(local.id) is None


def test_redact_before_truncation() -> None:
    assert "sk-ABCDEFGH" not in clean_text("说明 " + "a" * 285 + " sk-ABCDEFGHIJKLMNOPQRSTUV")
    key = "-----BEGIN PRIVATE KEY-----\n" + "A" * 400 + "\n-----END PRIVATE KEY-----"
    assert clean_text(key) == "[私钥已脱敏]"


@pytest.mark.parametrize("operation", ["accept", "replace", "reject", "remove"])
def test_memory_mutations_rollback(store: MemoryStore, operation: str) -> None:
    old, _ = store.add("生产环境短信统一走 gateway-a", "fact", "/repo")
    candidate = store.record_candidate("生产环境短信统一走 gateway-b", "fact", "/repo", "correction", "s1")
    # 让第二步失败，验证第一步也会回滚。
    table = "memories" if operation in ("accept", "replace") else "memory_rejections"
    event = "UPDATE" if operation == "replace" else "INSERT"
    store.conn.execute(
        f"CREATE TRIGGER fail_write BEFORE {event} ON {table} "
        "BEGIN SELECT RAISE(ABORT, 'simulated failure'); END"
    )
    store.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
        if operation == "accept":
            store.accept(candidate)
        elif operation == "replace":
            store.accept(candidate, replace_id=old.id)
        elif operation == "reject":
            store.reject(candidate, permanent=True)
        else:
            store.remove(old.id)
    assert store.memories("/repo") == [old]
    assert store._candidate(candidate.id) == candidate
    assert store.conn.execute("SELECT count(*) FROM memory_rejections").fetchone()[0] == 0


@pytest.mark.parametrize("target_scope", ["/other", None])
def test_replacement_cannot_cross_scope(store: MemoryStore, target_scope: str | None) -> None:
    old, _ = store.add("相似项目事实", "fact", target_scope)
    candidate = store.record_candidate("相似项目事实更新", "fact", "/repo", "correction", "s1")
    with pytest.raises(ValueError):
        store.accept(candidate, replace_id=old.id)
    assert store.get(old.id) == old and store._candidate(candidate.id) == candidate


@pytest.mark.parametrize("choice", ["a", "r 1", "s", "r 999"])
def test_confirm_similar_candidate(store: MemoryStore, monkeypatch: pytest.MonkeyPatch, choice: str) -> None:
    from log_agent import memory_cli

    old, _ = store.add("生产环境短信统一走 gateway-a", "fact", "/repo")
    candidate = store.record_candidate("生产环境短信统一走 gateway-b", "fact", "/repo", "correction", "s1")
    answers = iter(["y", choice])
    monkeypatch.setattr(memory_cli.console, "input", lambda *_a, **_k: next(answers))
    memory_cli.confirm(_session(store), [candidate])
    if choice == "r 1":
        assert store.get(old.id).text.endswith("gateway-b")
        assert len(store.memories("/repo")) == 1
    else:
        assert store.get(old.id) == old
        assert len(store.memories("/repo")) == (2 if choice == "a" else 1)
    assert (store._candidate(candidate.id) is None) == (choice in ("a", "r 1"))


def test_manual_add_prompts_before_replacement(monkeypatch: pytest.MonkeyPatch) -> None:
    runner.invoke(cli.app, ["memory", "add", "生产环境短信统一走 gateway-a"])
    result = runner.invoke(cli.app, ["memory", "add", "测试环境短信统一走 gateway-a"], input="a\n")
    assert result.exit_code == 0 and "新增或替换" in result.output
    store = MemoryStore(default_memory_path())
    try:
        assert len(store.memories()) == 2
    finally:
        store.close()


def test_add_code_updates_memory_scope(sample_log: Path, code_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import agent as agent_module

    project = project_key([str(code_repo)])
    store = MemoryStore(default_memory_path())
    store.add("本项目使用 gateway-b", "fact", project)
    store.close()
    real_build = agent_module.build_agent
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setattr(
        agent_module, "build_agent",
        lambda **kw: real_build(model=_RecordingModel(script=[AIMessage(content="ok")]),
                                checkpointer=kw.get("checkpointer"), memory=kw.get("memory")),
    )
    _seen_prompts.clear()
    result = runner.invoke(
        cli.app, ["chat", "-l", str(sample_log), "--db", str(tmp_path / "sessions.db")],
        input=f"/add-code {code_repo}\n/remember 服务部署在华东\n分析错误\nexit\n",
    )
    assert result.exit_code == 0, result.output
    assert "本项目使用 gateway-b" in _seen_prompts[-1]
    store = MemoryStore(default_memory_path())
    try:
        saved = next(m for m in store.memories(project) if m.text == "服务部署在华东")
        assert saved.project == project
        assert store.memories() == []
    finally:
        store.close()


@pytest.mark.parametrize('answer', ['', 's\n', 'r 999\n'])
def test_memory_add_unsaved_exits_nonzero(answer: str) -> None:
    first = runner.invoke(cli.app, ['memory', 'add', '生产环境短信统一走 gateway-a'])
    assert first.exit_code == 0
    result = runner.invoke(cli.app, ['memory', 'add', '生产环境短信统一走 gateway-b'], input=answer)
    assert result.exit_code == 1 and '未保存' in result.output
    store = MemoryStore(default_memory_path())
    try:
        assert [m.text for m in store.memories()] == ['生产环境短信统一走 gateway-a']
    finally:
        store.close()


@pytest.mark.parametrize('scope', [None, '/repo'])
@pytest.mark.parametrize('signal', ['mention', 'correction', 'explicit'])
def test_saved_paraphrases_are_suppressed(store: MemoryStore, scope: str | None, signal: str) -> None:
    old, _ = store.add('老通道指 Nexmo', 'term', scope)
    assert store.record_candidate('老通道指的是 Nexmo', 'term', '/repo', signal, 's2') is None
    assert store.get(old.id) == old


@pytest.mark.parametrize('changed', [
    '测试环境短信统一走 gateway-a',
    '生产环境短信统一走 gateway-b',
    '生产环境短信不统一走 gateway-a',
])
def test_candidate_similarity_preserves_fact_changes(store: MemoryStore, changed: str) -> None:
    first = store.record_candidate('生产环境短信统一走 gateway-a', 'fact', '/repo', 'mention', 's1')
    second = store.record_candidate(changed, 'fact', '/repo', 'mention', 's2')
    assert first.id != second.id
    assert store.pending('/repo') == []


def test_candidate_paraphrases_do_not_cross_kind_or_scope(store: MemoryStore) -> None:
    first = store.record_candidate('老通道指 Nexmo', 'term', '/repo', 'mention', 's1')
    other_kind = store.record_candidate('老通道指的是 Nexmo', 'fact', '/repo', 'mention', 's2')
    other_scope = store.record_candidate('老通道指的是 Nexmo', 'term', None, 'mention', 's2')
    assert len({first.id, other_kind.id, other_scope.id}) == 3
    assert store.pending('/repo') == []


@pytest.mark.parametrize('scope', [None, '/repo'])
@pytest.mark.parametrize('operation', ['add', 'replace', 'edit'])
def test_saving_clears_visible_equivalent_candidates(store: MemoryStore, scope: str | None, operation: str) -> None:
    original = None
    if operation != 'add':
        original, _ = store.add('旧术语定义', 'term', scope)
    candidates = [
        store.record_candidate('老通道指的是 Nexmo', 'term', project, 'correction', 's1')
        for project in (None, '/repo', '/other')
    ]
    different_kind = store.record_candidate('老通道指的是 Nexmo', 'fact', '/repo', 'correction', 's1')
    different_fact = store.record_candidate('老通道指 Twilio', 'term', '/repo', 'correction', 's1')
    if operation == 'edit':
        store.update(original.id, '老通道指 Nexmo')
    else:
        store.add('老通道指 Nexmo', 'term', scope, replace_id=original.id if original else None)
    for candidate in candidates:
        visible = scope is None or candidate.project == scope
        assert (store._candidate(candidate.id) is None) == visible
    assert store._candidate(different_kind.id) is not None
    assert store._candidate(different_fact.id) is not None


@pytest.mark.parametrize('scope', [None, '/repo'])
@pytest.mark.parametrize('edited', [None, '老通道就是 Nexmo'])
def test_stale_review_reuses_visible_saved_memory(store: MemoryStore, scope: str | None, edited: str | None) -> None:
    candidate = store.record_candidate('老通道指的是 Nexmo', 'term', '/repo', 'correction', 's1')
    old_review = store.pending('/repo')
    saved, _ = store.add('老通道指 Nexmo', 'term', scope)
    assert store._candidate(candidate.id) is None
    assert store.accept(old_review[0], edited) == saved
    assert store.memories(all_projects=True) == [saved]
    assert store.pending('/repo') == []


def test_accept_checks_other_connections_and_preserves_replace_target(store: MemoryStore) -> None:
    candidate = store.record_candidate('老通道指的是 Nexmo', 'term', '/repo', 'correction', 's1')
    target, _ = store.add('老通道指 Twilio', 'term', '/repo')
    other = MemoryStore(store.path)
    try:
        saved, _ = other.add('老通道指 Nexmo', 'term', None)
        assert store.accept(candidate, replace_id=target.id) == saved
        assert store.get(target.id) == target
        assert len(store.memories(all_projects=True)) == 2
    finally:
        other.close()


@pytest.mark.parametrize('operation', ['add', 'replace', 'edit'])
def test_candidate_cleanup_failure_rolls_back_saved_memory(store: MemoryStore, operation: str) -> None:
    original = None
    if operation != 'add':
        original, _ = store.add('旧术语定义', 'term', None)
    candidate = store.record_candidate('老通道指的是 Nexmo', 'term', '/repo', 'correction', 's1')
    store.conn.execute(
        "CREATE TRIGGER fail_cleanup BEFORE DELETE ON memory_candidates "
        "BEGIN SELECT RAISE(ABORT, 'cleanup failure'); END"
    )
    store.conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match='cleanup failure'):
        if operation == 'edit':
            store.update(original.id, '老通道指 Nexmo')
        else:
            store.add('老通道指 Nexmo', 'term', None, replace_id=original.id if original else None)
    assert store.memories(all_projects=True) == ([original] if original else [])
    assert store._candidate(candidate.id) == candidate


@pytest.mark.parametrize('choice', ['a', 'r 1'])
def test_edited_current_candidate_honors_review_choice(store: MemoryStore, monkeypatch: pytest.MonkeyPatch, choice: str) -> None:
    from log_agent import memory_cli

    saved, _ = store.add('老通道指 Nexmo', 'term', '/repo')
    candidate = store.record_candidate('老通道指 Twilio', 'term', '/repo', 'correction', 's1')
    edited = '老通道指的是 Nexmo'
    answers = iter(['e', edited, choice])
    monkeypatch.setattr(memory_cli.console, 'input', lambda *_a, **_k: next(answers))
    memory_cli.confirm(_session(store), [candidate])
    memories = store.memories('/repo')
    if choice == 'a':
        assert len(memories) == 2
        assert store.get(saved.id) == saved
        assert next(m for m in memories if m.id != saved.id).text == edited
    else:
        assert len(memories) == 1
        assert store.get(saved.id).text == edited
    assert store._candidate(candidate.id) is None


def test_edited_current_candidate_validates_replacement(store: MemoryStore) -> None:
    saved, _ = store.add('老通道指 Nexmo', 'term', None)
    candidate = store.record_candidate('老通道指 Twilio', 'term', '/repo', 'correction', 's1')
    with pytest.raises(ValueError, match='范围不匹配'):
        store.accept(candidate, '老通道指的是 Nexmo', replace_id=saved.id)
    assert store.get(saved.id) == saved
    assert store._candidate(candidate.id) == candidate


def test_global_cleanup_uses_index_across_many_scopes(store: MemoryStore, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import memory as module

    for i in range(200):
        store.record_candidate(f'无关服务编号 {i}', 'term', f'/scope-{i}', 'correction', 's1')
    targets = [
        store.record_candidate('老通道指的是 Nexmo', 'term', scope, 'correction', 's1')
        for scope in (None, '/scope-0', '/scope-199')
    ]
    original = module.equivalent
    comparisons = []

    def counted(left: str, right: str) -> bool:
        comparisons.append((left, right))
        return original(left, right)

    monkeypatch.setattr(module, 'equivalent', counted)
    store.add('老通道指 Nexmo', 'term', None)
    assert len(comparisons) == len(targets)
    assert all(store._candidate(c.id) is None for c in targets)
    assert len(store.pending(all_projects=True)) == 200
    plan = store.conn.execute(
        'EXPLAIN QUERY PLAN SELECT id, text FROM memory_candidates WHERE kind = ? AND equivalence_key = ?',
        ('term', module._equivalence_key('老通道指 Nexmo')),
    ).fetchall()
    assert any('SEARCH' in row[3] and 'memory_candidates_equivalence' in row[3] for row in plan)


def test_cleanup_prefilter_still_checks_equivalence(store: MemoryStore) -> None:
    candidate = store.record_candidate('的a是b就c指', 'term', '/repo', 'correction', 's1')
    store.add('abc', 'term', None)
    assert store._candidate(candidate.id) == candidate


def test_old_candidate_database_migrates_and_cleans_paraphrases(tmp_path: Path) -> None:
    path = tmp_path / 'legacy.db'
    conn = sqlite3.connect(path)
    conn.execute('''CREATE TABLE memory_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, project TEXT, text TEXT NOT NULL,
        signal TEXT NOT NULL, occurrences INTEGER NOT NULL DEFAULT 1, sessions TEXT NOT NULL DEFAULT '[]',
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL
    )''')
    conn.execute(
        'INSERT INTO memory_candidates (kind, project, text, signal, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?)',
        ('term', '/legacy', '老通道指的是 Nexmo', 'correction', '2026-09-30', '2026-09-30'),
    )
    conn.commit()
    conn.close()
    store = MemoryStore(path)
    try:
        assert len(store.pending('/legacy')) == 1
        store.add('老通道指 Nexmo', 'term', None)
        assert store.pending('/legacy') == []
    finally:
        store.close()
    reopened = MemoryStore(path)
    try:
        assert len(reopened.memories()) == 1
        assert reopened.pending('/legacy') == []
    finally:
        reopened.close()


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "suggest"), (False, "off"), (True, "suggest"), ("OFF", "off"), (" Explicit ", "explicit"), ("nope", None), (0, None)],
)
def test_normalize_mode(value: object, expected: str | None) -> None:
    from log_agent.memory import normalize_mode

    assert normalize_mode(value) == expected


def test_web_runner_unknown_mode_fails_closed(tmp_path: Path) -> None:
    from log_agent.sessions import SessionInfo
    from log_agent.web.runner import _open_memory

    info = SessionInfo(
        name="s", logs=[], code=[str(tmp_path)], model="m", title="", turns=0, total_tokens=0, created_at="", updated_at=""
    )
    assert _open_memory(tmp_path / "m.db", "bogus", info) is None
    assert _open_memory(tmp_path / "m.db", "OFF", info) is None
