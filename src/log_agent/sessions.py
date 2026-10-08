"""会话元数据：记录每个会话用了哪些日志/源码、问了几轮，支持列出与删除。

对话内容本身由 langgraph 的 SqliteSaver 存在同一个数据库文件里（按 thread_id 区分），
这里只加一张小表存"会话是什么"，不碰 checkpoint 表结构。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

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
    settings: dict[str, Any] = field(default_factory=dict)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _search_field(raw: str | None) -> str:
    """Decode the first element of SQLite's multi-path JSON array using history() semantics."""
    return str(json.loads(raw)[0] or "") if raw is not None else ""


class SessionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.create_function("unicode_casefold", 1, str.casefold, deterministic=True)
        self.conn.create_function("history_field", 1, _search_field, deterministic=True)
        with self.conn:
            self.conn.execute(_SCHEMA)
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS log_agent_session_settings "
                "(name TEXT PRIMARY KEY, settings TEXT NOT NULL)"
            )
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS log_agent_turns "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            self.conn.execute("CREATE INDEX IF NOT EXISTS log_agent_turns_name ON log_agent_turns(name, id)")
            columns = {r[1] for r in self.conn.execute("PRAGMA table_info(log_agent_turns)")}
            if "turn_number" not in columns:
                self.conn.execute("ALTER TABLE log_agent_turns ADD COLUMN turn_number INTEGER")
                # Older sessions may predate snapshots: keep those missing rounds as a gap.
                for name, total in self.conn.execute("SELECT name, turns FROM log_agent_sessions").fetchall():
                    ids = self.conn.execute("SELECT id FROM log_agent_turns WHERE name = ? ORDER BY id", (name,)).fetchall()
                    for number, (row_id,) in enumerate(ids, max(0, total - len(ids)) + 1):
                        self.conn.execute("UPDATE log_agent_turns SET turn_number = ? WHERE id = ?", (number, row_id))


    def get(self, name: str) -> SessionInfo | None:
        row = self.conn.execute(
            "SELECT name, logs, code, model, title, turns, total_tokens, created_at, updated_at "
            "FROM log_agent_sessions WHERE name = ?",
            (name,),
        ).fetchone()
        return self._with_settings(self._row(row)) if row else None

    def list(self) -> list[SessionInfo]:
        rows = self.conn.execute(
            "SELECT name, logs, code, model, title, turns, total_tokens, created_at, updated_at "
            "FROM log_agent_sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [self._with_settings(self._row(r)) for r in rows]

    def _with_settings(self, info: SessionInfo) -> SessionInfo:
        row = self.conn.execute("SELECT settings FROM log_agent_session_settings WHERE name = ?", (info.name,)).fetchone()
        if row:
            info.settings = json.loads(row[0])
        return info

    def latest(self) -> SessionInfo | None:
        sessions = self.list()
        return sessions[0] if sessions else None

    def reserve(self, name: str) -> None:
        """Atomically claim a new name; uniqueness conflicts belong to the caller."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO log_agent_sessions (name, created_at, updated_at) VALUES (?, ?, ?)",
                (name, _now(), _now()),
            )

    def release(self, name: str) -> None:
        """Release an unused reservation, without deleting registered sessions."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM log_agent_sessions WHERE name = ? AND turns = 0 "
                "AND logs = '[]' AND code = '[]' AND model = '' "
                "AND NOT EXISTS (SELECT 1 FROM log_agent_session_settings WHERE name = ?)",
                (name, name),
            )

    def touch(self, name: str, logs: list[str], code: list[str], model: str, settings: dict | None = None,
              *, reserved: bool = False) -> None:
        """登记会话（首次）或更新它当前使用的日志/源码/模型。"""
        now = _now()
        with self.conn:
            if reserved:
                updated = self.conn.execute(
                    "UPDATE log_agent_sessions SET logs = ?, code = ?, model = ?, updated_at = ? WHERE name = ?",
                    (json.dumps(logs, ensure_ascii=False), json.dumps(code, ensure_ascii=False), model, now, name),
                )
                if not updated.rowcount:
                    raise sqlite3.IntegrityError(f"Session reservation no longer exists: {name}")
            else:
                self.conn.execute(
                    "INSERT INTO log_agent_sessions (name, logs, code, model, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET logs = excluded.logs, code = excluded.code, "
                    "model = excluded.model, updated_at = excluded.updated_at",
                    (name, json.dumps(logs, ensure_ascii=False), json.dumps(code, ensure_ascii=False), model, now, now),
                )
            if settings is not None:
                previous = self.get(name)
                if previous and "origin" in previous.settings:
                    settings = {**settings, "origin": previous.settings["origin"]}
                self.conn.execute(
                    "INSERT INTO log_agent_session_settings (name, settings) VALUES (?, ?) "
                    "ON CONFLICT(name) DO UPDATE SET settings = excluded.settings",
                    (name, json.dumps(settings, ensure_ascii=False)),
                )

    def record_turn(self, name: str, question: str, tokens: int, payload: dict | None = None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE log_agent_sessions SET turns = turns + 1, total_tokens = total_tokens + ?, "
                "title = CASE WHEN title = '' THEN ? ELSE title END, updated_at = ? WHERE name = ?",
                (tokens, question[:80], _now(), name),
            )
            if payload is not None:
                self.conn.execute(
                    "INSERT INTO log_agent_turns (name, payload, turn_number) "
                    "SELECT name, ?, turns FROM log_agent_sessions WHERE name = ?",
                    (json.dumps(payload, ensure_ascii=False), name),
                )

    def history(self, name: str, search: str = "") -> list[tuple[int, dict]]:
        rows = self.conn.execute(
            "SELECT turn_number, payload FROM log_agent_turns WHERE name = ? ORDER BY turn_number", (name,),
        )
        result = []
        for number, raw in rows:
            payload = json.loads(raw)
            haystack = " ".join(str(payload.get(k) or "") for k in ("question", "summary", "report"))
            if not search or search.casefold() in haystack.casefold():
                result.append((number, payload))
        return result

    def turn(self, name: str, number: int) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM log_agent_turns WHERE name = ? AND turn_number = ?", (name, number),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def search(self, keyword: str) -> list[SessionInfo]:
        needle = keyword.casefold()
        # SQLite lower()/LIKE only fold ASCII. Keep Python's Unicode casefold semantics,
        # but extract and match inside SQLite and return names, never report payloads.
        # CASE guarantees malformed JSON never reaches extraction (unlike relying on
        # WHERE predicate evaluation order). Only individual search fields cross into Python.
        # Multi-path json_extract returns JSON text even for scalars, preserving booleans,
        # large integers and number spellings without SQLite SQL-value coercion. Repeating
        # the path keeps both entries local to this field and works before SQLite 3.38.
        fields = " || ' ' || ".join(
            f"CASE WHEN json_type(payload, '$.{key}') = 'text' "
            f"THEN json_extract(payload, '$.{key}') "
            f"ELSE history_field(json_extract(payload, '$.{key}', '$.{key}')) END"
            for key in ("question", "summary", "report")
        )
        matched = {row[0] for row in self.conn.execute(
            "SELECT DISTINCT name FROM log_agent_turns WHERE CASE WHEN json_valid(payload) THEN "
            f"instr(unicode_casefold({fields}), ?) > 0 ELSE 0 END", (needle,),
        )}
        return [item for item in self.list()
                if needle in " ".join([item.name, item.title, *item.logs]).casefold()
                or item.name in matched]

    def last_turn(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT payload FROM log_agent_turns WHERE name = ? ORDER BY id DESC LIMIT 1", (name,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def recent_turns(self, limit: int = 200, name: str | None = None) -> list[tuple[str, str, int, dict]]:
        """最近保存的轮次（新的在前）：(会话名, 会话标题, 轮次, 快照)，供 Web trace 列表使用。"""
        where, params = ("WHERE t.name = ?", [name]) if name else ("", [])
        rows = self.conn.execute(
            "SELECT t.name, COALESCE(s.title, ''), t.turn_number, t.payload FROM log_agent_turns t "
            f"JOIN log_agent_sessions s ON s.name = t.name {where} ORDER BY t.id DESC LIMIT ?",
            (*params, limit),
        )
        result = []
        for session, title, number, raw in rows:
            try:
                payload = json.loads(raw)
            except ValueError:
                continue
            if isinstance(payload, dict) and number is not None:
                result.append((session, title, number, payload))
        return result

    def delete(self, name: str) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM log_agent_sessions WHERE name = ?", (name,))
            deleted = cur.rowcount > 0
            for table in ("log_agent_session_settings", "log_agent_turns"):
                self.conn.execute(f"DELETE FROM {table} WHERE name = ?", (name,))
            # Web 分享链接随会话一起失效；从没开过 serve 的库里没有这张表
            try:
                self.conn.execute("DELETE FROM log_agent_shares WHERE name = ?", (name,))
            except sqlite3.OperationalError:
                pass
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
