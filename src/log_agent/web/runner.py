"""在 Web 里续问一轮：复用 CLI 的 StreamRenderer，把过程翻译成 AG-UI 事件流推给浏览器。

为什么继承 StreamRenderer 而不是另写一套：
旁白 / 报告正文的判定、子代理工具统计、预算收尾、中断与接口错误处理、结构化报告解析和证据核对
都在 StreamRenderer.run 里，Web 和终端必须得出同一份 TurnResult，存进会话后两边看到的才一致。
这里只挂几个钩子：
- 模型流式输出 -> CUSTOM `log_agent.draft`（实时预览，旁白也在这里出现）
- 工具开始 / 结束 -> CUSTOM `log_agent.tool_start` / `log_agent.tool_end`
- 报告正文落定（_flush_answer）-> TEXT_MESSAGE_*（前端 useChat 的助手消息）
- 本轮存档 -> CUSTOM `log_agent.turn`
工具调用故意不用 TOOL_CALL_* 事件：那会让 TanStack AI 认为是待客户端续接的工具调用并自动再发一次请求，
而这些工具在服务端已经执行完了。

终端渲染（rich Live）照常运行，但期间把共享 console 设成 quiet，不往 serve 的终端里刷屏。
analyze / chat 依赖的全局设置（时区、时间窗口、编码、脱敏）按会话设置应用，所以同一进程同时只跑一轮。
"""

from __future__ import annotations

import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..render import StreamRenderer
from ..sessions import SessionInfo, SessionStore

Emit = Callable[[dict[str, Any]], None]
AgentFactory = Callable[..., Any]

# 同一进程同时只允许一轮分析：工具依赖进程级的时区 / 时间窗口 / 编码设置
TURN_LOCK = threading.Lock()


class TurnCancelled(KeyboardInterrupt):
    """用户点了停止：沿用 Ctrl+C 的中断路径，已输出的部分照常保存。"""


# 跑完的轮次再保留一会儿：浏览器刚好在结束前后切回来时，仍能重放完整过程而不是 404
LIVE_RUN_GRACE_SECONDS = 120


class LiveRun:
    """一轮正在进行（或刚结束）的分析：和浏览器连接解耦。

    分析线程把每个 AG-UI 事件追加到 events 并推给当前所有订阅者。浏览器切到别的会话、
    刷新页面或断网都只是退订，分析照常跑完并存档；回来时 subscribe 先拿到已有事件重放，
    再接着收后续事件。只有显式的停止请求（cancelled）才会中断本轮。
    """

    def __init__(self, session: str, question: str, run_id: str) -> None:
        self.session = session
        self.question = question
        self.run_id = run_id
        self.started = time.time()
        self.finished_at: float | None = None
        self.cancelled = threading.Event()
        self.events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._subscribers: list[tuple[Any, Any]] = []

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self.events.append(event)
            subscribers = list(self._subscribers)
        self._push(subscribers, event)

    def finish(self) -> None:
        with self._lock:
            self.finished_at = time.time()
            subscribers, self._subscribers = self._subscribers, []
        self._push(subscribers, None)

    def subscribe(self, loop: Any, queue: Any) -> list[dict[str, Any]]:
        """返回到目前为止的全部事件；若还没结束，之后的事件推到 queue（结束时推 None）。"""
        with self._lock:
            snapshot = list(self.events)
            if self.done:
                queue.put_nowait(None)
            else:
                self._subscribers.append((loop, queue))
        return snapshot

    def unsubscribe(self, queue: Any) -> None:
        with self._lock:
            self._subscribers = [(lp, q) for lp, q in self._subscribers if q is not queue]

    @staticmethod
    def _push(subscribers: list[tuple[Any, Any]], event: dict[str, Any] | None) -> None:
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # 订阅者所在的事件循环已关闭
                pass

    def describe(self) -> dict[str, Any]:
        return {"active": not self.done, "question": self.question, "run_id": self.run_id,
                "started": self.started, "finished_at": self.finished_at}


_LIVE_LOCK = threading.Lock()
_LIVE_RUNS: dict[str, LiveRun] = {}


def register_live_run(run: LiveRun) -> None:
    with _LIVE_LOCK:
        _LIVE_RUNS[run.session] = run


def get_live_run(session: str) -> LiveRun | None:
    with _LIVE_LOCK:
        now = time.time()
        for name in [n for n, r in _LIVE_RUNS.items() if r.done and now - (r.finished_at or now) > LIVE_RUN_GRACE_SECONDS]:
            del _LIVE_RUNS[name]
        return _LIVE_RUNS.get(session)


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "timestamp": int(time.time() * 1000), **fields}


def custom(name: str, value: Any) -> dict[str, Any]:
    return _event("CUSTOM", name=name, value=value)


class _TextTap:
    """包住 message_stream：把每个增量转发给浏览器，并在这里响应取消。"""

    def __init__(self, inner: Any, renderer: WebStreamRenderer) -> None:
        self._inner = inner
        self._renderer = renderer

    @property
    def text(self):
        renderer = self._renderer
        renderer.emit(custom("log_agent.draft", {"reset": True}))
        for delta in self._inner.text:
            renderer.check_cancelled()
            if delta:
                renderer.emit(custom("log_agent.draft", {"delta": delta}))
            yield delta

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class WebStreamRenderer(StreamRenderer):
    def __init__(self, emit: Emit, cancelled: threading.Event, *, linker=None, budget=None,
                 redact_owner: bool = True) -> None:
        super().__init__(verbose=False, linker=linker, budget=budget)
        from .app import _redacted_copy

        self.emit = lambda event: emit(_redacted_copy(event, redact_owner))
        self.cancelled = cancelled
        self.message_id = f"msg-{uuid.uuid4().hex[:12]}"
        self.message_open = False

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise TurnCancelled()

    def _on_message(self, message_stream: Any) -> None:
        self.check_cancelled()
        super()._on_message(_TextTap(message_stream, self))
        self.emit(custom("log_agent.draft", {"reset": True}))

    def _on_tool(self, tool_stream: Any) -> None:
        self.check_cancelled()
        before = self.tool_count
        super()._on_tool(tool_stream)
        if self.tool_count > before:
            run = self.running[-1]
            self.emit(custom("log_agent.tool_start", {
                "id": run.call_id, "name": run.name, "label": run.label, "args": run.args, "note": run.note,
            }))

    def _record(self, run, subagent: str = "") -> None:
        super()._record(run, subagent)
        record = asdict(self.records[-1])
        self.emit(custom("log_agent.tool_end", {"id": run.call_id, "label": run.label, **record}))

    def _flush_answer(self, text: str) -> None:
        before = len(self.answer_parts)
        super()._flush_answer(text)
        if len(self.answer_parts) == before:
            return
        if not self.message_open:
            self.emit(_event("TEXT_MESSAGE_START", messageId=self.message_id, role="assistant"))
            self.message_open = True
        separator = "\n\n" if before else ""
        self.emit(_event("TEXT_MESSAGE_CONTENT", messageId=self.message_id, delta=separator + text))

    def close_message(self) -> None:
        if self.message_open:
            self.emit(_event("TEXT_MESSAGE_END", messageId=self.message_id))
            self.message_open = False


def _apply_session_settings(settings: dict[str, Any], no_redact: bool) -> None:
    from .. import redact
    from ..logfile import set_forced_encoding
    from ..timefilter import set_default_timezone, set_default_window

    set_default_timezone(settings.get("timezone") or "UTC")
    set_forced_encoding(settings.get("encoding") or None)
    set_default_window(settings.get("since") or None, settings.get("until") or None)
    redact.set_enabled(not no_redact)


def session_no_redact(store: SessionStore, name: str) -> bool:
    """会话最初是否关闭了脱敏：chat 把它记在每轮快照的 settings 里，续问时沿用。"""
    last = store.last_turn(name) or {}
    info = store.get(name)
    return bool((info.settings if info else {}).get("no_redact", (last.get("settings") or {}).get("no_redact")))


def run_turn(
    *,
    db_path: Path,
    info: SessionInfo,
    question: str,
    emit: Emit,
    cancelled: threading.Event,
    agent_factory: AgentFactory,
    base_url: str | None,
    thread_id: str,
    run_id: str,
    redact_owner: bool = True,
) -> dict[str, Any] | None:
    """执行一轮续问并存进会话；返回本轮报告快照。调用方负责持有 TURN_LOCK。"""
    from langgraph.checkpoint.sqlite import SqliteSaver

    from .. import logfile, redact, timefilter
    from ..budget import TokenBudget, parse_budget
    from ..citations import CitationLinker
    from ..cli_context import _build_context_message, _run_config
    from ..compare import parse_range
    from ..export import build_payload
    from ..term import console
    from .app import _redacted_copy

    settings = dict(info.settings)
    original_emit = emit

    def emit(event):
        original_emit(_redacted_copy(event, redact_owner))

    saved_globals = (logfile._forced_encoding, timefilter._default_timezone,
                     timefilter._default_window, redact.is_enabled())
    emit(_event("RUN_STARTED", threadId=thread_id, runId=run_id))
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    quiet = console.quiet
    try:
        store = SessionStore(conn)
        no_redact = session_no_redact(store, info.name)
        _apply_session_settings(settings, no_redact)
        baseline = parse_range(settings["baseline"]) if settings.get("baseline") else None
        budget = TokenBudget(parse_budget(str(settings["budget"]))) if settings.get("budget") else None
        max_steps = int(settings.get("max_steps") or 120)
        config = _run_config(max_steps, info.name)

        checkpointer = SqliteSaver(conn)
        agent = agent_factory(model=info.model, checkpointer=checkpointer,
                              base_url=settings.get("base_url") or base_url, budget=budget)
        try:
            first_turn = checkpointer.get(config) is None
        except Exception:
            first_turn = True
        message = (_build_context_message(info.logs, info.code, question, baseline) if first_turn else question)

        renderer = WebStreamRenderer(
            original_emit, cancelled, linker=CitationLinker(info.logs, info.code, mode="off"), budget=budget,
            redact_owner=redact_owner,
        )
        console.quiet = True
        try:
            result = renderer.run(agent, {"messages": [{"role": "user", "content": message}]}, config=config)
        finally:
            console.quiet = quiet
        renderer.close_message()

        payload = build_payload(
            result, question=question, logs=info.logs, code=info.code, model=info.model,
            settings={**settings, "no_redact": no_redact},
        )
        store.record_turn(info.name, question, result.usage.get("total", 0), payload)
        updated = store.get(info.name)
        emit(custom("log_agent.turn", {
            "turn": updated.turns if updated else None,
            "status": payload["status"],
            "error": payload["error"],
            "summary": payload["summary"],
            "structured_status": payload["structured_status"],
        }))
        if result.error:
            emit(_event("RUN_ERROR", threadId=thread_id, runId=run_id, message=result.error))
        else:
            emit(_event("RUN_FINISHED", threadId=thread_id, runId=run_id, finishReason="stop"))
        return payload
    finally:
        (logfile._forced_encoding, timefilter._default_timezone,
         timefilter._default_window, enabled) = saved_globals
        redact.set_enabled(enabled)
        console.quiet = quiet
        conn.close()
