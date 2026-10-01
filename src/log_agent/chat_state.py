"""会话分析设置与旧版会话恢复，独立于交互界面。"""

from __future__ import annotations

import os

SETTING_KEYS = ("since", "until", "timezone", "baseline", "encoding", "budget", "max_steps")
_ENV = {"model": "LOG_AGENT_MODEL", "timezone": "LOG_AGENT_TIMEZONE", "encoding": "LOG_AGENT_ENCODING",
        "base_url": "OPENAI_BASE_URL"}


def restored_value(ctx, name: str, current, saved: dict):
    """显式命令行 / 环境变量 > 会话 > 配置文件 / 内置默认值。"""
    source = ctx.get_parameter_source(name)
    if source is not None and source.name in {"COMMANDLINE", "ENVIRONMENT"}:
        return current
    if name in _ENV and os.environ.get(_ENV[name]):
        return os.environ[_ENV[name]]
    return saved.get(name, current)


def legacy_last_turn(state: dict | None, model: str) -> dict | None:
    """旧 checkpoint 只能恢复原文，不伪造当时没有存下来的来源或用量。"""
    from .export import build_payload
    from .render import TurnResult, content_to_text, parse_summary_line

    if not state:
        return None
    question = ""
    answer = ""
    for message in state.get("messages", []):
        content = content_to_text(getattr(message, "content", ""))
        if getattr(message, "type", "") == "human":
            question = content.rsplit("用户问题：", 1)[-1].strip()
            answer = ""
        elif getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
            answer = content
    if not question:
        return None
    summary = next((v for line in answer.splitlines() if (v := parse_summary_line(line))), ("", ""))
    result = TurnResult(report=answer, summary=summary[0], confidence=summary[1])
    payload = build_payload(result, question=question, logs=[], code=[], model=model)
    payload["provenance"] = "legacy_unknown"
    payload["generated_at"] = None
    payload["elapsed_seconds"] = None
    payload["usage"] = {}
    return payload


def remove_log(paths: list[str], value: str) -> list[str]:
    from pathlib import Path

    target = value.strip().strip('"').strip("'")
    if not target:
        raise ValueError("用法：/remove-log <完整路径或唯一文件名>")
    absolute = str(Path(target).expanduser().resolve())
    matches = [p for p in paths if p == absolute]
    if not matches and Path(target).name == target:
        matches = [p for p in paths if Path(p).name == target]
    if len(matches) != 1:
        raise ValueError("没有唯一匹配的日志，请用 /sources 查看并输入完整路径。")
    if len(paths) == 1:
        raise ValueError("至少需要保留一份日志，请先 /add-log 再移除。")
    return [p for p in paths if p != matches[0]]


def parse_turn_number(raw: str) -> int:
    """Accept positive ASCII decimals representable by SQLite's INTEGER binding."""
    import re

    if not re.fullmatch(r"[1-9][0-9]{0,18}", raw):
        raise ValueError("轮次必须是正整数")
    number = int(raw)
    if number > 2**63 - 1:
        raise ValueError("轮次超出支持范围")
    return number


def parse_turn_selection(arg: str) -> tuple[int | None, str]:
    """Optional leading --turn N; preserve spaces and Windows backslashes in paths."""
    import re

    raw = arg.strip()
    if not raw.startswith("--turn"):
        return None, raw.strip('"').strip("'")
    match = re.fullmatch(r"--turn\s+([1-9][0-9]*)(?:\s+(.*))?", raw)
    if not match:
        raise ValueError("用法：/save [--turn 正整数] [路径]，例如 /save --turn 3 report.md")
    try:
        number = parse_turn_number(match[1])
    except ValueError as exc:
        raise ValueError("用法：/save [--turn 正整数] [路径]，轮次超出支持范围") from exc
    return number, (match[2] or "").strip().strip('"').strip("'")
