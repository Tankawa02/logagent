from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.agent import build_agent
from log_agent.logfile import clip_line

from .conftest import ScriptedChatModel, tool_call


def _tool_attr(tool: Any, key: str) -> str:
    value = tool.get(key) if isinstance(tool, dict) else getattr(tool, key, None)
    return value if isinstance(value, str) else ""


def _system_text(messages: list[BaseMessage]) -> str:
    system = next((m for m in messages if isinstance(m, SystemMessage)), None)
    if system is None:
        return ""
    if isinstance(system.content, str):
        return system.content
    return " ".join(b.get("text", "") for b in system.content if isinstance(b, dict))


class _RecordingModel(ScriptedChatModel):
    """记录每次模型调用看到的工具集合和系统提示词。"""

    calls: list[dict[str, Any]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _RecordingModel:
        self.calls.append({"tools": {_tool_attr(t, "name"): _tool_attr(t, "description") for t in tools}})
        return self

    def _record_system(self, messages: list[BaseMessage]) -> None:
        if self.calls and "system" not in self.calls[-1]:
            self.calls[-1]["system"] = _system_text(messages)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        self._record_system(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        self._record_system(messages)
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def test_main_agent_can_delegate_but_never_sees_builtin_fs_tools() -> None:
    model = _RecordingModel(script=[AIMessage(content="### 结论\n\nok")], calls=[])
    build_agent(model=model).invoke({"messages": [{"role": "user", "content": "hi"}]})

    main = model.calls[0]
    assert "task" in main["tools"]
    assert "read_file" not in main["tools"] and "execute" not in main["tools"]
    assert {"log_overview", "search_logs", "grep_code", "read_code_file"} <= set(main["tools"])

    task_description = main["tools"]["task"]
    assert "concise summary" not in task_description
    assert "code-investigator" in task_description and "log-investigator" in task_description

    assert "Be concise and direct" not in main["system"]
    assert "最终报告必须由你自己撰写" in main["system"]


def test_subagent_evidence_feeds_report_written_by_main_agent(
    sample_log: Path, code_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from log_agent import agent as agent_module

    script = [
        tool_call(
            "task",
            "t1",
            subagent_type="code-investigator",
            description=f"在 {code_repo} 里找 order_id 的读取位置，原样返回代码片段",
        ),
        tool_call("grep_code", "g1", code_dir=str(code_repo), pattern="order_id", regex=False),
        AIMessage(content="子代理取证：app/order.py:3 直接读取 order['order_id']"),
        AIMessage(content="### 结论\n\n主代理报告：支付时缺少 order_id，见 app/order.py:3。"),
    ]
    model = _RecordingModel(script=script, calls=[])
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    out_file = tmp_path / "report.md"

    result = CliRunner().invoke(
        cli.app,
        ["analyze", "-l", str(sample_log), "-c", str(code_repo), "-o", str(out_file), "--max-steps", "20"],
    )
    assert result.exit_code == 0, result.output

    report = out_file.read_text(encoding="utf-8")
    assert "主代理报告" in report
    assert "子代理取证" not in report

    subagent_calls = [c for c in model.calls if "task" not in c["tools"]]
    assert subagent_calls, "子代理应当被调用"
    sub_tools = set(subagent_calls[0]["tools"])
    assert {"grep_code", "read_code_file"} <= sub_tools
    assert not sub_tools & {"read_file", "grep", "execute", "search_logs"}


def test_clip_line_centers_on_match() -> None:
    text = "a" * 3000 + "provider=nexmo" + "b" * 3000
    clipped = clip_line(text, 500, focus=3000)
    assert "provider=nexmo" in clipped
    assert clipped.startswith("… ") and "6014" in clipped
