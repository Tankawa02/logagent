"""一次交互运行的共享状态；命令和提问循环通过同一对象读写会话。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from rich.text import Text

from .citations import CitationLinker
from .render import TurnResult, format_duration
from .term import glyphs

if TYPE_CHECKING:
    from .memory import MemorySession
    from .sessions import SessionStore
    from .skills import SkillSource
    from .timefilter import TimeWindow


class ChatTotals:
    def __init__(self) -> None:
        self.turns = 0
        self.elapsed = 0.0
        self.tokens = 0
        self.tools = 0

    def add(self, result: TurnResult) -> None:
        self.turns += 1
        self.elapsed += result.elapsed
        self.tokens += result.usage.get("total", 0)
        self.tools += len(result.tools)

    def line(self) -> Text:
        sep = f" {glyphs.sep} "
        return Text(
            f"本次共 {self.turns} 轮{sep}用时 {format_duration(self.elapsed)}{sep}"
            f"工具 {self.tools} 次{sep}{self.tokens:,} tokens",
            style="muted",
        )


def new_session_name() -> str:
    return "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")


@dataclass
class ChatSession:
    store: SessionStore
    session: str
    log_paths: list[str]
    code_paths: list[str]
    model: str
    settings: dict[str, Any]
    linker: CitationLinker
    mem: MemorySession | None
    base_url: str | None = None
    skill_sources: list[SkillSource] = field(default_factory=list)
    no_redact: bool = False
    baseline_window: TimeWindow | None = None
    first_turn: bool = True
    source_note: str = ""
    suggestions: list[str] = field(default_factory=list)
    last: dict[str, Any] | None = None
    last_question: str | None = None
    totals: ChatTotals = field(default_factory=ChatTotals)

    def persist(self) -> None:
        self.store.touch(self.session, self.log_paths, self.code_paths, self.model, self.settings)

    def refresh_sources(self) -> None:
        self.linker = CitationLinker(self.log_paths, self.code_paths)
        self.suggestions = []
        self.persist()
