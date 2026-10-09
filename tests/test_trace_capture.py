from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from log_agent.subtrace import SubagentTracker
from log_agent.trace_capture import TOOL_OUTPUT_CHARS, input_delta, output_detail


def test_input_delta_first_call_is_full_then_only_new_non_assistant_messages() -> None:
    first = [SystemMessage("系统提示"), HumanMessage("为什么超时？")]
    entries, full = input_delta(first, 0)
    assert full and [e["role"] for e in entries] == ["system", "user"]

    ai = AIMessage("", tool_calls=[{"name": "grep_code", "args": {"q": "x"}, "id": "c1", "type": "tool_call"}])
    second = [*first, ai, ToolMessage("命中 3 处", tool_call_id="c1", name="grep_code")]
    entries, full = input_delta(second, len(first))
    assert not full
    assert entries == [{"role": "tool", "text": "命中 3 处", "chars": len("命中 3 处"), "name": "grep_code", "tool_call_id": "c1"}]

    # 上下文被压缩：前缀对不上，退回完整输入
    entries, full = input_delta([SystemMessage("摘要")], len(second))
    assert full and entries[0]["text"] == "摘要"


def test_output_detail_keeps_text_reasoning_and_tool_requests() -> None:
    message = AIMessage(
        "先看日志",
        additional_kwargs={"reasoning_content": "需要先确认时间窗"},
        tool_calls=[{"name": "search_logs", "args": {"pattern": "timeout"}, "id": "c2", "type": "tool_call"}],
    )
    detail = output_detail(message)
    assert detail["output_text"]["text"] == "先看日志"
    assert detail["reasoning"]["text"] == "需要先确认时间窗"
    assert detail["tool_requests"] == [{"name": "search_logs", "args": {"pattern": "timeout"}, "id": "c2"}]


def test_tracker_queues_only_main_agent_inputs() -> None:
    tracker = SubagentTracker()
    tracker.on_chat_model_start({}, [[HumanMessage("主代理")]], run_id=uuid4(), parent_run_id=None)
    task_run = uuid4()
    tracker.on_tool_start({"name": "task"}, "", run_id=task_run, parent_run_id=None, tool_call_id="t1")
    tracker.on_chat_model_start({}, [[HumanMessage("子代理")]], run_id=uuid4(), parent_run_id=task_run)
    assert [m.content for m in tracker.main_input(0)] == ["主代理"]
    assert tracker.main_input(1) is None
    assert TOOL_OUTPUT_CHARS > 0
