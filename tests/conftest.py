from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from log_agent import logfile, redact, timefilter, tools


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """每个用例都从干净状态开始：脱敏开启、无强制编码、缓存清空、HOME 指向临时目录。"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    redact.set_enabled(True)
    logfile.set_forced_encoding(None)
    timefilter.reset_default_window()
    tools._file_list_cache.clear()
    yield
    redact.set_enabled(True)
    logfile.set_forced_encoding(None)
    timefilter.reset_default_window()

@pytest.fixture
def sample_log(tmp_path: Path) -> Path:
    lines = [
        "2026-06-09 10:00:00 INFO  service started",
        "2026-06-09 10:00:01 INFO  handling request id=1",
        "2026-06-09 10:00:02 WARN  slow query took 1200ms",
        "2026-06-09 10:00:03 ERROR [order] payment failed order=1001",
        "Traceback (most recent call last):",
        '  File "app/order.py", line 42, in pay',
        "KeyError: 'order_id'",
        "2026-06-09 10:00:04 INFO  handling request id=2",
        "2026-06-09 10:00:05 ERROR [order] payment failed order=1002",
        "2026-06-09 10:00:06 INFO  done",
    ]
    path = tmp_path / "app.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture
def code_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "node_modules" / "dep").mkdir(parents=True)
    (root / "app" / "order.py").write_text(
        "def pay(order):\n"
        "    # 支付入口\n"
        "    return order['order_id']\n",
        encoding="utf-8",
    )
    (root / "app" / "legacy.java").write_bytes("// 旧版订单服务\nclass Order { void pay() {} }\n".encode("gbk"))
    (root / "node_modules" / "dep" / "index.js").write_text("function pay() {}\n", encoding="utf-8")
    (root / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return root


class ScriptedChatModel(BaseChatModel):
    """按剧本依次返回消息的假模型，支持工具调用与流式输出，用来跑通整条 agent 链路。"""

    script: list[AIMessage]
    cursor: int = 0

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> ScriptedChatModel:
        return self

    def _next(self) -> AIMessage:
        message = self.script[min(self.cursor, len(self.script) - 1)]
        self.cursor += 1
        return message

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=self._next())])

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        message = self._next()
        chunk = AIMessageChunk(
            content=message.content,
            tool_call_chunks=[
                {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": i}
                for i, call in enumerate(message.tool_calls)
            ],
            usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        )
        yield ChatGenerationChunk(message=chunk)


def tool_call(name: str, call_id: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])
