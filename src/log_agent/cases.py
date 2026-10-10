"""历史案例检索：把过去的"根因结论 + 异常链签名"建成索引，新会话遇到相同异常链时提示模型。

签名来自 log_overview 的异常链聚类（根因异常 + 首个业务栈帧），与模型无关、也不随行号变化：
- root：根因异常的简单类名（`NullPointerException`），
- frame：业务栈帧的 `文件名:函数名`（`OrderService.java:placeOrder`），不含行号，发版后仍能对上。
一份日志里常有好几条无关的异常链，所以每条签名还记下它是否被当时的结论 / 证据提到（cited），
只有被提到的签名才算"这个案例的根因签名"，匹配时权重更高。

只索引判定为 finding 的轮次；用户反馈"没用"的轮次不参与检索，"根因不对"且写了纠正的，提示里改用纠正内容。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from datetime import datetime
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_agent_cases (
    name TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    project TEXT NOT NULL DEFAULT '',
    signatures TEXT NOT NULL,
    conclusion TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    PRIMARY KEY (name, turn_number)
)
"""

MAX_HINTS = 3
_SCAN_LIMIT = 1000
_FRAME = re.compile(r"^(?P<func>.*?)\s*\((?P<file>[^():]+)(?::\d+)?\)\s*$")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(_SCHEMA)


def _simple(name: str) -> str:
    return re.split(r"[.$:]", name.strip())[-1] if name else ""


def _frame_key(frame: str) -> str:
    match = _FRAME.match(frame or "")
    if not match:
        return ""
    func = _simple(match["func"].replace("::", "."))
    file = match["file"].replace("\\", "/").rsplit("/", 1)[-1]
    return f"{file}:{func}" if func else file


def log_signatures(logs: list[str]) -> list[dict[str, str]]:
    """本次日志的异常链签名（复用 log_overview 的扫描缓存，同一份日志不会扫两遍）。"""
    from .tools import log_overview

    found: dict[tuple[str, str], dict[str, str]] = {}
    for path in logs:
        try:
            meta = getattr(log_overview(path), "meta", None) or {}
        except Exception:  # noqa: BLE001 — 检索是附加能力，日志读不了就当没有签名
            continue
        for chain in meta.get("top_chains") or []:
            root, frame = _simple(str(chain.get("root") or "")), _frame_key(str(chain.get("frame") or ""))
            if root:
                found.setdefault((root, frame), {"root": root, "frame": frame})
    return list(found.values())


def _analysis_text(payload: dict[str, Any]) -> tuple[str, set[str]]:
    analysis = payload.get("analysis") or {}
    texts = [analysis.get("conclusion") or "", payload.get("summary") or ""]
    sources: set[str] = set()
    for issue in analysis.get("issues") or []:
        texts += [issue.get("title") or "", issue.get("symptoms") or ""]
        texts += [f"{h.get('explanation', '')} {h.get('reasoning', '')}" for h in issue.get("root_cause_hypotheses") or []]
        for evidence in issue.get("evidence") or []:
            texts.append(evidence.get("excerpt") or "")
            sources.add(str(evidence.get("source") or "").replace("\\", "/").rsplit("/", 1)[-1])
    return "\n".join(texts), sources


def index_turn(conn: sqlite3.Connection, name: str, turn_number: int, payload: dict[str, Any]) -> bool:
    """登记一轮的案例；不是 finding、没有异常链签名时不登记。返回是否登记。"""
    analysis = payload.get("analysis") or {}
    if payload.get("status") != "ok" or analysis.get("assessment") != "finding":
        return False
    signatures = log_signatures(list(payload.get("logs") or []))
    if not signatures:
        return False
    text, sources = _analysis_text(payload)
    for sig in signatures:
        file = sig["frame"].split(":", 1)[0] if sig["frame"] else ""
        sig["cited"] = bool(sig["root"] in text or (file and (file in sources or file in text)))
    from .memory import project_key

    ensure_schema(conn)
    conn.execute(
        "INSERT INTO log_agent_cases (name, turn_number, project, signatures, conclusion, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(name, turn_number) DO UPDATE SET "
        "project = excluded.project, signatures = excluded.signatures, conclusion = excluded.conclusion, "
        "confidence = excluded.confidence, created_at = excluded.created_at",
        (name, turn_number, project_key(payload.get("code") or []) or "", json.dumps(signatures, ensure_ascii=False),
         str(analysis.get("conclusion") or payload.get("summary") or "")[:400], str(analysis.get("confidence") or ""),
         datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
    )
    return True


def _score(current: list[dict[str, str]], stored: list[dict[str, Any]]) -> tuple[int, list[str]]:
    score, matched = 0, []
    exact = {(s["root"], s["frame"]): s for s in stored}
    cited_roots = {s["root"] for s in stored if s.get("cited")}
    for sig in current:
        hit = exact.get((sig["root"], sig["frame"]))
        if hit is not None and sig["frame"]:
            score += 3 if hit.get("cited") else 1
            matched.append(f"{sig['root']} @ {sig['frame']}")
        elif sig["root"] in cited_roots:
            score += 1
            matched.append(sig["root"])
    return score, matched


def find_similar(conn: sqlite3.Connection, logs: list[str], code: list[str], *,
                 exclude: str | None = None, limit: int = MAX_HINTS) -> list[dict[str, Any]]:
    """与本次日志异常链签名相似的历史案例，按相似度、同项目、时间倒序排列。"""
    try:
        ensure_schema(conn)
        current = log_signatures(logs)
        if not current:
            return []
        try:
            rows = conn.execute(
                "SELECT c.name, c.turn_number, c.project, c.signatures, c.conclusion, c.confidence, c.created_at, "
                "COALESCE(s.title, ''), f.rating, f.comment FROM log_agent_cases c "
                "JOIN log_agent_sessions s ON s.name = c.name "
                "LEFT JOIN log_agent_feedback f ON f.name = c.name AND f.turn_number = c.turn_number "
                "ORDER BY c.created_at DESC LIMIT ?", (_SCAN_LIMIT,),
            ).fetchall()
        except sqlite3.OperationalError:  # 还没建会话表 / 反馈表的库
            return []
    except sqlite3.Error:
        return []
    from .memory import project_key

    project = project_key(code) or ""
    results = []
    for name, turn, case_project, raw, conclusion, confidence, created, title, rating, comment in rows:
        if name == exclude or rating == "down" or (rating == "wrong" and not (comment or "").strip()):
            continue
        try:
            stored = json.loads(raw)
        except ValueError:
            continue
        score, matched = _score(current, stored)
        if score < 3:
            continue
        same_project = bool(project) and case_project == project
        results.append({
            "session": name, "turn": turn, "title": title, "conclusion": conclusion, "confidence": confidence,
            "created_at": created, "matched": matched, "score": score + (1 if same_project else 0),
            "same_project": same_project,
            "correction": (comment or "").strip() if rating == "wrong" else "",
            "confirmed": rating == "up",
        })
    results.sort(key=lambda r: r["created_at"], reverse=True)
    results.sort(key=lambda r: (-r["score"], -int(r["confirmed"])))
    return results[:limit]


def first_turn_hint(conn: sqlite3.Connection | None, logs: list[str], code: list[str],
                    session: str | None) -> tuple[str, list[dict[str, Any]]]:
    """新会话首条消息要追加的历史案例提示，以及命中的案例（存进本轮 payload 供界面展示）。"""
    if conn is None or os.environ.get("LOG_AGENT_CASES", "on").strip().lower() in ("off", "false", "0", "no"):
        return "", []
    cases = find_similar(conn, logs, code, exclude=session)
    return hint_text(cases), cases


def _ago(created: str) -> str:
    try:
        days = (datetime.now() - datetime.strptime(created, "%Y-%m-%d %H:%M:%S")).days
    except ValueError:
        return created
    return "今天" if days <= 0 else f"{days} 天前"


_CONFIDENCE = {"high": "高", "medium": "中", "low": "低"}


def hint_text(cases: list[dict[str, Any]]) -> str:
    if not cases:
        return ""
    lines = ["", "## 历史相似案例（仅供参考）",
             "以下过去的排查与本次日志出现了相同的异常链签名。它们只是线索：必须用本次的日志和源码重新验证，"
             "不能直接照搬结论；如果本次证据指向不同根因，以本次证据为准，并在报告里说明与历史案例的异同。"]
    for case in cases:
        label = case["title"] or case["session"]
        lines.append(f"- {_ago(case['created_at'])} · 会话「{label}」第 {case['turn']} 轮"
                     f"（相同签名：{'、'.join(case['matched'][:3])}）")
        if case["correction"]:
            lines.append(f"  当时的结论被用户标记为根因不对，用户纠正：{case['correction']}")
        else:
            confidence = _CONFIDENCE.get(case["confidence"], case["confidence"])
            suffix = "；用户确认有用" if case["confirmed"] else ""
            lines.append(f"  当时结论：{case['conclusion']}（可信度：{confidence or '未知'}{suffix}）")
    return "\n".join(lines)
