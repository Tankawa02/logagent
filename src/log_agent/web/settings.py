"""网页设置页：查看 / 修改模型连接与常用配置，保存后立即作用于正在运行的 serve，不用重启。

写到哪里：
- 模型、接口地址、超时等：用户级 `~/.log-agent/config.toml` 的顶层配置项。只改这几行，注释和其它配置段原样保留，
  改完重新解析一遍，确认除了这几项其它内容都没变，否则拒绝写入。
- API Key：`~/.log-agent/credentials.toml`（0600，见 credentials.py），不写进 config.toml。

谁优先（与命令行一致）：启动参数 > 环境变量 > 项目级配置 / [chat] 段 > 用户级顶层配置 > 内置默认值。
被更高优先级覆盖的项会在页面上标出来源；启动参数和环境变量锁定的项不能在网页里改。
"""

from __future__ import annotations

import os
import re
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .. import credentials
from ..config import ConfigError, LoadedConfig, _read, find_project_config, load_config, user_config_path

# 配置键 -> 能覆盖它的环境变量
FIELDS: dict[str, str | None] = {
    "model": "LOG_AGENT_MODEL",
    "base_url": "OPENAI_BASE_URL",
    "timeout": "LOG_AGENT_TIMEOUT",
    "max_retries": "LOG_AGENT_MAX_RETRIES",
    "timezone": "LOG_AGENT_TIMEZONE",
    "memory": None,
}
LABELS = {
    "model": "默认模型",
    "base_url": "接口地址",
    "timeout": "请求超时",
    "max_retries": "重试次数",
    "timezone": "默认时区",
    "memory": "长期记忆",
}
ENV_VARS = frozenset(env for env in FIELDS.values() if env)
MEMORY_MODES = ("suggest", "explicit", "off")
_HIDDEN_URL = "（地址含凭据，已隐藏）"

_HEADER = re.compile(r"^\s*\[")
_lock = threading.Lock()


@dataclass
class SettingsContext:
    """serve 启动时的快照：哪些项被启动参数 / 用户自己的环境变量锁定。"""

    default_model: str
    read_only: bool = False
    cli_base_url: str | None = None
    env_locked: frozenset[str] = frozenset()
    key_from_env: bool = False
    cwd: Path | None = None


def snapshot_env() -> frozenset[str]:
    return frozenset(name for name in ENV_VARS if os.environ.get(name, "").strip())


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _number(raw: Any) -> float:
    if isinstance(raw, bool):
        raise ValueError("需要数字")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            pass
    raise ValueError("需要数字")


def validate_value(key: str, raw: Any) -> Any:
    """返回写进配置文件的值；None 表示删除该项（恢复默认）。"""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return None
    if key in ("model", "base_url", "timezone", "memory") and not isinstance(raw, str):
        raise ValueError("需要文本")
    if key == "model":
        value = raw.strip()
        if len(value) > 200 or any(ch.isspace() for ch in value):
            raise ValueError("模型名不能包含空格，且不超过 200 个字符")
        return value
    if key == "base_url":
        from ..onboarding import unsafe_url_reason

        value = raw.strip()
        reason = unsafe_url_reason(value)
        if reason:
            raise ValueError(f"{reason}。带凭据的地址请放在环境变量 OPENAI_BASE_URL 里")
        return value
    if key == "timeout":
        number = _number(raw)
        if not 1 <= number <= 3600:
            raise ValueError("范围 1–3600 秒")
        return int(number) if number.is_integer() else number
    if key == "max_retries":
        number = _number(raw)
        if not number.is_integer() or not 0 <= number <= 10:
            raise ValueError("需要 0–10 的整数")
        return int(number)
    if key == "timezone":
        from ..timefilter import parse_timezone

        value = raw.strip()
        try:
            parse_timezone(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value
    if key == "memory":
        value = raw.strip().lower()
        if value not in MEMORY_MODES:
            raise ValueError(f"可选 {' / '.join(MEMORY_MODES)}")
        return value
    raise ValueError("不支持的配置项")


# ---------------------------------------------------------------------------
# 改写 TOML：只动顶层的几行
# ---------------------------------------------------------------------------


def _toml_value(value: Any) -> str:
    import json

    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)  # JSON 字符串转义是 TOML 基本字符串的子集
    return repr(value)


def _key_pattern(key: str) -> re.Pattern[str]:
    names = {re.escape(key), re.escape(key.replace("_", "-"))}
    return re.compile(rf"^\s*(?:{'|'.join(names)})\s*=")


def update_toml_text(text: str, updates: dict[str, Any]) -> str:
    """把 updates 里的顶层键改成新值（None 删除），返回新文本。改写结果和预期不符时抛 ConfigError。"""
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"用户级配置文件格式错误，请先手动修正：{exc}") from exc

    lines = text.splitlines()
    boundary = next((i for i, line in enumerate(lines) if _HEADER.match(line)), len(lines))
    head, tail = lines[:boundary], lines[boundary:]
    for key, value in updates.items():
        pattern = _key_pattern(key)
        hits = [i for i, line in enumerate(head) if pattern.match(line)]
        if value is None:
            head = [line for i, line in enumerate(head) if i not in hits]
            continue
        line = f"{key} = {_toml_value(value)}"
        if hits:
            head[hits[0]] = line
        else:
            at = len(head)
            while at > 0 and not head[at - 1].strip():
                at -= 1
            head.insert(at, line)
    if not text.strip() and any(v is not None for v in updates.values()):
        head = ["# log-agent 用户级配置。Web 设置页会改写这里的顶层配置项，其余内容保持原样。", *head]
    if head and tail and head[-1].strip():
        head.append("")
    result = "\n".join(head + tail).rstrip("\n")
    result = result + "\n" if result else ""

    expected = dict(before)
    for key, value in updates.items():
        expected.pop(key.replace("_", "-"), None)
        if value is None:
            expected.pop(key, None)
        else:
            expected[key] = value
    try:
        after = tomllib.loads(result)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError("没能安全地改写用户级配置文件，请手动编辑") from exc
    if after != expected:
        raise ConfigError("没能安全地改写用户级配置文件（结构较复杂），请手动编辑")
    return result


def write_user_config(updates: dict[str, Any]) -> Path:
    from .manage import _atomic_write

    path = user_config_path()
    old = path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    new = update_toml_text(old, updates)
    if new != old:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _atomic_write(path, new)
    return path


# ---------------------------------------------------------------------------
# 读取各层配置、应用到运行中的服务
# ---------------------------------------------------------------------------


def _other_config_path(cwd: Path | None) -> Path | None:
    explicit = os.environ.get("LOG_AGENT_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        return path.resolve() if path.is_file() else None
    project = find_project_config(cwd)
    user = user_config_path()
    if project and (not user.exists() or project.resolve() != user.resolve()):
        return project
    return None


def _layer(path: Path | None) -> LoadedConfig:
    loaded = LoadedConfig()
    if path and path.is_file():
        _read(path, loaded)
    return loaded


def _defaults(ctx: SettingsContext) -> dict[str, Any]:
    from ..netguard import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT

    return {"model": ctx.default_model, "base_url": None, "timeout": DEFAULT_TIMEOUT,
            "max_retries": DEFAULT_MAX_RETRIES, "timezone": "UTC", "memory": "suggest"}


def _safe_url(value: Any) -> Any:
    from ..onboarding import unsafe_url_reason
    from ..probe import endpoint_label

    if isinstance(value, str) and value and unsafe_url_reason(value):
        return f"{endpoint_label(value)} {_HIDDEN_URL}"
    return value


def describe(ctx: SettingsContext, config: Any) -> dict[str, Any]:
    from ..probe import mask_key

    user_path = user_config_path()
    other_path = _other_config_path(ctx.cwd)
    user, other = _layer(user_path), _layer(other_path)
    merged = load_config(ctx.cwd).for_command("chat")
    defaults = _defaults(ctx)
    layers = (
        (other.per_command.get("chat", {}), "project", other_path, "[chat]"),
        (user.per_command.get("chat", {}), "user_section", user_path, "[chat]"),
        (other.shared, "project", other_path, None),
        (user.shared, "user", user_path, None),
    )

    fields: dict[str, Any] = {}
    for key, env in FIELDS.items():
        source, origin, effective = "default", None, defaults[key]
        if key == "base_url" and ctx.cli_base_url:
            source, origin, effective = "cli", "--base-url", ctx.cli_base_url
        elif env and env in ctx.env_locked:
            source, origin, effective = "env", env, os.environ.get(env)
        elif key in merged:
            effective = merged[key]
            for values, name, path, section in layers:
                if key in values:
                    source, origin = name, f"{path}{' ' + section if section else ''}"
                    break
        value = user.shared.get(key)
        fields[key] = {
            "value": _safe_url(value) if key == "base_url" else value,
            "effective": _safe_url(effective) if key == "base_url" else effective,
            "default": defaults[key],
            "source": source,
            "origin": origin,
            "locked": source in ("cli", "env"),
            "env": env,
        }

    active = os.environ.get(credentials.API_KEY_ENV, "").strip()
    saved = credentials.read_api_key()
    return {
        "config_path": str(user_path),
        "credentials_path": str(credentials.credentials_path()),
        "project_config": str(other_path) if other_path else None,
        "read_only": ctx.read_only,
        "can_chat": bool(config.can_chat and config.agent_factory is not None),
        "api_key": {
            "set": bool(active),
            "masked": mask_key(active) if active else None,
            "source": "env" if ctx.key_from_env else ("file" if saved else None),
            "locked": ctx.key_from_env,
            "saved": bool(saved),
        },
        "fields": fields,
    }


def apply(ctx: SettingsContext, config: Any) -> None:
    """按当前各层配置重新计算运行时设置。只改用户没有自己设置过的环境变量。"""
    from ..memory import normalize_mode
    from . import workspace

    values = load_config(ctx.cwd).for_command("chat")
    for key in ("timeout", "max_retries", "timezone"):
        env = FIELDS[key]
        if env in ctx.env_locked:
            continue
        if values.get(key) is not None:
            os.environ[env] = str(values[key])
        else:
            os.environ.pop(env, None)

    locked_model = "LOG_AGENT_MODEL" in ctx.env_locked and os.environ.get("LOG_AGENT_MODEL")
    config.default_model = locked_model or values.get("model") or ctx.default_model
    locked_url = "OPENAI_BASE_URL" in ctx.env_locked and os.environ.get("OPENAI_BASE_URL")
    config.base_url = ctx.cli_base_url or locked_url or values.get("base_url") or None
    config.memory_mode = normalize_mode(values.get("memory")) or "suggest"

    if not ctx.key_from_env:
        key = credentials.read_api_key()
        if key:
            os.environ[credentials.API_KEY_ENV] = key
        else:
            os.environ.pop(credentials.API_KEY_ENV, None)
    config.can_chat = not ctx.read_only and bool(os.environ.get(credentials.API_KEY_ENV, "").strip())

    with workspace._models_lock:  # Key / 地址变了，旧的模型列表不再可信
        workspace._models_cache.clear()


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


class SettingsUpdate(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, max_length=credentials.MAX_KEY_LENGTH + 64)
    clear_api_key: bool = False


class ConnectionTest(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=2048)
    api_key: str | None = Field(default=None, max_length=credentials.MAX_KEY_LENGTH + 64)


def register(app: FastAPI, *, require_owner: Callable[..., None], config: Any) -> None:
    def context() -> SettingsContext:
        if config.settings is None:
            raise HTTPException(503, "当前服务没有启用设置页")
        return config.settings

    @app.get("/api/settings", dependencies=[Depends(require_owner)])
    def get_settings() -> dict[str, Any]:
        try:
            return describe(context(), config)
        except ConfigError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.put("/api/settings", dependencies=[Depends(require_owner)])
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        ctx = context()
        unknown = sorted(set(body.values) - FIELDS.keys())
        if unknown:
            raise HTTPException(400, f"不支持的配置项：{', '.join(unknown)}")
        updates: dict[str, Any] = {}
        for key, raw in body.values.items():
            try:
                updates[key] = validate_value(key, raw)
            except ValueError as exc:
                raise HTTPException(400, f"{LABELS[key]}：{exc}") from exc
        key = None
        if body.api_key is not None and not body.clear_api_key:
            try:
                key = credentials.validate_api_key(body.api_key)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc

        with _lock:
            try:
                if updates:
                    write_user_config(updates)
                if body.clear_api_key:
                    credentials.save_api_key(None)
                elif key:
                    credentials.save_api_key(key)
                apply(ctx, config)
                return describe(ctx, config)
            except ConfigError as exc:
                raise HTTPException(400, str(exc)) from exc
            except OSError as exc:
                raise HTTPException(500, f"写入配置失败：{exc.strerror or exc}") from exc

    @app.post("/api/settings/test", dependencies=[Depends(require_owner)])
    def test_connection(body: ConnectionTest) -> dict[str, Any]:
        from ..probe import PING_TIMEOUT, endpoint_label, ping

        context()
        model = (body.model or "").strip() or config.default_model
        if ":" not in model:
            model = f"openai:{model}"
        base_url = config.base_url
        if body.base_url is not None:
            try:
                base_url = validate_value("base_url", body.base_url)
            except ValueError as exc:
                raise HTTPException(400, f"接口地址：{exc}") from exc

        openai_compatible = bool(base_url) or model.startswith("openai:")
        chat_model = None
        if body.api_key and body.api_key.strip():
            try:
                key = credentials.validate_api_key(body.api_key)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            if openai_compatible:
                try:
                    from langchain_openai import ChatOpenAI

                    # 用页面上还没保存的 Key 单独建一个客户端，不改进程环境变量，避免影响正在运行的分析
                    chat_model = ChatOpenAI(model=model.split(":", 1)[1] if model.startswith("openai:") else model,
                                            base_url=base_url or None, api_key=key,
                                            timeout=PING_TIMEOUT, max_retries=0)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "message": f"无法创建模型客户端：{type(exc).__name__}: {exc}",
                            "model": model, "endpoint": endpoint_label(base_url), "latency": 0}
        elif openai_compatible and not os.environ.get(credentials.API_KEY_ENV, "").strip():
            return {"ok": False, "message": "还没有 API Key：先在上面填写 Key 再测试。",
                    "model": model, "endpoint": endpoint_label(base_url), "latency": 0}

        result = ping(model, base_url, chat_model=chat_model)
        return {"ok": result.ok, "message": result.message, "latency": round(result.latency, 2),
                "served_model": result.served_model, "model": model, "endpoint": endpoint_label(base_url)}
