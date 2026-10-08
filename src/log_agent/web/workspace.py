"""网页里新建分析会用到的本机接口：浏览目录、列出模型、按选定的日志 / 源码登记新会话。

只给本人（持有访问令牌）用：浏览目录等价于让浏览器看到运行 serve 的这台机器上的文件名，
和在终端里 `ls` 是同一个权限边界，所以不对分享链接开放。
"""

from __future__ import annotations

import os
import sqlite3
import string
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

MAX_ENTRIES = 2000
_MODELS_TTL = 300.0
_models_cache: dict[str | None, tuple[float, list[str]]] = {}
_models_lock = threading.Lock()


class CreateSessionRequest(BaseModel):
    logs: list[str] = Field(default_factory=list)
    code: list[str] = Field(default_factory=list)
    model: str | None = None
    since: str | None = None
    until: str | None = None
    timezone: str | None = None
    baseline: str | None = None
    encoding: str | None = None


def _entry(path: Path) -> dict[str, Any] | None:
    try:
        is_dir = path.is_dir()
        stat = path.stat()
    except OSError:
        return None
    return {
        "name": path.name or str(path),
        "path": str(path),
        "kind": "dir" if is_dir else "file",
        "size": None if is_dir else stat.st_size,
        "mtime": int(stat.st_mtime),
    }


def list_directory(
    raw: str | None, show_hidden: bool = False, query: str = "", dirs_only: bool = False
) -> dict[str, Any]:
    """列出目录。筛选词和「只要目录」在截断到 MAX_ENTRIES 之前生效，大目录里的条目也能被搜到。"""
    target = Path(raw).expanduser() if raw else Path.home()
    try:
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(400, f"路径无效：{raw}") from exc
    if target.is_file():
        target = target.parent
    if not target.is_dir():
        raise HTTPException(404, f"目录不存在：{target}")
    try:
        children = list(target.iterdir())
    except PermissionError as exc:
        raise HTTPException(403, f"没有权限读取：{target}") from exc
    except OSError as exc:
        raise HTTPException(400, f"读取目录失败：{exc}") from exc
    if not show_hidden:
        children = [c for c in children if not c.name.startswith(".")]
    needle = query.strip().lower()
    if needle:
        children = [c for c in children if needle in c.name.lower()]
    entries = [e for e in (_entry(c) for c in children) if e]
    if dirs_only:
        entries = [e for e in entries if e["kind"] == "dir"]
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].lower()))
    parent = target.parent if target.parent != target else None
    return {
        "path": str(target),
        "parent": str(parent) if parent else None,
        "sep": os.sep,
        "query": needle,
        "entries": entries[:MAX_ENTRIES],
        "total": len(entries),
        "truncated": len(entries) > MAX_ENTRIES,
    }


def expand_pattern(pattern: str) -> dict[str, Any]:
    """把路径栏里的通配符展开成文件，规则和新建会话时一致（resolve_log_inputs）。"""
    from ..inputs import LogInputError, resolve_log_inputs

    raw = pattern.strip()
    if not raw:
        raise HTTPException(400, "请输入通配符，如 /var/log/app/*.log")
    try:
        files = [p for p in resolve_log_inputs([raw]) if Path(p).is_file()]
    except LogInputError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not files:
        raise HTTPException(404, f"没有匹配的文件：{raw}")
    # 浏览器跳到第一个通配段之前的目录，用户能看到这些文件在哪
    literal: list[str] = []
    for part in Path(raw).expanduser().parts:
        if any(ch in part for ch in "*?["):
            break
        literal.append(part)
    base = Path(*literal) if literal else Path(files[0]).parent
    return {"files": files[:MAX_ENTRIES], "dir": str(base.resolve()), "truncated": len(files) > MAX_ENTRIES}


def places(conn: sqlite3.Connection | None) -> list[dict[str, str]]:
    """浏览器左侧的快捷位置：主目录、启动目录、最近用过的日志 / 源码目录、Windows 盘符。"""
    result: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(label: str, path: Path | str, kind: str) -> None:
        key = str(path)
        if key in seen or not Path(key).is_dir():
            return
        seen.add(key)
        result.append({"label": label, "path": key, "kind": kind})

    add("主目录", Path.home(), "home")
    add("启动目录", Path.cwd(), "cwd")
    if conn is not None:
        from ..sessions import SessionStore

        for info in SessionStore(conn).list()[:20]:
            for log in info.logs:
                add(Path(log).parent.name or str(Path(log).parent), Path(log).parent, "recent-log")
            for code in info.code:
                add(Path(code).name or code, code, "recent-code")
            if len(result) >= 12:
                break
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            add(f"{letter}:", f"{letter}:\\", "drive")
    else:
        add("/", Path("/"), "root")
    return result


def list_models(base_url: str | None, default: str) -> dict[str, Any]:
    from ..probe import list_models as probe_models

    now = time.monotonic()
    with _models_lock:
        cached = _models_cache.get(base_url)
    if cached and now - cached[0] < _MODELS_TTL:
        models = cached[1]
    else:
        try:
            models = [m if ":" in m else f"openai:{m}" for m in probe_models(base_url, timeout=8.0)]
        except Exception:  # noqa: BLE001 — 拿不到列表时前端退回手动输入
            models = []
        with _models_lock:
            _models_cache[base_url] = (now, models)
    return {"default": default, "models": models}


def validate_request(body: CreateSessionRequest) -> tuple[list[str], list[str], dict[str, Any]]:
    """校验并展开路径 / 时间设置；返回 (日志绝对路径, 源码绝对路径, 会话设置)。"""
    from ..compare import parse_range
    from ..inputs import LogInputError, resolve_log_inputs
    from ..timefilter import parse_bound, parse_timezone

    raw_logs = [p.strip() for p in body.logs if p and p.strip()]
    if not raw_logs:
        raise HTTPException(400, "至少选择一个日志文件")
    if "-" in raw_logs:
        raise HTTPException(400, "网页里不能从标准输入读取日志，请选择日志文件")
    try:
        logs = resolve_log_inputs(raw_logs)
    except LogInputError as exc:
        raise HTTPException(400, str(exc)) from exc
    for path in logs:
        if not Path(path).is_file():
            raise HTTPException(400, f"不是文件：{path}")

    code: list[str] = []
    for raw in body.code:
        if not raw or not raw.strip():
            continue
        path = Path(raw.strip()).expanduser().resolve()
        if not path.is_dir():
            raise HTTPException(400, f"源码目录不存在：{path}")
        if str(path) not in code:
            code.append(str(path))

    def clean(value: str | None) -> str | None:
        return value.strip() or None if isinstance(value, str) else None

    since, until = clean(body.since), clean(body.until)
    timezone = clean(body.timezone) or os.environ.get("LOG_AGENT_TIMEZONE") or "UTC"
    baseline, encoding = clean(body.baseline), clean(body.encoding)
    try:
        parse_timezone(timezone)
        if since:
            parse_bound(since, upper=False)
        if until:
            parse_bound(until, upper=True)
        if baseline:
            parse_range(baseline)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if encoding:
        import codecs

        try:
            codecs.lookup(encoding)
        except LookupError as exc:
            raise HTTPException(400, f"不认识的编码名：{encoding}") from exc

    settings = {
        "since": since, "until": until, "timezone": timezone, "baseline": baseline,
        "encoding": encoding, "budget": None, "max_steps": 120, "base_url": None,
        "no_redact": False, "origin": "chat",
    }
    return logs, code, settings
