"""配置文件：把每次都要写的模型、接口地址、源码目录等放进 TOML，命令行只写本次变化的部分。

查找顺序（后者覆盖前者）：
1. 用户级 `~/.log-agent/config.toml`
2. 项目级 `.log-agent.toml`：从当前目录逐级向上找，找到最近的一个
3. 环境变量 `LOG_AGENT_CONFIG` 指定的文件（设置后替代项目级查找）

优先级：命令行参数 > 环境变量（LOG_AGENT_MODEL 等）> 配置文件 > 内置默认值。

    model = "openai:qwen-max"
    base_url = "https://llm-gateway.example.com/v1"
    code = ["../sms-service"]      # 相对路径以配置文件所在目录为准
    timeout = 180

    [analyze]                      # 只对某个子命令生效的覆盖项
    verbose = true
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_FILE = ".log-agent.toml"
COMMANDS = ("analyze", "chat", "watch")

# 配置键 -> 对应的 CLI 参数名；值为 None 的键不走命令行参数，改为写入环境变量
_KEYS: dict[str, str | None] = {
    "model": "model",
    "base_url": "base_url",
    "code": "code",
    "skills": "skills",
    "encoding": "encoding",
    "no_redact": "no_redact",
    "max_steps": "max_steps",
    "budget": "budget",
    "verbose": "verbose",
    "db": "db",
    "memory": "memory",
    "timeout": None,
    "max_retries": None,
}
# 这些键有对应的环境变量：环境变量已设置时以环境变量为准
_ENV_OVERRIDES = {
    "model": "LOG_AGENT_MODEL",
    "base_url": "OPENAI_BASE_URL",
    "timeout": "LOG_AGENT_TIMEOUT",
    "max_retries": "LOG_AGENT_MAX_RETRIES",
}
_ONLY_FOR = {"db": ("chat",)}
_PATH_KEYS = {"code", "skills", "db"}
_PATH_LIST_KEYS = {"code", "skills"}


class ConfigError(Exception):
    pass


@dataclass
class LoadedConfig:
    files: list[Path] = field(default_factory=list)
    shared: dict[str, Any] = field(default_factory=dict)
    per_command: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def for_command(self, command: str) -> dict[str, Any]:
        merged = {**self.shared, **self.per_command.get(command, {})}
        return {k: v for k, v in merged.items() if command in _ONLY_FOR.get(k, COMMANDS)}


def user_config_path() -> Path:
    return Path.home() / ".log-agent" / "config.toml"


def find_project_config(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for folder in (current, *current.parents):
        candidate = folder / PROJECT_FILE
        if candidate.is_file():
            return candidate
    return None


def _normalize(raw: dict[str, Any], source: Path, section: str, warnings: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    where = f"{source}" + (f" [{section}]" if section else "")
    for raw_key, value in raw.items():
        key = raw_key.replace("-", "_")
        if key not in _KEYS:
            warnings.append(f"{where}: 忽略不认识的配置项 {raw_key}")
            continue
        if key in _PATH_KEYS:
            value = _resolve_paths(key, value, source.parent, where)
        out[key] = value
    return out


def _resolve_paths(key: str, value: Any, base: Path, where: str) -> Any:
    def resolve(item: Any) -> str:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{where}: {key} 必须是路径字符串")
        path = Path(os.path.expandvars(item)).expanduser()
        return str(path if path.is_absolute() else (base / path).resolve())

    if key in _PATH_LIST_KEYS:
        items = value if isinstance(value, list) else [value]
        return [resolve(item) for item in items]
    return resolve(value)


def _read(path: Path, config: LoadedConfig) -> None:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"配置文件格式错误 {path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"读取配置文件失败 {path}: {exc}") from exc

    shared = {k: v for k, v in data.items() if not isinstance(v, dict)}
    config.shared.update(_normalize(shared, path, "", config.warnings))
    for name, section in data.items():
        if not isinstance(section, dict):
            continue
        if name not in COMMANDS:
            config.warnings.append(f"{path}: 忽略不认识的配置段 [{name}]")
            continue
        config.per_command.setdefault(name, {}).update(_normalize(section, path, name, config.warnings))
    config.files.append(path)


def load_config(cwd: Path | None = None) -> LoadedConfig:
    config = LoadedConfig()
    user = user_config_path()
    if user.is_file():
        _read(user, config)

    explicit = os.environ.get("LOG_AGENT_CONFIG")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"LOG_AGENT_CONFIG 指向的文件不存在: {path}")
        _read(path.resolve(), config)
    else:
        project = find_project_config(cwd)
        if project and project.resolve() != user.resolve():
            _read(project, config)
    return config


def apply_to_environment(values: dict[str, Any]) -> None:
    """只作用于环境变量的配置项（超时、重试），环境变量已设置时不覆盖。"""
    for key in ("timeout", "max_retries"):
        if key in values:
            os.environ.setdefault(_ENV_OVERRIDES[key], str(values[key]))


def cli_defaults(values: dict[str, Any]) -> dict[str, Any]:
    """转成 click 的 default_map：只含命令行参数，且跳过已被环境变量覆盖的项。"""
    defaults: dict[str, Any] = {}
    for key, value in values.items():
        param = _KEYS.get(key)
        if param is None:
            continue
        env = _ENV_OVERRIDES.get(key)
        if env and os.environ.get(env):
            continue
        defaults[param] = value
    return defaults


_loaded = LoadedConfig()


def set_loaded(config: LoadedConfig) -> None:
    global _loaded
    _loaded = config


def loaded() -> LoadedConfig:
    return _loaded
