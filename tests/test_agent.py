from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from log_agent.agent import build_agent
from log_agent.logfile import clip_line

from .conftest import ScriptedChatModel

_seen: dict[str, Any] = {}


class _RecordingModel(ScriptedChatModel):
    def bind_tools(self, tools: Any, **kwargs: Any) -> _RecordingModel:
        _seen["tools"] = sorted(getattr(t, "name", None) or t.get("name") for t in tools)
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        _seen["system"] = next((m for m in messages if isinstance(m, SystemMessage)), None)
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_model_sees_only_our_tools_and_no_brevity_rule() -> None:
    _seen.clear()
    agent = build_agent(model=_RecordingModel(script=[AIMessage(content="### 结论\n\nok")]))
    agent.invoke({"messages": [{"role": "user", "content": "hi"}]})

    tools = _seen["tools"]
    assert "task" not in tools and "read_file" not in tools
    assert {"log_overview", "search_logs", "grep_code", "read_code_file"} <= set(tools)

    system = _seen["system"]
    text = system.content if isinstance(system.content, str) else " ".join(
        b.get("text", "") for b in system.content if isinstance(b, dict)
    )
    assert "Be concise and direct" not in text
    assert "行为 / 业务逻辑类" in text


def test_clip_line_centers_on_match() -> None:
    text = "a" * 3000 + "provider=nexmo" + "b" * 3000
    clipped = clip_line(text, 500, focus=3000)
    assert "provider=nexmo" in clipped
    assert clipped.startswith("… ") and "6014" in clipped
