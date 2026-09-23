"""模型接口的超时 / 重试配置、重试可见性，以及把接口异常翻译成可操作的中文提示。"""

from __future__ import annotations

import logging
import os
import threading
import time

DEFAULT_TIMEOUT = 120.0
DEFAULT_MAX_RETRIES = 3

# openai / anthropic 官方 SDK 在自动重试前都会打这条 INFO 日志；监听它就能把"正在重试"显示出来。
_CLIENT_LOGGERS = ("openai._base_client", "anthropic._base_client")
_RETRY_MARKER = "Retrying request"
# 超过这个时间没有新的重试日志，就认为已经恢复，状态栏不再提示。
_RETRY_VISIBLE_SECONDS = 45.0


def _env_number(name: str, default: float, cast: type) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = cast(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def request_timeout() -> float:
    return float(_env_number("LOG_AGENT_TIMEOUT", DEFAULT_TIMEOUT, float))


def max_retries() -> int:
    return int(_env_number("LOG_AGENT_MAX_RETRIES", DEFAULT_MAX_RETRIES, int))


class RetryWatch(logging.Handler):
    """统计当前这一轮里 SDK 自动重试的次数，供状态栏展示。"""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._lock = threading.Lock()
        self._count = 0
        self._last = 0.0

    def emit(self, record: logging.LogRecord) -> None:
        if not isinstance(record.msg, str) or not record.msg.startswith(_RETRY_MARKER):
            return
        with self._lock:
            self._count += 1
            self._last = time.monotonic()

    def reset(self) -> None:
        with self._lock:
            self._count = 0
            self._last = 0.0

    @property
    def count(self) -> int:
        with self._lock:
            return self._count

    def active_note(self) -> str:
        with self._lock:
            if not self._count or time.monotonic() - self._last > _RETRY_VISIBLE_SECONDS:
                return ""
            return f"接口波动，自动重试第 {self._count} 次"


retry_watch = RetryWatch()
_installed = False


def install_retry_watch() -> RetryWatch:
    global _installed
    if not _installed:
        for name in _CLIENT_LOGGERS:
            logger = logging.getLogger(name)
            logger.addHandler(retry_watch)
            if logger.getEffectiveLevel() > logging.INFO:
                logger.setLevel(logging.INFO)
        _installed = True
    return retry_watch


def _status_code(exc: BaseException) -> int | None:
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def describe_api_error(exc: BaseException) -> str | None:
    """模型接口类异常返回一句中文说明；不是接口问题（代码 bug 等）返回 None，交给上层照常抛出。"""
    name = type(exc).__name__
    module = type(exc).__module__ or ""
    retries = max_retries()
    first_line = (str(exc).strip().splitlines() or [name])[0]

    if name in {"APITimeoutError", "ReadTimeout", "ConnectTimeout", "TimeoutException"}:
        return (
            f"模型接口请求超时（单次 {request_timeout():g}s，已自动重试 {retries} 次）。"
            "可以设置 LOG_AGENT_TIMEOUT 调大超时，或稍后再试。"
        )
    if name in {"APIConnectionError", "ConnectError", "ConnectionError"}:
        return f"无法连接模型接口（已自动重试 {retries} 次）：{first_line}。请检查网络、代理和 --base-url。"

    code = _status_code(exc)
    if code == 401 or name == "AuthenticationError":
        return "模型接口认证失败（401）：请检查 OPENAI_API_KEY 等密钥是否正确、是否过期。"
    if code == 403 or name == "PermissionDeniedError":
        return "模型接口拒绝访问（403）：当前密钥没有该模型或接口的权限。"
    if code == 404 or name == "NotFoundError":
        return "模型接口返回 404：请检查 --base-url 是否带了正确的路径（如 /v1），以及 -m 模型名是否存在。"
    if code == 429 or name == "RateLimitError":
        return f"模型接口限流或额度不足（429，已自动重试 {retries} 次）：{first_line}"
    if code is not None and code >= 500:
        return f"模型接口服务端错误（{code}，已自动重试 {retries} 次）：{first_line}"
    if code == 400 or name == "BadRequestError":
        return f"模型接口拒绝了请求（400）：{first_line}"
    if module.startswith(("openai", "anthropic", "httpx", "httpcore")) or name.endswith("APIError"):
        return f"模型接口出错（{name}）：{first_line}"
    return None
