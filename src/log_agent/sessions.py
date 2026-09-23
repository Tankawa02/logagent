"""会话元数据：记录每个会话用了哪些日志/源码、问了几轮，支持列出与删除。

对话内容本身由 langgraph 的 SqliteSaver 存在同一个数据库文件里（按 thread_id 区分），
这里只加一张小表存"会话是什么"，不碰 checkpoint 表结构。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_agent_sessions (
    name TEXT PRIMARY KEY,
    logs TEXT NOT NULL DEFAULT '[]',
    code TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    turns INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


def default_db_path() -> Path:
    return Path.home() / ".log-agent" / "sessions.db"


@dataclass
class SessionInfo:
    name: str
    logs: list[str]
    code: list[str]
    model: str
    title: str
    turns: int
    total_tokens: int
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SessionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        with self.conn:
            self.conn.execute(_SCHEMA)

    def get(self, name: str) -> SessionInfo | None:
        row = self.conn.execute(
            "SELECT name, logs, code, model, title, turns, total_tokens, created_at, updated_at "
            "FROM log_agent_sessions WHERE name = ?",
            (name,),
        ).fetchone()
        return self._row(row) if row else None

    def list(self) -> list[SessionInfo]:
        rows = self.conn.execute(
            "SELECT name, logs, code, model, title, turns, total_tokens, created_at, updated_at "
            "FROM log_agent_sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [self._row(r) for r in rows]

    def latest(self) -> SessionInfo | None:
        sessions = self.list()
        return sessions[0] if sessions else None

    def touch(self, name: str, logs: list[str], code: list[str], model: str) -> None:
        """登记会话（首次）或更新它当前使用的日志/源码/模型。"""
        now = _now()
        with self.conn:
            self.conn.execute(
                "INSERT INTO log_agent_sessions (name, logs, code, model, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET logs = excluded.logs, code = excluded.code, "
                "model = excluded.model, updated_at = excluded.updated_at",
                (name, json.dumps(logs, ensure_ascii=False), json.dumps(code, ensure_ascii=False), model, now, now),
            )

    def record_turn(self, name: str, question: str, tokens: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE log_agent_sessions SET turns = turns + 1, total_tokens = total_tokens + ?, "
                "title = CASE WHEN title = '' THEN ? ELSE title END, updated_at = ? WHERE name = ?",
                (tokens, question[:80], _now(), name),
            )

    def delete(self, name: str) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM log_agent_sessions WHERE name = ?", (name,))
            deleted = cur.rowcount > 0
            # 同时清掉 langgraph 存的对话内容；表不存在（从未对话过）时忽略
            for table in ("checkpoints", "writes"):
                try:
                    cur = self.conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (name,))  # noqa: S608
                    deleted = deleted or cur.rowcount > 0
                except sqlite3.OperationalError:
                    pass
        return deleted

    @staticmethod
    def _row(row: tuple) -> SessionInfo:
        name, logs, code, model, title, turns, tokens, created, updated = row
        return SessionInfo(
            name=name,
            logs=json.loads(logs or "[]"),
            code=json.loads(code or "[]"),
            model=model,
            title=title,
            turns=int(turns),
            total_tokens=int(tokens),
            created_at=created,
            updated_at=updated,
        )


def describe_source_change(old: SessionInfo, logs: list[str], code: list[str]) -> str:
    """续会话时如果日志/源码变了，生成一段说明发给模型，避免它继续引用旧路径。"""
    lines: list[str] = []
    if old.logs != logs:
        lines.append("注意：本次续会话使用的日志文件与之前不同，请以下列路径为准：")
        lines.extend(f"  - {p}" for p in logs)
    if old.code != code:
        if code:
            lines.append("源码目录已更新为：")
            lines.extend(f"  - {p}" for p in code)
        else:
            lines.append("本次未提供源码目录。")
    return "\n".join(lines)
