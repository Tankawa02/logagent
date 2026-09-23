"""通过 LangChain 回调追踪子代理内部的工具调用与 token 用量。

`stream_events` 只推送主代理这一层的消息和工具调用；子代理在 `task` 工具内部
`invoke`，它的事件不会出现在主流里。但子代理继承了父级 config，回调会一路传下去，
所以用回调顺着 run 树往上找到所属的 `task`，就能把子代理的每一步挂到对应的委派行下面。
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler

_MAX_ANCESTRY = 256


@dataclass
class SubCall:
    """子代理里的一次工具调用。字段与主流的工具句柄对齐，渲染层可以直接复用。"""

    name: str
    args: dict[str, Any]
    started: float = field(default_factory=time.perf_counter)
    ended: float | None = None
    output: Any = None
    error: str | None = None

    @property
    def completed(self) -> bool:
        return self.ended is not None


def _usage_of(response: Any) -> dict[str, int]:
    totals = {"input": 0, "output": 0, "total": 0}
    for batch in getattr(response, "generations", None) or []:
        for generation in batch:
            usage = getattr(getattr(generation, "message", None), "usage_metadata", None) or {}
            totals["input"] += int(usage.get("input_tokens") or 0)
            totals["output"] += int(usage.get("output_tokens") or 0)
            totals["total"] += int(usage.get("total_tokens") or 0)
    return totals


class SubagentTracker(BaseCallbackHandler):
    """线程安全：并行的多个子代理会在不同线程里触发回调。"""

    raise_error = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._parent: dict[UUID, UUID | None] = {}
        self._task_runs: dict[UUID, str] = {}
        self._calls: dict[UUID, SubCall] = {}
        self._children: dict[str, list[SubCall]] = defaultdict(list)
        self._usage = {"input": 0, "output": 0, "total": 0}
        self._model_calls = 0

    # ---- 查询（渲染线程调用）----------------------------------------------

    def children(self, task_call_id: str) -> list[SubCall]:
        with self._lock:
            return list(self._children.get(task_call_id, ()))

    @property
    def usage(self) -> dict[str, int]:
        with self._lock:
            return dict(self._usage)

    @property
    def tool_count(self) -> int:
        with self._lock:
            return sum(len(calls) for calls in self._children.values())

    @property
    def model_calls(self) -> int:
        with self._lock:
            return self._model_calls

    # ---- run 树 -----------------------------------------------------------

    def _remember(self, run_id: UUID, parent_run_id: UUID | None) -> None:
        with self._lock:
            self._parent[run_id] = parent_run_id

    def _owner(self, run_id: UUID | None) -> str | None:
        """沿父链向上找最近的 `task` 工具，返回它的 tool_call_id；主代理自己的调用返回 None。"""
        current = run_id
        for _ in range(_MAX_ANCESTRY):
            if current is None:
                return None
            if current in self._task_runs:
                return self._task_runs[current]
            current = self._parent.get(current)
        return None

    # ---- 回调 -------------------------------------------------------------

    def on_chain_start(self, serialized: Any, inputs: Any, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        self._remember(run_id, parent_run_id)

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        self._remember(run_id, parent_run_id)

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        self._remember(run_id, parent_run_id)

    def on_llm_end(self, response: Any, *, run_id: UUID, parent_run_id: UUID | None = None, **kwargs: Any) -> None:
        with self._lock:
            if self._owner(run_id) is None:
                return
            self._model_calls += 1
            for key, value in _usage_of(response).items():
                self._usage[key] += value

    def on_tool_start(
        self,
        serialized: Any,
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        name = (serialized or {}).get("name") or kwargs.get("name") or ""
        with self._lock:
            self._parent[run_id] = parent_run_id
            owner = self._owner(parent_run_id)
            if name == "task" and owner is None:
                self._task_runs[run_id] = kwargs.get("tool_call_id") or str(run_id)
                return
            if owner is None:
                return
            call = SubCall(name=name, args=dict(inputs) if isinstance(inputs, dict) else {"input": input_str})
            self._calls[run_id] = call
            self._children[owner].append(call)

    def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            call = self._calls.get(run_id)
            if call is not None:
                call.output = output
                call.ended = time.perf_counter()

    def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        with self._lock:
            call = self._calls.get(run_id)
            if call is not None:
                call.error = str(error) or type(error).__name__
                call.ended = time.perf_counter()
