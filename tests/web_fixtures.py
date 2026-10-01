"""Web 测试与本地预览共用的造数工具：生成一份带错误尖峰的日志和一条已存档的 chat 会话。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from log_agent.evidence import check_analysis
from log_agent.export import build_payload
from log_agent.render import TurnResult
from log_agent.sessions import SessionStore

SESSION = "chat-web-demo"
QUESTION = "10:05 之后支付为什么大量失败？"


def spike_log(path: Path) -> Path:
    """10:00~10:10 每 10 秒一条 INFO，10:05:00~10:05:20 出现一波支付失败（含手机号，用来验证脱敏）。"""
    lines: list[str] = []
    for second in range(0, 600, 10):
        stamp = f"2026-06-09 10:{second // 60:02d}:{second % 60:02d}"
        lines.append(f"{stamp} INFO  handling request id={second}")
        if second == 120:
            lines.append(f"{stamp} WARN  slow query took 1200ms")
        if 300 <= second <= 320:
            for k in range(4):
                lines.append(f"{stamp} ERROR [order] payment failed order={1000 + second + k} phone=13812345678")
                lines.append("Traceback (most recent call last):")
                lines.append('  File "app/order.py", line 3, in pay')
                lines.append("KeyError: 'order_id'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def first_line(path: Path, needle: str) -> int:
    return next(i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if needle in line)


def report_text(log: Path) -> str:
    line = first_line(log, "payment failed")
    analysis = {
        "assessment": "finding",
        "confidence": "high",
        "conclusion": "10:05 起订单缺少 order_id 字段，pay() 直接取值抛 KeyError，导致支付批量失败。",
        "impact": "10:05:00~10:05:20 共 12 笔支付失败。",
        "next_steps": ["在 pay() 入口校验 order_id", "排查上游 10:05 的发布"],
        "issues": [{
            "title": "pay() 未处理缺失的 order_id",
            "symptoms": "ERROR payment failed 后紧跟 KeyError: 'order_id'",
            "impact": "支付请求直接失败",
            "evidence": [
                {"source": log.name, "line_start": line, "line_end": line + 3,
                 "excerpt": "payment failed order=1300\n…\nKeyError: 'order_id'"},
                {"source": "app/order.py", "line_start": 3, "line_end": 3, "excerpt": "return order['order_id']"},
                {"source": log.name, "line_start": 2, "line_end": 2, "excerpt": "这一行并不存在于日志里"},
            ],
            "root_cause_hypotheses": [{
                "explanation": "上游订单结构变更", "confidence": "medium",
                "reasoning": "错误从 10:05 开始集中出现，之前没有同类错误。",
            }],
            "open_questions": ["10:05 是否有发布？"],
            "recommendations": ["使用 order.get('order_id') 并返回明确错误"],
            "reproduction_conditions": ["请求体缺少 order_id"],
            "verification_steps": ["补丁后重放 10:05 的请求"],
        }],
        "open_questions": [],
    }
    return (
        "**一句话结论**：10:05 起订单缺少 order_id，pay() 抛 KeyError（可信度：高）\n\n"
        f"### 证据\n\n- `{log.name}:{line}` payment failed\n- `app/order.py:3` 直接取 order['order_id']\n\n"
        "```log-agent-report\n" + json.dumps(analysis, ensure_ascii=False) + "\n```\n"
    )


def seed_session(db: Path, log: Path, code: Path, *, name: str = SESSION, no_redact: bool = False) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    try:
        store = SessionStore(conn)
        settings = {"since": None, "until": None, "timezone": "+08:00", "baseline": None,
                    "encoding": None, "budget": None, "max_steps": 120}
        store.touch(name, [str(log)], [str(code)], "openai:gpt-test", settings)
        result = TurnResult(report=report_text(log), elapsed=12.5,
                            usage={"input": 900, "output": 300, "total": 1200})
        result.evidence_check = check_analysis(result.analysis, [str(log)], [str(code)])
        payload = build_payload(result, question=QUESTION, logs=[str(log)], code=[str(code)],
                                model="openai:gpt-test", settings={**settings, "no_redact": no_redact})
        store.record_turn(name, QUESTION, 1200, payload)
    finally:
        conn.close()
