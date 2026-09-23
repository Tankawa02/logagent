"""上游依赖契约测试。

log-agent 有几处行为依赖 deepagents / langgraph / openai 的内部细节（改写内置提示词、隐藏内置工具、
监听 SDK 的重试日志、靠回调看到子代理）。这些依赖一旦升级改了写法，我们的补丁不会报错，而是**悄悄失效**：
报告又变短、内置工具又冒出来、重试提示不再出现。这里用真实依赖把每个假设钉住，升级后跑测试就能第一时间发现。

某条失败时：先看上游改成了什么，再同步改 agent.py / netguard.py 里对应的常量或逻辑。
"""

from __future__ import annotations

from typing import Any

import httpx
import openai
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage

from log_agent import netguard
from log_agent.agent import _BREVITY_LINES, _HIDDEN_TOOLS, _TASK_SUMMARY_REPLACEMENT, _TASK_SUMMARY_RULE, build_agent
from log_agent.tools import ALL_TOOLS

from .conftest import ScriptedChatModel, tool_call


class _CapturingModel(ScriptedChatModel):
    """记录每次模型调用时实际看到的工具列表和系统提示词。"""

    calls: list[dict[str, Any]] = []
    pending_tools: list[Any] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _CapturingModel:
        self.pending_tools = list(tools)
        return self

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        system = next((m for m in messages if isinstance(m, SystemMessage)), None)
        self.calls.append({"tools": self.pending_tools, "system": system.text if system else ""})
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def _tool_name(tool: Any) -> str:
    return tool["name"] if isinstance(tool, dict) else tool.name


def _tool_description(tool: Any) -> str:
    return tool.get("description", "") if isinstance(tool, dict) else tool.description


def _run(script: list[AIMessage]) -> _CapturingModel:
    model = _CapturingModel(script=script, calls=[], pending_tools=[])
    agent = build_agent(model=model)
    with agent.stream_events(
        {"messages": [{"role": "user", "content": "hi"}]}, config={"recursion_limit": 30}, version="v3"
    ) as stream:
        for _ in stream.interleave("messages", "tool_calls"):
            pass
    return model


def test_upstream_prompt_still_contains_patched_text() -> None:
    """我们用精确字符串替换改写上游提示词；上游一改措辞，替换就会变成空操作。"""
    from deepagents.graph import BASE_AGENT_PROMPT
    from deepagents.middleware.subagents import TASK_TOOL_DESCRIPTION

    for line in _BREVITY_LINES:
        assert line in BASE_AGENT_PROMPT, f"deepagents 基础提示词里找不到要删除的行：{line!r}"
    assert _TASK_SUMMARY_RULE in TASK_TOOL_DESCRIPTION, "deepagents task 工具说明里找不到要替换的句子"


def test_model_sees_patched_prompt_and_only_our_tools() -> None:
    model = _run([AIMessage(content="done")])
    seen = model.calls[0]

    for line in _BREVITY_LINES:
        assert line.strip() not in seen["system"]

    names = {_tool_name(t) for t in seen["tools"]}
    ours = {t.__name__ for t in ALL_TOOLS}
    assert ours <= names
    leaked = names & _HIDDEN_TOOLS
    assert not leaked, f"应被隐藏的内置工具又出现了：{leaked}"
    # 精确比对：上游新增或改名了内置工具（比如 read_file 改叫 read）时，这里会失败，
    # 提醒我们判断新工具是否也要加进 _HIDDEN_TOOLS。
    assert names - ours == {"task", "write_todos"}, f"出现了未知的内置工具：{names - ours - {'task', 'write_todos'}}"

    task = next(t for t in seen["tools"] if _tool_name(t) == "task")
    description = _tool_description(task)
    assert _TASK_SUMMARY_REPLACEMENT in description and _TASK_SUMMARY_RULE not in description


def test_subagents_are_patched_too() -> None:
    model = _run(
        [
            tool_call("task", "t1", subagent_type="code-investigator", description="find"),
            AIMessage(content="sub done"),
            AIMessage(content="final"),
        ]
    )
    assert len(model.calls) == 3
    sub = model.calls[1]
    names = {_tool_name(t) for t in sub["tools"]}
    assert not names & _HIDDEN_TOOLS and "task" not in names


def test_callbacks_reach_subagent_tools() -> None:
    """子代理过程可见和 token 统计都靠回调能传进子代理；上游若改成不继承 config，这条会失败。"""

    class Recorder(BaseCallbackHandler):
        def __init__(self) -> None:
            self.tools: list[tuple[str, Any]] = []
            self.llm_runs = 0

        def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kwargs) -> None:
            self.tools.append((kwargs.get("name") or serialized.get("name"), kwargs.get("tool_call_id")))

        def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
            self.llm_runs += 1

    recorder = Recorder()
    model = _CapturingModel(
        script=[
            tool_call("task", "t1", subagent_type="code-investigator", description="find"),
            tool_call("list_code_files", "g1", code_dir="."),
            AIMessage(content="sub done"),
            AIMessage(content="final"),
        ],
        calls=[],
        pending_tools=[],
    )
    agent = build_agent(model=model)
    with agent.stream_events(
        {"messages": [{"role": "user", "content": "hi"}]},
        config={"callbacks": [recorder], "recursion_limit": 30},
        version="v3",
    ) as stream:
        for _ in stream.interleave("messages", "tool_calls"):
            pass

    assert ("task", "t1") in recorder.tools
    assert ("list_code_files", "g1") in recorder.tools
    assert recorder.llm_runs == 4


def test_openai_sdk_still_logs_retries() -> None:
    """状态栏的"自动重试第 N 次"靠监听 openai SDK 的重试日志；用真实客户端 + 假传输层验证它还在打。"""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503, headers={"retry-after-ms": "1"}, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "m",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            },
        )

    watch = netguard.install_retry_watch()
    watch.reset()
    client = openai.OpenAI(
        api_key="test",
        base_url="https://gw.example.com/v1",
        max_retries=2,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    client.chat.completions.create(model="m", messages=[{"role": "user", "content": "hi"}])
    assert len(attempts) == 2
    assert watch.count == 1, "openai SDK 的重试日志格式变了，更新 netguard._RETRY_MARKER"
    watch.reset()
