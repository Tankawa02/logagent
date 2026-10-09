"""网页设置页保存的 API Key：`~/.log-agent/credentials.toml`。

- 和 `config.toml` 分开存放：配置文件常被复制进项目、贴给同事，Key 不能跟着走。
- 文件权限 0600（只有本人可读写）；写入先落临时文件再原子替换，不会留下半截内容。
- 环境变量 `OPENAI_API_KEY` 优先：只有环境变量没设置时，才用文件里的 Key 补上。
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import tomllib
from pathlib import Path

API_KEY_ENV = "OPENAI_API_KEY"
_FIELD = "openai_api_key"
MAX_KEY_LENGTH = 512

# 启动时 Key 从哪里来："env" 用户自己设的环境变量；"file" 本文件；None 没有
_source: str | None = None


def credentials_path() -> Path:
    return Path.home() / ".log-agent" / "credentials.toml"


def validate_api_key(key: str) -> str:
    """返回去掉首尾空白的 Key；格式明显不对时抛 ValueError。"""
    key = key.strip()
    if not key:
        raise ValueError("API Key 不能为空")
    if len(key) > MAX_KEY_LENGTH:
        raise ValueError(f"API Key 太长（超过 {MAX_KEY_LENGTH} 个字符）")
    if any(ch.isspace() or not ch.isprintable() or ord(ch) > 0x7E for ch in key):
        raise ValueError("API Key 只能包含可见的 ASCII 字符，不能有空格或换行")
    return key


def read_api_key(path: Path | None = None) -> str | None:
    path = path or credentials_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get(_FIELD)
    return value.strip() or None if isinstance(value, str) else None


def save_api_key(key: str | None, path: Path | None = None) -> None:
    """保存 Key；key 为 None 时删除文件。"""
    path = path or credentials_path()
    if key is None:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()
        return
    key = validate_api_key(key)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    content = (
        "# log-agent 的 API Key（由 Web 设置页写入）。只有本人可读写，不要提交到仓库或发给别人。\n"
        "# 环境变量 OPENAI_API_KEY 设置时以环境变量为准。\n"
        f"{_FIELD} = {json.dumps(key)}\n"
    )
    # mkstemp 创建的文件本身就是 0600，写完再原子替换，避免出现权限过宽的中间状态
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):  # Windows 上 chmod 只影响只读位，忽略
        os.chmod(path, 0o600)


def load_into_environment(path: Path | None = None) -> str | None:
    """环境变量没有 Key 时，用文件里保存的 Key 补上；返回并记录 Key 的来源。"""
    global _source
    if os.environ.get(API_KEY_ENV, "").strip():
        _source = "env"
    else:
        key = read_api_key(path)
        if key:
            os.environ[API_KEY_ENV] = key
            _source = "file"
        else:
            _source = None
    return _source


def key_source() -> str | None:
    return _source
