"""拦截同一轮里参数完全相同的工具调用。

模型经常用一模一样的参数再调一次 search_logs / read_code_file（忘了已经查过，或者在原地打转）。
工具都是只读的，同一轮里同样的参数结果不会变，所以直接告诉模型"已经查过"，并附上上次的结果（较短时）
或开头预览（较长时），省下一步工具执行，也提醒它换个查法。

"一轮"以状态里最后一条用户消息为界：多轮对话 / watch 的每一轮、每次子代理委派都会带来新的用户消息，
缓存自然失效——日志在两轮之间追加了内容也不会拿到旧结果。只缓存成功和"无结果"类提示，报错不缓存
（可能是临时性的，例如文件刚被轮转）。
"""

from __future__ import annotations

import json
import threading
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

# 结果不超过这个长度时原样附上；更长的只给开头预览，避免同一份大结果反复占用上下文
REPEAT_FULL_CHARS = 4000
REPEAT_PREVIEW_CHARS = 1500
_MAX_RUNS = 8


def _messages(state: Any) -> list:
    if isinstance(state, dict):
        return list(state.get("messages") or [])
    return list(getattr(state, "messages", None) or [])


def _run_key(state: Any) -> str | None:
    for message in reversed(_messages(state)):
        if getattr(message, "type", "") == "human":
            return getattr(message, "id", None) or f"obj-{id(message)}"
    return None


def _call_key(name: str, args: Any) -> str:
    try:
        return name + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return name + repr(args)


class _Seen:
    __slots__ = ("content", "artifact", "order")

    def __init__(self, content: str, artifact: Any, order: int) -> None:
        self.content = content
        self.artifact = artifact
        self.order = order


class DuplicateCallMiddleware(AgentMiddleware):
    def __init__(self, tool_names: Iterable[str]) -> None:
        super().__init__()
        self.tool_names = frozenset(tool_names)
        self._runs: OrderedDict[str, dict[str, _Seen]] = OrderedDict()
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def _lookup(self, request) -> tuple[str | None, str | None, _Seen | None]:
        call = request.tool_call
        name = call.get("name")
        if name not in self.tool_names:
            return None, None, None
        run = _run_key(request.state)
        if run is None:
            return None, None, None
        key = _call_key(name, call.get("args"))
        with self._lock:
            seen = self._runs.get(run, {}).get(key)
            if run in self._runs:
                self._runs.move_to_end(run)
        return run, key, seen

    def _remember(self, run: str | None, key: str | None, result: Any) -> None:
        if run is None or key is None or not isinstance(result, ToolMessage):
            return
        artifact = result.artifact if isinstance(result.artifact, dict) else {}
        if result.status == "error" or artifact.get("status") == "error":
            return
        content = result.content if isinstance(result.content, str) else str(result.content)
        with self._lock:
            calls = self._runs.setdefault(run, {})
            self._runs.move_to_end(run)
            order = self._counts.get(run, 0) + 1
            self._counts[run] = order
            calls.setdefault(key, _Seen(content, artifact, order))
            while len(self._runs) > _MAX_RUNS:
                old, _ = self._runs.popitem(last=False)
                self._counts.pop(old, None)

    @staticmethod
    def _repeat(request, seen: _Seen) -> ToolMessage:
        call = request.tool_call
        name = call.get("name") or ""
        head = (f"[提示] 这次 {name} 调用与本轮之前的一次调用参数完全相同，"
                "工具只读、结果不会变化，未重复执行。需要新信息时请换关键词、行区间、时间窗口或文件。")
        if len(seen.content) <= REPEAT_FULL_CHARS:
            body = f"{head}\n上次的结果：\n{seen.content}"
        else:
            body = (f"{head}\n上次的结果较长（约 {len(seen.content):,} 字），请回看之前的工具结果；开头预览：\n"
                    f"{seen.content[:REPEAT_PREVIEW_CHARS]}\n…")
        artifact = {"status": "hint", "kind": "duplicate", "message": "与之前的调用相同，未重复执行",
                    "duplicate_of": seen.order}
        return ToolMessage(content=body, tool_call_id=call.get("id") or "", name=name, artifact=artifact)

    def wrap_tool_call(self, request, handler):
        run, key, seen = self._lookup(request)
        if seen is not None:
            return self._repeat(request, seen)
        result = handler(request)
        self._remember(run, key, result)
        return result

    async def awrap_tool_call(self, request, handler):
        run, key, seen = self._lookup(request)
        if seen is not None:
            return self._repeat(request, seen)
        result = await handler(request)
        self._remember(run, key, result)
        return result
