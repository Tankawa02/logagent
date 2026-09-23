"""长期记忆：跨会话记住用户的表达偏好、术语和项目事实。

设计要点（阶段一）：
- **只由代码写入**：模型只能通过 `suggest_memory` 提出候选，候选要经用户确认才会变成记忆；
  用户说"记住……"或用 `/remember` 时直接保存。模型拿不到任何能改记忆库的工具。
- **候选先攒着，信号够强才问**：用户明确要求记住、纠正了 agent，或同一条内容在两个不同会话里出现过，
  才请用户确认；只出现过一次的候选 30 天后自动过期。被拒绝的内容进入冷却（或永久屏蔽），不会反复提。
- **读取不进 checkpoint**：每次模型调用前把当前范围内的记忆追加到系统提示词末尾，
  删除 / 修改下一轮就生效；同一轮内容不变，不打断 prompt caching。
- **范围**：表达偏好默认全局；术语、项目事实挂在项目上（第一个源码目录所在的 git 仓库根）。
- 记忆库 `~/.log-agent/memory.db` 与会话库分开，出错时降级为不使用记忆，不影响排查本身。
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from . import redact

MODES = ("suggest", "explicit", "off")
DEFAULT_MODE = "suggest"

KIND_LABELS = {"preference": "表达偏好", "term": "术语", "fact": "项目事实"}
Kind = Literal["preference", "term", "fact"]
Signal = Literal["explicit", "correction", "mention"]
_SIGNAL_RANK = {"mention": 0, "correction": 1, "explicit": 2}

MAX_TEXT = 300
MAX_SUGGESTIONS_PER_TURN = 2
PROMPT_BUDGET = 3000
CANDIDATE_TTL = timedelta(days=30)
MAX_CANDIDATES_PER_SCOPE = 50
REJECT_COOLDOWN = timedelta(days=90)
FEATURE_HINT_AFTER_SESSIONS = 3
_SIMILARITY = 0.7

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    project TEXT,
    text TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    project TEXT,
    text TEXT NOT NULL,
    signal TEXT NOT NULL,
    occurrences INTEGER NOT NULL DEFAULT 1,
    sessions TEXT NOT NULL DEFAULT '[]',
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_rejections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT,
    text TEXT NOT NULL,
    until TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# 用户明确要求记住的说法：命中时本轮候选直接升级为 explicit，模型没提候选时用用户原话兜底
_EXPLICIT_TRIGGER = re.compile(r"记住|记一下|记下来|帮我记|以后都|以后请|以后回答|以后报告|今后都|下次都")
_TRIGGER_PREFIX = re.compile(r"^\s*(?:请|麻烦)?\s*(?:帮我)?\s*(?:记住|记一下|记下来|记下)\s*[:：,，、]?\s*")
_PREFERENCE_HINT = re.compile(r"报告|回答|回复|输出|先写|格式|篇幅|简洁|详细|中文|英文|markdown|表格|结论", re.I)


def default_memory_path() -> Path:
    return Path.home() / ".log-agent" / "memory.db"


def project_key(code_paths: Iterable[str]) -> str | None:
    """项目 = 第一个源码目录所在的 git 仓库根；不是 git 仓库时就用该目录本身。没给源码时为 None（只用全局记忆）。"""
    for raw in code_paths:
        start = Path(raw).expanduser().resolve()
        for folder in (start, *start.parents):
            if (folder / ".git").exists():
                return str(folder)
        return str(start)
    return None


def project_label(project: str | None) -> str:
    return "全局" if project is None else (Path(project).name or project)


def guess_kind(text: str) -> Kind:
    return "preference" if _PREFERENCE_HINT.search(text) else "fact"


def default_scope(kind: str, project: str | None) -> str | None:
    """表达偏好默认全局；术语、事实挂在项目上（没有项目时只能全局）。"""
    return None if kind == "preference" else project


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def _ts(value: datetime) -> str:
    return value.isoformat(sep=" ")


def _normalize(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.lower())


def _bigrams(text: str) -> set[str]:
    norm = _normalize(text)
    if len(norm) < 2:
        return {norm} if norm else set()
    return {norm[i : i + 2] for i in range(len(norm) - 1)}


def similar(a: str, b: str) -> bool:
    """字符二元组 Dice 系数：只用于判断"是不是同一条"，判错的代价是多问或少问一次。"""
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return False
    return 2 * len(left & right) / (len(left) + len(right)) >= _SIMILARITY


def clean_text(text: str) -> str:
    """存储前统一处理：压缩空白、截断、脱敏（与工具输出同一套规则，跟随 --no-redact 开关）。"""
    text = " ".join(str(text).split())[:MAX_TEXT]
    return redact.redact_log(text)


@dataclass
class Memory:
    id: int
    kind: str
    project: str | None
    text: str
    origin: str
    created_at: str
    updated_at: str

    @property
    def scope_label(self) -> str:
        return project_label(self.project)


@dataclass
class Candidate:
    id: int
    kind: str
    project: str | None
    text: str
    signal: str
    occurrences: int
    sessions: list[str]
    first_seen: str
    last_seen: str

    @property
    def immediate(self) -> bool:
        """用户明确要求或纠正：本轮结束就确认；重复出现的候选攒到会话结束一起问。"""
        return self.signal in ("explicit", "correction")

    @property
    def due(self) -> bool:
        return self.immediate or len(self.sessions) >= 2

    @property
    def reason(self) -> str:
        if self.signal == "explicit":
            return "你让我记住的"
        if self.signal == "correction":
            return "来自你的纠正"
        return f"在 {len(self.sessions)} 个会话里提到过"


class MemoryStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # 渲染器在后台线程里跑 agent，中间件会在那里读记忆；写入只发生在两轮之间的主线程，不会并发
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        with self.conn:
            self.conn.executescript(_SCHEMA)
        self._purge()

    def close(self) -> None:
        self.conn.close()

    # ---- 记忆 ---------------------------------------------------------------

    def memories(self, project: str | None = None, *, all_projects: bool = False) -> list[Memory]:
        """当前范围可见的记忆：全局 + 本项目（all_projects=True 时列出全部）。按创建顺序，保证每轮注入内容稳定。"""
        sql = "SELECT id, kind, project, text, origin, created_at, updated_at FROM memories"
        params: tuple = ()
        if not all_projects:
            sql += " WHERE project IS NULL OR project = ?"
            params = (project,)
        rows = self.conn.execute(sql + " ORDER BY id", params).fetchall()
        return [Memory(*row) for row in rows]

    def get(self, memory_id: int) -> Memory | None:
        row = self.conn.execute(
            "SELECT id, kind, project, text, origin, created_at, updated_at FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        return Memory(*row) if row else None

    def add(self, text: str, kind: str, project: str | None, origin: str = "explicit") -> tuple[Memory, bool]:
        """保存一条记忆，返回 (记忆, 是否更新了已有的相似条目)。

        同范围内已有相似条目时直接改写它（视为纠正），避免同一件事存两条互相矛盾的版本。
        """
        text = clean_text(text)
        now = _ts(_now())
        for existing in self._same_scope(project):
            if existing.kind == kind and similar(existing.text, text):
                with self.conn:
                    self.conn.execute(
                        "UPDATE memories SET text = ?, updated_at = ? WHERE id = ?", (text, now, existing.id)
                    )
                self._drop_similar_candidates(project, text)
                return self.get(existing.id), True  # type: ignore[return-value]
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO memories (kind, project, text, origin, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (kind, project, text, origin, now, now),
            )
        self._drop_similar_candidates(project, text)
        return self.get(int(cur.lastrowid)), False  # type: ignore[return-value]

    def update(self, memory_id: int, text: str) -> Memory | None:
        with self.conn:
            cur = self.conn.execute(
                "UPDATE memories SET text = ?, updated_at = ? WHERE id = ?", (clean_text(text), _ts(_now()), memory_id)
            )
        return self.get(memory_id) if cur.rowcount else None

    def remove(self, memory_id: int) -> Memory | None:
        memory = self.get(memory_id)
        if memory is None:
            return None
        with self.conn:
            self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        # 删掉的内容不该马上又被当成候选提出来
        self._reject(memory.project, memory.text, permanent=False)
        return memory

    def _same_scope(self, project: str | None) -> list[Memory]:
        return [m for m in self.memories(project) if m.project == project]

    # ---- 候选 ---------------------------------------------------------------

    def record_candidate(
        self, text: str, kind: str, project: str | None, signal: str, session: str
    ) -> Candidate | None:
        """登记一次候选出现；与已有记忆重复或已被拒绝时返回 None。"""
        text = clean_text(text)
        if not _normalize(text):
            return None
        if any(similar(m.text, text) for m in self.memories(project)):
            return None
        if self._is_rejected(project, text):
            return None
        now = _ts(_now())
        for candidate in self._candidates(project):
            if candidate.project != project or not similar(candidate.text, text):
                continue
            sessions = candidate.sessions if session in candidate.sessions else [*candidate.sessions, session][-10:]
            strongest = max(candidate.signal, signal, key=lambda s: _SIGNAL_RANK.get(s, 0))
            # 更强的信号（明确要求 / 纠正）带来的措辞以用户最新说法为准
            new_text = text if _SIGNAL_RANK.get(signal, 0) >= _SIGNAL_RANK.get(candidate.signal, 0) else candidate.text
            with self.conn:
                self.conn.execute(
                    "UPDATE memory_candidates SET text = ?, kind = ?, signal = ?, occurrences = occurrences + 1, "
                    "sessions = ?, last_seen = ? WHERE id = ?",
                    (new_text, kind, strongest, json.dumps(sessions), now, candidate.id),
                )
            return self._candidate(candidate.id)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO memory_candidates (kind, project, text, signal, sessions, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, project, text, signal, json.dumps([session]), now, now),
            )
        self._cap_candidates(project)
        return self._candidate(int(cur.lastrowid))

    def pending(self, project: str | None = None, *, all_projects: bool = False) -> list[Candidate]:
        """已经达到确认条件、等用户处理的候选。"""
        return [c for c in self._candidates(project, all_projects=all_projects) if c.due]

    def accept(self, candidate: Candidate, text: str | None = None) -> Memory:
        with self.conn:
            self.conn.execute("DELETE FROM memory_candidates WHERE id = ?", (candidate.id,))
        memory, _ = self.add(text or candidate.text, candidate.kind, candidate.project, origin="suggested")
        return memory

    def reject(self, candidate: Candidate, *, permanent: bool) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM memory_candidates WHERE id = ?", (candidate.id,))
        self._reject(candidate.project, candidate.text, permanent=permanent)

    def _candidates(self, project: str | None, *, all_projects: bool = False) -> list[Candidate]:
        sql = (
            "SELECT id, kind, project, text, signal, occurrences, sessions, first_seen, last_seen FROM memory_candidates"
        )
        params: tuple = ()
        if not all_projects:
            sql += " WHERE project IS NULL OR project = ?"
            params = (project,)
        return [self._candidate_row(r) for r in self.conn.execute(sql + " ORDER BY id", params).fetchall()]

    def _candidate(self, candidate_id: int) -> Candidate | None:
        row = self.conn.execute(
            "SELECT id, kind, project, text, signal, occurrences, sessions, first_seen, last_seen "
            "FROM memory_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return self._candidate_row(row) if row else None

    @staticmethod
    def _candidate_row(row: tuple) -> Candidate:
        cid, kind, project, text, signal, occurrences, sessions, first_seen, last_seen = row
        return Candidate(cid, kind, project, text, signal, int(occurrences), json.loads(sessions or "[]"), first_seen, last_seen)

    def _drop_similar_candidates(self, project: str | None, text: str) -> None:
        stale = [c.id for c in self._candidates(project) if similar(c.text, text)]
        if stale:
            with self.conn:
                self.conn.executemany("DELETE FROM memory_candidates WHERE id = ?", [(i,) for i in stale])

    def _cap_candidates(self, project: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "DELETE FROM memory_candidates WHERE project IS ? AND id NOT IN ("
                "SELECT id FROM memory_candidates WHERE project IS ? ORDER BY last_seen DESC, id DESC LIMIT ?)",
                (project, project, MAX_CANDIDATES_PER_SCOPE),
            )

    # ---- 拒绝 / 过期 --------------------------------------------------------

    def _reject(self, project: str | None, text: str, *, permanent: bool) -> None:
        now = _now()
        until = None if permanent else _ts(now + REJECT_COOLDOWN)
        with self.conn:
            self.conn.execute(
                "INSERT INTO memory_rejections (project, text, until, created_at) VALUES (?, ?, ?, ?)",
                (project, text, until, _ts(now)),
            )

    def _is_rejected(self, project: str | None, text: str) -> bool:
        rows = self.conn.execute(
            "SELECT text FROM memory_rejections WHERE (project IS NULL OR project IS ?) AND (until IS NULL OR until > ?)",
            (project, _ts(_now())),
        ).fetchall()
        return any(similar(row[0], text) for row in rows)

    def _purge(self) -> None:
        now = _now()
        with self.conn:
            # 只出现过一次、没有强信号的候选到期作废；冷却期结束的拒绝记录也清掉
            self.conn.execute(
                "DELETE FROM memory_candidates WHERE last_seen < ? AND signal = 'mention'", (_ts(now - CANDIDATE_TTL),)
            )
            self.conn.execute("DELETE FROM memory_rejections WHERE until IS NOT NULL AND until <= ?", (_ts(now),))

    # ---- 元数据 -------------------------------------------------------------

    def meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM memory_meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO memory_meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )


# ---------------------------------------------------------------------------
# 一次 CLI 运行的记忆上下文
# ---------------------------------------------------------------------------


@dataclass
class _Suggestion:
    text: str
    kind: str
    signal: str


@dataclass
class MemorySession:
    """把记忆库、当前项目、当前会话和本轮收到的候选放在一起，供工具、中间件和 CLI 共用。"""

    store: MemoryStore
    mode: str
    project: str | None
    session: str
    asked: set[int] = field(default_factory=set)
    _turn: list[_Suggestion] = field(default_factory=list)

    @property
    def suggests(self) -> bool:
        return self.mode == "suggest"

    def memories(self) -> list[Memory]:
        return self.store.memories(self.project)

    # ---- 本轮候选 -----------------------------------------------------------

    def suggest(self, text: str, kind: str, signal: str) -> str:
        if kind not in KIND_LABELS:
            kind = guess_kind(text)
        if signal not in _SIGNAL_RANK:
            signal = "mention"
        if len(self._turn) >= MAX_SUGGESTIONS_PER_TURN:
            return "本轮已经提了足够的候选，不再记录。继续完成排查，不要在报告里提及。"
        self._turn.append(_Suggestion(text=text, kind=kind, signal=signal))
        return "已记下候选，本轮结束后由用户决定是否保存。继续完成排查，不要在报告里提及。"

    def finish_turn(self, user_input: str) -> list[Candidate]:
        """本轮结束：登记候选，返回需要马上请用户确认的（明确要求 / 纠正）。"""
        suggestions, self._turn = self._turn, []
        if self.mode == "off":
            return []
        explicit = bool(_EXPLICIT_TRIGGER.search(user_input))
        if explicit:
            for item in suggestions:
                item.signal = "explicit"
            if not suggestions:
                fallback = _TRIGGER_PREFIX.sub("", user_input).strip()
                if fallback and len(fallback) <= MAX_TEXT:
                    suggestions = [_Suggestion(text=fallback, kind=guess_kind(fallback), signal="explicit")]
        immediate: list[Candidate] = []
        for item in suggestions:
            candidate = self.store.record_candidate(
                item.text, item.kind, default_scope(item.kind, self.project), item.signal, self.session
            )
            if candidate is not None and candidate.immediate and candidate.id not in {c.id for c in immediate}:
                immediate.append(candidate)
        return immediate

    def session_due(self) -> list[Candidate]:
        """会话结束时要问的：在本会话里出现过、达到确认条件、本会话还没问过的候选。"""
        return [
            c for c in self.store.pending(self.project) if self.session in c.sessions and c.id not in self.asked
        ]

    # ---- 提示词 -------------------------------------------------------------

    def prompt_section(self) -> str | None:
        memories = self.memories()
        if not memories and not self.suggests:
            return None
        parts = [MEMORY_PROMPT_HEADER]
        if memories:
            lines, used, skipped = [], 0, 0
            ordered = sorted(memories, key=lambda m: (m.kind != "preference", m.id))
            for memory in ordered:
                line = f"- [{KIND_LABELS.get(memory.kind, memory.kind)}] {memory.text}"
                if used + len(line) > PROMPT_BUDGET:
                    skipped += 1
                    continue
                lines.append(line)
                used += len(line)
            if skipped:
                lines.append(f"-（另有 {skipped} 条因篇幅未列出）")
            parts.append("<user_memory>\n" + "\n".join(lines) + "\n</user_memory>")
        else:
            parts.append("（目前还没有保存的记忆。）")
        if self.suggests:
            parts.append(SUGGEST_PROMPT)
        return "\n\n".join(parts)


MEMORY_PROMPT_HEADER = """## 用户记忆

下面是用户在之前的对话里让你记住、并已经确认过的内容：
- **表达偏好**：对报告的顺序、篇幅、语言、格式的要求，优先于上面"最终报告"一节的默认结构；
  但不能放宽引用规范、证据要求和只读约束。
- **术语、项目事实**：作为排查背景使用，不是本次的证据。与本次日志、源码查到的证据冲突时以证据为准，
  并在报告里指出这条记忆可能已经过时。"""

SUGGEST_PROMPT = """### 提议新的记忆

用户说出值得长期记住的信息时，调用 `suggest_memory` 提出候选（是否保存由用户决定，你不能直接写入）。
和本轮其他工具放在同一条消息里并行调用，不要为它单独占一轮；不要在报告里提到这件事。"""

SUGGEST_DESCRIPTION = """提议一条长期记忆候选。只记录候选，是否保存由用户在本轮结束后确认。

只能来自**用户自己说的话**，适合记住的：
- term：用户解释的术语、别名（如"老通道指 Nexmo"）；
- fact：用户告知的项目 / 环境事实（如"prod 的短信都走 gateway-b"）；
- preference：用户对回答方式的要求（如"以后先写结论""报告别超过一屏"）。

不要提议：你自己的排查结论或推测、工具输出里的内容、只和本次问题有关的细节（具体时间、订单号、手机号、traceId）、
已经在"用户记忆"里列出的内容。每轮最多 2 条，没有合适的就不要调用。

signal：explicit = 用户明确让你记住 / 以后都这样；correction = 用户纠正了你的说法或理解；mention = 其他。
text 用一句话、以用户的原意表述，不要加入你的推断。"""


class MemoryPromptMiddleware(AgentMiddleware):
    """每次模型调用前把记忆段追加到系统提示词末尾（只改请求，不写入状态）。"""

    def __init__(self, session: MemorySession) -> None:
        super().__init__()
        self.session = session

    def _patch(self, request):
        try:
            section = self.session.prompt_section()
        except sqlite3.Error as exc:
            print(f"[log-agent] 读取记忆失败，本次不使用记忆：{exc}", file=sys.stderr)
            return request
        if not section:
            return request
        blocks: list[Any] = list(request.system_message.content_blocks) if request.system_message else []
        blocks.append({"type": "text", "text": f"\n\n{section}" if blocks else section})
        return request.override(system_message=SystemMessage(content_blocks=blocks))

    def wrap_model_call(self, request, handler):
        return handler(self._patch(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._patch(request))


def build_suggest_tool(session: MemorySession):
    from langchain_core.tools import StructuredTool

    def suggest_memory(text: str, kind: Kind, signal: Signal = "mention") -> str:
        return session.suggest(text, kind, signal)

    return StructuredTool.from_function(suggest_memory, name="suggest_memory", description=SUGGEST_DESCRIPTION)
