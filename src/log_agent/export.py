"""把一轮分析结果导出成 Markdown 或 JSON 文件。"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from . import __version__
from .render import TurnResult
from .report import ReportView, report_body


def build_payload(
    result: TurnResult, *, question: str, logs: list[str], code: list[str], model: str, settings: dict | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "analysis": deepcopy(result.analysis),
        "structured_status": result.structured_status,
        "evidence_check": deepcopy(result.evidence_check),
        "tool": "log-agent",
        "version": __version__,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "question": question,
        "logs": list(logs),
        "code": list(code),
        "settings": deepcopy(settings or {}),
        "model": model,
        "status": "interrupted" if result.interrupted else ("error" if result.error else "ok"),
        "error": result.error or None,
        "summary": result.summary or None,
        "confidence": result.confidence or None,
        "finding": result.finding,
        "budget_hit": result.budget_hit,
        "elapsed_seconds": result.elapsed,
        "usage": dict(result.usage),
        "tool_calls": [asdict(t) for t in result.tools],
        "report": result.report,
    }


def to_markdown(payload: dict[str, Any], view: str = "detailed") -> str:
    meta = [
        f"- 问题：{payload['question']}",
        *[f"- 日志：`{p}`" for p in payload["logs"]],
        *[f"- 源码：`{p}`" for p in payload["code"]],
        f"- 模型：`{payload['model']}`",
        f"- 生成时间：{payload['generated_at'] or '未知'}",
        ("- 用量：旧版会话未保存逐轮统计" if payload.get("provenance") == "legacy_unknown" else
         f"- 用时 {payload['elapsed_seconds']}s · 工具调用 {len(payload['tool_calls'])} 次"
         f" · tokens {payload['usage'].get('total', 0):,}"),
    ]
    analysis = payload.get("analysis")
    if analysis:
        meta.append(f"- 异常判定：{analysis['assessment']} · 可信度：{analysis['confidence']}")
    check = payload.get("evidence_check")
    if check:
        from .evidence import summary_line

        meta.append(f"- 证据核对：{summary_line(check)}")
    settings = payload.get("settings", {})
    if settings:
        meta.extend([
            f"- 时间范围：{settings.get('since') or '开头'} → {settings.get('until') or '结尾'}",
            f"- 时区：{settings.get('timezone', 'UTC')}",
            f"- 基线：{settings.get('baseline') or '未设置'}",
        ])
    if payload.get("provenance") == "legacy_unknown":
        meta.append("- 来源说明：旧版会话未保存逐轮来源和设置，本报告仅恢复原回答，来源无法核实。")
    if payload["status"] != "ok":
        meta.append(f"- 状态：{'已中断' if payload['status'] == 'interrupted' else payload['error']}（报告可能不完整）")
    report = report_body(payload, view).strip() or "_（没有生成报告内容）_"
    return "# 日志分析报告\n\n" + "\n".join(meta) + "\n\n---\n\n" + report + "\n"


def infer_format(path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    return "json" if path.suffix.lower() == ".json" else "markdown"


def write_report(path: Path, payload: dict[str, Any], fmt: str, view: str = "detailed") -> Path:
    ReportView(view)
    # Older persisted snapshots have a wording-derived finding; do not export it as a v2 assessment.
    if not payload.get("schema_version"):
        payload = {**payload, "schema_version": 2, "analysis": None, "structured_status": "missing", "finding": None}
    target = path.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    content = (json.dumps({**payload, "view": view, "rendered_report": report_body(payload, view)},
                         ensure_ascii=False, indent=2) if fmt == "json" else to_markdown(payload, view))
    # 固定 UTF-8 + LF：Windows 记事本和 VS Code 都能正确识别，避免系统默认 GBK 写出乱码
    target.write_text(content, encoding="utf-8", newline="\n")
    return target.resolve()
