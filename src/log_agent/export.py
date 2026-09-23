"""把一轮分析结果导出成 Markdown 或 JSON 文件。"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .render import TurnResult


def build_payload(result: TurnResult, *, question: str, logs: list[str], code: list[str], model: str) -> dict[str, Any]:
    return {
        "tool": "log-agent",
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "question": question,
        "logs": logs,
        "code": code,
        "model": model,
        "status": "interrupted" if result.interrupted else ("error" if result.error else "ok"),
        "error": result.error or None,
        "elapsed_seconds": result.elapsed,
        "usage": result.usage,
        "tool_calls": [asdict(t) for t in result.tools],
        "report": result.report,
    }


def to_markdown(payload: dict[str, Any]) -> str:
    meta = [
        f"- 问题：{payload['question']}",
        *[f"- 日志：`{p}`" for p in payload["logs"]],
        *[f"- 源码：`{p}`" for p in payload["code"]],
        f"- 模型：`{payload['model']}`",
        f"- 生成时间：{payload['generated_at']}",
        f"- 用时 {payload['elapsed_seconds']}s · 工具调用 {len(payload['tool_calls'])} 次"
        f" · tokens {payload['usage'].get('total', 0):,}",
    ]
    if payload["status"] != "ok":
        meta.append(f"- 状态：{'已中断' if payload['status'] == 'interrupted' else payload['error']}（报告可能不完整）")
    report = payload["report"].strip() or "_（没有生成报告内容）_"
    return "# 日志分析报告\n\n" + "\n".join(meta) + "\n\n---\n\n" + report + "\n"


def infer_format(path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    return "json" if path.suffix.lower() == ".json" else "markdown"


def write_report(path: Path, payload: dict[str, Any], fmt: str) -> Path:
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, ensure_ascii=False, indent=2) if fmt == "json" else to_markdown(payload)
    # 固定 UTF-8 + LF：Windows 记事本和 VS Code 都能正确识别，避免系统默认 GBK 写出乱码
    target.write_text(content, encoding="utf-8", newline="\n")
    return target.resolve()
