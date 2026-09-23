"""单轮 tokens 预算：用到一定比例时让 agent 停止取证、基于已有证据收尾出报告。

计数走 LangChain 回调，主代理和子代理的模型调用都算在内；
收尾靠中间件：超过阈值后，下一次模型调用去掉全部工具，并在系统提示词末尾追加收尾指令，
模型只能直接作答，于是这一轮自然结束，而不是被 --max-steps 硬切断。
"""

from __future__ import annotations

import re
import threading
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import SystemMessage

from .subtrace import _usage_of

WRAP_UP_RATIO = 0.8

_SIZE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([km]?)\s*$", re.IGNORECASE)
_UNIT = {"": 1, "k": 1_000, "m": 1_000_000}


def parse_budget(text: str) -> int:
    """解析 `200k`、`1.5m`、`150000` 这类写法。

    Raises:
        ValueError: 格式不对，或小于 10k（太小的预算连一次日志概览都不够）。
    """
    match = _SIZE.match(text or "")
    if not match:
        raise ValueError(f"看不懂的预算写法：{text}（示例：200k、1.5m、150000）")
    value = int(float(match.group(1)) * _UNIT[match.group(2).lower()])
    if value < 10_000:
        raise ValueError(f"预算太小：{text}，至少 10k tokens")
    return value


def format_tokens(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m".replace(".0m", "m")
    if value >= 1_000:
        return f"{value / 1_000:.0f}k"
    return str(value)


class TokenBudget(BaseCallbackHandler):
    """线程安全：并行的子代理会在不同线程里触发回调。"""

    raise_error = False

    def __init__(self, limit: int, wrap_ratio: float | None = None) -> None:
        self.limit = limit
        self.threshold = int(limit * (WRAP_UP_RATIO if wrap_ratio is None else wrap_ratio))
        self._lock = threading.Lock()
        self._used = 0
        self._wrapped = False

    def reset(self) -> None:
        """chat 每轮开始时调用：预算按单轮计算，和 --max-steps 一致。"""
        with self._lock:
            self._used = 0
            self._wrapped = False

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def wrapped(self) -> bool:
        """本轮是否已经触发过收尾。"""
        with self._lock:
            return self._wrapped

    def should_wrap_up(self) -> bool:
        with self._lock:
            if self._used < self.threshold:
                return False
            self._wrapped = True
            return True

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        total = _usage_of(response)["total"]
        with self._lock:
            self._used += total


_MAIN_WRAP_UP = """## 预算提示（系统自动追加）

本轮已用约 {used} tokens，接近用户设置的上限 {limit}。现在停止调用工具（本次已不再提供工具），
基于目前已经拿到的证据直接写出最终报告，格式照常。证据链没闭合的地方在报告里明确标注"待确认"，
并写出下一步应该查什么；可信度按实际证据如实下调。"""

_SUB_WRAP_UP = """## 预算提示（系统自动追加）

本轮 tokens 接近用户设置的上限。停止调用工具，立即按返回格式交回目前已拿到的证据，
没查完的部分列在"推导"里说明还缺什么。"""


class BudgetMiddleware(AgentMiddleware):
    """超过收尾阈值后：去掉工具，并在系统提示词末尾追加收尾指令。"""

    def __init__(self, budget: TokenBudget, subagent: bool = False) -> None:
        super().__init__()
        self.budget = budget
        self.subagent = subagent

    def _patch(self, request):
        if not self.budget.should_wrap_up():
            return request
        note = _SUB_WRAP_UP if self.subagent else _MAIN_WRAP_UP.format(
            used=format_tokens(self.budget.used), limit=format_tokens(self.budget.limit)
        )
        blocks: list[Any] = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": f"\n\n{note}" if blocks else note})
        return request.override(system_message=SystemMessage(content_blocks=blocks), tools=[])

    def wrap_model_call(self, request, handler):
        return handler(self._patch(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._patch(request))
