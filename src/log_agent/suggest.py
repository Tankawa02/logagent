"""chat 开场的候选问题：本地扫一遍日志概览（不调用模型），把高频错误变成可以直接选的问题。"""

from __future__ import annotations

from pathlib import Path

from .tools import log_overview

_MAX_SIGNATURE = 60


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _MAX_SIGNATURE else text[: _MAX_SIGNATURE - 1] + "…"


def suggest_questions(log_paths: list[str], limit: int = 3) -> list[str]:
    """按出现次数挑出最多 limit 条高频错误，生成候选问题；日志里没有 ERROR/FATAL 时返回空列表。"""
    candidates: list[tuple[int, str, int, str]] = []
    for path in log_paths:
        result = log_overview(path)
        if getattr(result, "status", "error") != "ok":
            continue
        name = Path(path).name
        for item in result.meta.get("top_errors") or []:
            candidates.append((item["count"], name, item["first_line"], item["signature"]))
    candidates.sort(key=lambda c: -c[0])

    questions: list[str] = []
    seen: set[str] = set()
    for count, name, line, signature in candidates:
        short = _clip(signature)
        if short in seen:
            continue
        seen.add(short)
        questions.append(f"「{short}」出现了 {count} 次（首次 {name}:{line}），根因是什么？")
        if len(questions) >= limit:
            break
    return questions
