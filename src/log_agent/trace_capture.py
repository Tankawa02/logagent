"""把模型的输入输出、工具的原始结果整理成可存档的 trace 明细（类似 LangSmith 的 run 详情）。

每次模型调用的完整上下文会随轮次越滚越大，逐次全量存档是平方级增长；
这里只存「相对上一次调用新增的消息」（通常就是上一批工具结果），第一次调用存完整输入。
上下文被中间件压缩 / 截断时前缀对不上，则退回存完整输入并标记出来。
单条文本都有长度上限，超出部分截掉并记下原始长度，界面上能看出被截断了。
"""

from __future__ import annotations

from typing import Any

from .render_common import content_to_text

MESSAGE_CHARS = 6000
OUTPUT_CHARS = 20000
REASONING_CHARS = 12000
TOOL_OUTPUT_CHARS = 8000

_ROLES = {"system": "system", "human": "user", "ai": "assistant", "tool": "tool"}


def clip(text: str, limit: int) -> dict[str, Any]:
    """返回 {"text", "chars"}；chars 是原始长度，大于 len(text) 表示被截断。"""
    return {"text": text[:limit], "chars": len(text)}


def reasoning_of(message: Any) -> str:
    """推理模型的思考过程：有的放在 additional_kwargs，有的是 content 里的 reasoning / thinking 块。"""
    extra = getattr(message, "additional_kwargs", None) or {}
    for key in ("reasoning_content", "reasoning"):
        value = extra.get(key)
        if isinstance(value, str) and value.strip():
            return value
    content = getattr(message, "content", None)
    parts: list[str] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict) or block.get("type") not in ("reasoning", "thinking"):
                continue
            value = block.get("reasoning") or block.get("thinking") or block.get("text")
            if isinstance(value, str):
                parts.append(value)
            for item in block.get("summary") or []:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def tool_requests_of(message: Any) -> list[dict[str, Any]]:
    requests = []
    for call in getattr(message, "tool_calls", None) or []:
        if isinstance(call, dict):
            requests.append({"name": str(call.get("name") or ""), "args": call.get("args") or {}, "id": str(call.get("id") or "")})
    return requests


def message_entry(message: Any) -> dict[str, Any]:
    role = _ROLES.get(str(getattr(message, "type", "")), str(getattr(message, "type", "") or "unknown"))
    entry: dict[str, Any] = {"role": role, **clip(content_to_text(getattr(message, "content", "")), MESSAGE_CHARS)}
    if role == "tool":
        entry["name"] = str(getattr(message, "name", "") or "")
        entry["tool_call_id"] = str(getattr(message, "tool_call_id", "") or "")
    elif role == "assistant":
        requests = tool_requests_of(message)
        if requests:
            entry["tool_requests"] = requests
    return entry


def input_delta(messages: list[Any], previous_count: int) -> tuple[list[dict[str, Any]], bool]:
    """返回 (要存档的输入消息, 是否为完整输入)。

    增量里去掉 assistant 消息：它们就是上一次模型调用的输出，trace 里已经存过一遍。
    """
    if previous_count <= 0 or len(messages) < previous_count:
        return [message_entry(m) for m in messages], True
    fresh = [m for m in messages[previous_count:] if getattr(m, "type", "") != "ai"]
    return [message_entry(m) for m in fresh], False


def output_detail(message: Any) -> dict[str, Any]:
    if message is None:
        return {}
    detail: dict[str, Any] = {"output_text": clip(content_to_text(getattr(message, "content", "")), OUTPUT_CHARS)}
    reasoning = reasoning_of(message)
    if reasoning:
        detail["reasoning"] = clip(reasoning, REASONING_CHARS)
    requests = tool_requests_of(message)
    if requests:
        detail["tool_requests"] = requests
    return detail


def tool_output_text(output: Any) -> str:
    return content_to_text(getattr(output, "content", output)) if output is not None else ""
