"""会话分享链接：给同事一个只读链接，打开就能看报告、时间线和证据原文。

和会话存在同一个 SQLite 文件里。库里只存 token 的 SHA-256，数据库文件泄露也拿不到可用链接；
链接只在创建时完整返回一次，之后列表里只显示末尾几位，方便辨认和撤销。
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

_SCHEMA = """
CREATE TABLE IF NOT EXISTS log_agent_shares (
    id TEXT PRIMARY KEY,
    token_hash TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    hint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT
)
"""

_FMT = "%Y-%m-%d %H:%M:%S"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


@dataclass
class Share:
    id: str
    name: str
    hint: str
    created_at: str
    expires_at: str | None

    def to_dict(self) -> dict:
        return asdict(self)


class ShareStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        with self.conn:
            self.conn.execute(_SCHEMA)
            self.conn.execute("CREATE INDEX IF NOT EXISTS log_agent_shares_name ON log_agent_shares(name)")

    def create(self, name: str, ttl_hours: float | None) -> tuple[Share, str]:
        token = secrets.token_urlsafe(24)
        created = _now()
        expires = (created + timedelta(hours=ttl_hours)).strftime(_FMT) if ttl_hours else None
        share = Share(secrets.token_hex(6), name, token[-4:], created.strftime(_FMT), expires)
        with self.conn:
            self.conn.execute(
                "INSERT INTO log_agent_shares (id, token_hash, name, hint, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (share.id, _hash(token), name, share.hint, share.created_at, share.expires_at),
            )
        return share, token

    def resolve(self, token: str) -> str | None:
        """token 有效（存在且未过期）时返回会话名。"""
        if not token or len(token) > 128:
            return None
        row = self.conn.execute(
            "SELECT name, expires_at FROM log_agent_shares WHERE token_hash = ?", (_hash(token),),
        ).fetchone()
        if row is None:
            return None
        name, expires = row
        if expires and datetime.strptime(expires, _FMT) <= _now():
            return None
        return name

    def list(self, name: str) -> list[Share]:
        rows = self.conn.execute(
            "SELECT id, name, hint, created_at, expires_at FROM log_agent_shares WHERE name = ? "
            "ORDER BY created_at DESC", (name,),
        ).fetchall()
        return [Share(*row) for row in rows]

    def revoke(self, name: str, share_id: str) -> bool:
        with self.conn:
            cur = self.conn.execute("DELETE FROM log_agent_shares WHERE id = ? AND name = ?", (share_id, name))
        return cur.rowcount > 0
