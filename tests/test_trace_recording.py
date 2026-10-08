"""Trace 记录在中断 / 旧数据下的行为：未完成的模型调用和工具不能消失，缺失的度量不能当成 0。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

from log_agent.render import StreamRenderer
from log_agent.sessions import SessionStore


class _InterruptedMessage:
    """流出一个增量后被打断（相当于 Ctrl+C / 浏览器点停止）。"""

    output = None

    @property
    def text(self):
        yield "## 结论\n\n数据库连接池"
        raise KeyboardInterrupt


class _Agent:
    def __init__(self, events):
        self.events = events

    @contextmanager
    def stream_events(self, payload, config=None, version="v3"):
        yield SimpleNamespace(interleave=lambda *kinds: iter(self.events), output=None)


def _tool(call_id: str, name: str, completed: bool):
    return SimpleNamespace(
        tool_call_id=call_id, tool_name=name, input={"pattern": "timeout"},
        completed=completed, output="命中 3 处" if completed else None, error=None, ended=None,
    )


def test_interrupted_turn_keeps_running_model_call_and_tools():
    events = [
        ("tool_calls", _tool("a", "search_logs", completed=True)),
        ("tool_calls", _tool("b", "read_lines", completed=False)),
        ("messages", _InterruptedMessage()),
    ]
    result = StreamRenderer(verbose=False).run(_Agent(events), {"messages": []})

    assert result.interrupted
    assert "数据库连接池" in result.report  # 半截正文照常保留

    [call] = result.llm_calls
    assert call["incomplete"] is True
    assert call["input"] is None and call["output"] is None  # 用量未知，而不是 0
    assert call["first_token"] is not None and call["seconds"] >= 0

    by_name = {t.name: t for t in result.tools}
    assert set(by_name) == {"search_logs", "read_lines"}
    assert not by_name["search_logs"].incomplete
    running = by_name["read_lines"]
    assert running.incomplete and not running.failed
    assert running.started is not None and running.seconds >= 0


def test_trace_summaries_are_compact_and_skip_malformed_rows():
    conn = sqlite3.connect(":memory:")
    store = SessionStore(conn)
    store.touch("s1", ["/tmp/a.log"], [], "openai:gpt-test", {})
    store.record_turn("s1", "旧问题", 0, {"question": "旧问题", "provenance": "legacy_unknown",
                                          "elapsed_seconds": None, "usage": {}, "report": "x" * 10_000})
    store.record_turn("s1", "新问题", 120, {
        "question": "新问题", "model": "openai:gpt-test", "elapsed_seconds": 3.5,
        "usage": {"input": 100, "output": 20, "total": 120}, "report": "很长的报告",
        "tool_calls": [
            {"name": "search_logs", "seconds": 1.2, "failed": False, "args": {"pattern": "secret"}},
            {"name": "read_lines", "seconds": 0.4, "failed": False, "incomplete": True},
        ],
        "llm_calls": [{"seconds": 1.0}, {"seconds": 2.0}],
    })
    store.record_turn("s1", "坏数据", 0, {"question": "坏数据"})
    conn.execute("UPDATE log_agent_turns SET payload = 'not json' WHERE turn_number = 3")

    rows = store.trace_summaries(10)
    assert [r["turn"] for r in rows] == [2, 1]
    latest, legacy = rows
    assert "report" not in latest and "args" not in str(latest["tools"])
    assert latest["elapsed_seconds"] == 3.5 and latest["total"] == 120 and latest["llm_calls"] == 2
    assert latest["tools"] == [["search_logs", 1.2, 0, None], ["read_lines", 0.4, 0, 1]]
    assert legacy["provenance"] == "legacy_unknown" and legacy["elapsed_seconds"] is None
    assert legacy["llm_calls"] is None and legacy["tools"] == []
    assert store.trace_summaries(10, "missing") == []
