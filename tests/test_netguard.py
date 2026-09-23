from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from typer.testing import CliRunner

from log_agent import cli, netguard
from log_agent.agent import _resolve_chat_model

from .conftest import ScriptedChatModel


def test_string_model_gets_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.setenv("LOG_AGENT_TIMEOUT", "45")
    monkeypatch.setenv("LOG_AGENT_MAX_RETRIES", "5")

    via_gateway = _resolve_chat_model("openai:kimi-k3", "https://gw.example.com/v1")
    assert via_gateway.model_name == "kimi-k3"
    assert via_gateway.request_timeout == 45 and via_gateway.max_retries == 5

    direct = _resolve_chat_model("openai:gpt-4.1", None)
    assert direct.request_timeout == 45 and direct.max_retries == 5


def test_bad_env_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_AGENT_TIMEOUT", "abc")
    monkeypatch.setenv("LOG_AGENT_MAX_RETRIES", "-1")
    assert netguard.request_timeout() == netguard.DEFAULT_TIMEOUT
    assert netguard.max_retries() == netguard.DEFAULT_MAX_RETRIES


def test_retry_watch_counts_sdk_retry_logs() -> None:
    watch = netguard.install_retry_watch()
    watch.reset()
    logging.getLogger("openai._base_client").info("Retrying request to %s in %f seconds", "/chat", 0.5)
    logging.getLogger("openai._base_client").info("Sending HTTP Request")
    assert watch.count == 1
    assert "第 1 次" in watch.active_note()
    watch.reset()
    assert watch.active_note() == ""


def test_non_api_errors_are_not_swallowed() -> None:
    assert netguard.describe_api_error(KeyError("x")) is None
    request = httpx.Request("POST", "https://gw.example.com/v1/chat/completions")
    assert "超时" in netguard.describe_api_error(openai.APITimeoutError(request=request))
    not_found = openai.NotFoundError("nope", response=httpx.Response(404, request=request), body=None)
    assert "--base-url" in netguard.describe_api_error(not_found)


class _FlakyModel(ScriptedChatModel):
    """先正常输出一段正文，第二次调用时模拟重试耗尽后的超时。"""

    def _stream(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        if self.cursor >= 1:
            raise openai.APITimeoutError(request=httpx.Request("POST", "https://gw.example.com/v1"))
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def test_api_failure_keeps_partial_report(
    sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from log_agent import agent as agent_module

    script = [
        AIMessage(
            content="### 初步发现\n\n10:00:03 出现 payment failed。\n\n",
            tool_calls=[{"name": "log_overview", "args": {"log_path": str(sample_log)}, "id": "o1", "type": "tool_call"}],
        ),
        AIMessage(content="不会走到这里"),
    ]
    model = _FlakyModel(script=script)
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    out_file = tmp_path / "report.json"

    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "-o", str(out_file)])

    assert result.exit_code == 1, result.output
    assert "Traceback" not in result.output
    assert "请求超时" in result.output
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["status"] == "error" and "超时" in data["error"]
    assert "payment failed" in data["report"]
