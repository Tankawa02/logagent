"""真正连一次模型服务：`doctor --ping` 和 `init` 用来验证 Key、网关和模型名。

只发一次最小请求（一句 "ping"，限制输出 token），不重试、超时较短，失败时把接口异常翻译成可操作的提示。
结果里只带主机名，不带完整 URL 和 Key。
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from threading import Thread
from typing import Any
from urllib.parse import urlsplit

PING_TIMEOUT = 20.0
API_KEY_ENV = "OPENAI_API_KEY"
_PING_PROMPT = "ping. Reply with exactly: ok"
# 有些模型（o 系列、部分网关）不接受 max_tokens，被拒时去掉上限再试一次
# SDK 自己的超时之外再留一点余量，正常情况下先由 SDK 报超时（信息更具体）
_GRACE_SECONDS = 2.0
_MAX_TOKENS_HINTS = ("max_tokens", "max_completion_tokens", "max_output_tokens")


@dataclass
class ProbeResult:
    ok: bool
    message: str
    latency: float = 0.0
    served_model: str = ""


def api_key() -> str:
    return os.environ.get(API_KEY_ENV, "").strip()


def mask_key(key: str) -> str:
    """只显示前 3 位和后 4 位，短 Key 整个遮住。"""
    if len(key) < 12:
        return "*" * len(key)
    return f"{key[:3]}…{key[-4:]}"


def endpoint_label(base_url: str | None) -> str:
    if not base_url:
        return "官方接口"
    try:
        host = urlsplit(base_url).hostname
    except ValueError:
        host = None
    return host or "（接口地址格式无效）"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # 多段内容（Anthropic / Responses API）
        return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    return str(content or "")


def _describe(exc: BaseException, timeout: float) -> str:
    from .netguard import describe_api_error

    message = describe_api_error(exc, retries=0, timeout=timeout)
    if message:
        return message
    first = (str(exc).strip().splitlines() or [type(exc).__name__])[0]
    return f"{type(exc).__name__}: {first}"


def _bounded(call: Callable[[], Any], deadline: float) -> Any:
    """在工作线程里执行，最多等 deadline 秒；SDK 自己的超时不生效时也不会卡住命令行。"""
    if deadline <= 0:
        raise FutureTimeout
    result: list[Any] = []
    errors: list[BaseException] = []

    def run() -> None:
        try:
            result.append(call())
        except BaseException as exc:  # preserve exceptions raised in the worker
            errors.append(exc)

    worker = Thread(target=run, name="log-agent-ping", daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise FutureTimeout
    if errors:
        raise errors[0]
    return result[0]


def ping(model: str, base_url: str | None, *, timeout: float = PING_TIMEOUT, chat_model: Any = None) -> ProbeResult:
    """发一次最小请求。chat_model 供测试注入；正常使用时按 model / base_url 构造，和 analyze 用同一条路径。

    必须同时做到“不重试”和“按 timeout 超时”：provider 不接受这两个参数时直接报告不支持，
    不退回到默认构造（那样会用 provider 自己的重试和超时）。另外再用工作线程限定最长等待时间。
    """
    from .agent import _resolve_chat_model

    try:
        llm = chat_model if chat_model is not None else _resolve_chat_model(
            model, base_url, timeout=timeout, retries=0, strict=True)
    except (TypeError, ValueError) as exc:
        if isinstance(exc, TypeError) and any(option in str(exc) for option in ("timeout", "max_retries")):
            return ProbeResult(False, f"该模型 provider 不支持设置超时和重试次数，无法可靠地执行 --ping：{_describe(exc, timeout)}")
        return ProbeResult(False, f"无法创建模型客户端：{_describe(exc, timeout)}")
    except Exception as exc:  # noqa: BLE001 — 缺 provider 包、模型名格式不对等，都当成配置错误报给用户
        return ProbeResult(False, f"无法创建模型客户端：{_describe(exc, timeout)}")

    started = time.monotonic()
    deadline = started + timeout + _GRACE_SECONDS
    try:
        try:
            reply = _bounded(lambda: llm.invoke(_PING_PROMPT, max_tokens=16), deadline - time.monotonic())
        except FutureTimeout:
            raise
        except Exception as exc:  # noqa: BLE001
            if not any(hint in str(exc) for hint in _MAX_TOKENS_HINTS):
                raise
            reply = _bounded(lambda: llm.invoke(_PING_PROMPT), deadline - time.monotonic())
    except FutureTimeout:
        return ProbeResult(False, f"模型接口在 {timeout:g}s 内没有响应。可以用 --ping-timeout 调大，或检查网络和网关。",
                           time.monotonic() - started)
    except Exception as exc:  # noqa: BLE001 — 接口错误统一翻译，不抛到命令行
        return ProbeResult(False, _describe(exc, timeout), time.monotonic() - started)
    latency = time.monotonic() - started
    meta = getattr(reply, "response_metadata", None) or {}
    served = str(meta.get("model_name") or meta.get("model") or "")
    text = " ".join(_text(getattr(reply, "content", "")).split())[:40]
    detail = f"返回“{text}”" if text else "返回为空（接口可用，但模型没有输出文字）"
    return ProbeResult(True, detail, latency, served)


def list_models(base_url: str | None, *, timeout: float = 10.0) -> list[str]:
    """列出 OpenAI 兼容接口上可用的模型 id（网关常常只开放一部分）。失败时抛出异常，由调用方决定是否忽略。"""
    from openai import OpenAI

    client = OpenAI(base_url=base_url or None, timeout=timeout, max_retries=0)
    ids = sorted({item.id for item in client.models.list()})
    # 嵌入 / 语音 / 图片模型不能用来对话
    skip = ("embed", "whisper", "tts", "dall-e", "moderation", "image", "audio", "transcribe", "realtime", "rerank")
    return [name for name in ids if not any(word in name.lower() for word in skip)]
