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

import re
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

    def __init__(self, session: str, question: str, run_id: str, *, turn: int | None = None) -> None:
        self.session = session
        self.question = question
        self.run_id = run_id
        # 这一轮存档后会是第几轮：前端拿它和会话历史比对，已经存档的就不再重放，避免出现两遍
        self.turn = turn
        self.saved_turn: int | None = None
        self.started = time.time()
        self.finished_at: float | None = None
        self.cancelled = threading.Event()
        self.events: list[dict[str, Any]] = []
        self._finished = threading.Event()
        self._lock = threading.Lock()
        self._subscribers: list[tuple[Any, Any]] = []

    @property
    def done(self) -> bool:
        return self.finished_at is not None

    def wait(self, timeout: float) -> bool:
        return self._finished.wait(timeout)

    def emit(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._store(event)
            subscribers = list(self._subscribers)
        self._push(subscribers, event)

    def _store(self, event: dict[str, Any]) -> None:
        """记下用于重放的事件，同时压缩体积：重放结果不变，但不会随输出的字数线性增长。

        - 同一条回答连续的文字增量合并成一条；
        - 草稿（log_agent.draft）每次 reset 都会清空，之前的草稿事件不再需要重放。
        """
        kind = event.get("type")
        if kind == "CUSTOM" and event.get("name") == "log_agent.turn":
            value = event.get("value") or {}
            if isinstance(value, dict) and isinstance(value.get("turn"), int):
                self.saved_turn = value["turn"]
        last = self.events[-1] if self.events else None
        if (kind == "TEXT_MESSAGE_CONTENT" and last is not None and last.get("type") == kind
                and last.get("messageId") == event.get("messageId")):
            # 推给订阅者的是原事件对象，这里只替换存档里的那一份，不能原地修改
            self.events[-1] = {**last, "delta": str(last.get("delta", "")) + str(event.get("delta", ""))}
            return
        if kind == "CUSTOM" and event.get("name") == "log_agent.draft":
            value = event.get("value") or {}
            if value.get("reset"):
                self.events = [e for e in self.events if not (e.get("type") == "CUSTOM" and e.get("name") == "log_agent.draft")]
            elif (last is not None and last.get("type") == "CUSTOM" and last.get("name") == "log_agent.draft"
                    and "delta" in (last.get("value") or {}) and "delta" in value):
                merged = str(last["value"]["delta"]) + str(value["delta"])
                self.events[-1] = {**last, "value": {**last["value"], "delta": merged}}
                return
        self.events.append(event)

    def finish(self) -> None:
        with self._lock:
            self.finished_at = time.time()
            subscribers, self._subscribers = self._subscribers, []
        self._finished.set()
        self._push(subscribers, None)
        # 保留期一过就释放事件，不依赖之后还有没有人来查
        timer = threading.Timer(LIVE_RUN_GRACE_SECONDS, discard_live_run, args=(self.session, self))
        timer.daemon = True
        timer.start()

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
                "turn": self.turn, "saved_turn": self.saved_turn,
                "started": self.started, "finished_at": self.finished_at}


_LIVE_LOCK = threading.Lock()
_LIVE_RUNS: dict[str, LiveRun] = {}
# 停止请求比提问请求先到（刚发出就点停止）时先记下 run_id，这一轮一登记就立刻按中断处理
_EARLY_STOPS: dict[str, float] = {}
EARLY_STOP_TTL_SECONDS = 300


def register_live_run(run: LiveRun) -> None:
    with _LIVE_LOCK:
        _LIVE_RUNS[run.session] = run
        if _EARLY_STOPS.pop(run.run_id, None) is not None:
            run.cancelled.set()


def get_live_run(session: str) -> LiveRun | None:
    with _LIVE_LOCK:
        return _LIVE_RUNS.get(session)


def discard_live_run(session: str, run: LiveRun | None = None) -> None:
    """移除会话的 live run；给了 run 时只在它仍是当前登记的那一轮时才移除。"""
    with _LIVE_LOCK:
        if run is None or _LIVE_RUNS.get(session) is run:
            _LIVE_RUNS.pop(session, None)


def request_stop(session: str, run_id: str | None) -> dict[str, Any]:
    """停止指定的一轮。还没登记的 run_id 先记下，等它开始时直接按中断处理。"""
    with _LIVE_LOCK:
        live = _LIVE_RUNS.get(session)
        if live is not None and (run_id is None or live.run_id == run_id):
            if live.done:
                return {"stopped": False, "finished": True}
            live.cancelled.set()
            return {"stopped": True}
        if run_id is None:
            return {"stopped": False, "finished": True}
        now = time.time()
        for key in [k for k, t in _EARLY_STOPS.items() if now - t > EARLY_STOP_TTL_SECONDS]:
            del _EARLY_STOPS[key]
        _EARLY_STOPS[run_id] = now
        return {"stopped": True, "pending": True}


def _event(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "timestamp": int(time.time() * 1000), **fields}


def custom(name: str, value: Any) -> dict[str, Any]:
    return _event("CUSTOM", name=name, value=value)


_APPENDIX_START = re.compile(r"^```log-agent", re.MULTILINE)


class _TextTap:
    """包住 message_stream：把每个增量转发给浏览器，并在这里响应取消。

    还看不出是不是报告正文时（可能只是工具调用前的一句旁白），增量作为草稿推给时间线；
    一旦判定为正文，就逐个增量直接推进助手消息，浏览器里是连续的打字机效果。
    终端那边 StreamRenderer 仍按完整的 Markdown 块固化，存档内容不受影响。
    """

    def __init__(self, inner: Any, renderer: WebStreamRenderer) -> None:
        self._inner = inner
        self._renderer = renderer

    @property
    def text(self):
        from ..render_report import looks_like_report

        renderer = self._renderer
        renderer.emit(custom("log_agent.draft", {"reset": True}))
        buffer = ""
        for delta in self._inner.text:
            renderer.check_cancelled()
            if delta:
                buffer += delta
                if renderer.live_answer:
                    renderer.emit_answer(delta)
                    # 正文写完、开始写机器附录：附录在页面上是隐藏的，不提示的话看起来像已经结束
                    if _APPENDIX_START.search(buffer):
                        renderer.phase("structuring")
                elif looks_like_report(buffer):
                    renderer.live_answer = True
                    renderer.emit(custom("log_agent.draft", {"reset": True}))
                    renderer.emit_answer(buffer.lstrip(), new_block=True)
                else:
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
        # 当前这次模型输出已经在逐字推给浏览器：_flush_answer 只做存档记账，不再重复推送
        self.live_answer = False
        self.sent_any = False
        self.current_phase = ""

    def phase(self, name: str) -> None:
        """正文之后的收尾阶段（整理结构化报告 → 核对证据 → 保存），每个阶段只通知一次。"""
        if name == self.current_phase:
            return
        self.current_phase = name
        self.emit(custom("log_agent.phase", {"phase": name}))

    def _on_phase(self, name: str) -> None:
        self.phase(name)

    def check_cancelled(self) -> None:
        if self.cancelled.is_set():
            raise TurnCancelled()

    def emit_answer(self, text: str, *, new_block: bool = False) -> None:
        if not text:
            return
        if not self.message_open:
            self.emit(_event("TEXT_MESSAGE_START", messageId=self.message_id, role="assistant"))
            self.message_open = True
        separator = "\n\n" if new_block and self.sent_any else ""
        self.emit(_event("TEXT_MESSAGE_CONTENT", messageId=self.message_id, delta=separator + text))
        self.sent_any = True

    def _on_message(self, message_stream: Any) -> None:
        self.check_cancelled()
        self.live_answer = False
        try:
            super()._on_message(_TextTap(message_stream, self))
        finally:
            self.live_answer = False
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
        if len(self.answer_parts) == before or self.live_answer:
            return
        self.emit_answer(text, new_block=True)

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


def _open_memory(memory_path: Path | None, mode: str, info: SessionInfo):
    """和 CLI 共用同一个记忆库与项目归属；记忆库打不开时本轮不用记忆，不影响分析。"""
    from ..memory import MemorySession, MemoryStore, default_memory_path, normalize_mode, project_key

    # serve 启动时已校验；这里兜底时宁可关闭，也不在用户想关掉记忆时悄悄打开
    mode = normalize_mode(mode) or "off"
    if mode == "off":
        return None
    try:
        store = MemoryStore(memory_path or default_memory_path())
    except (sqlite3.Error, OSError):
        return None
    return MemorySession(store=store, mode=mode, project=project_key(info.code), session=info.name)


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
    memory_path: Path | None = None,
    memory_mode: str = "suggest",
    prefix: str = "",
) -> dict[str, Any] | None:
    """执行一���续问并存进会话；返回本轮报告快照。调用方负责持有 TURN_LOCK。"""
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
    memory = None
    try:
        store = SessionStore(conn)
        no_redact = session_no_redact(store, info.name)
        _apply_session_settings(settings, no_redact)
        baseline = parse_range(settings["baseline"]) if settings.get("baseline") else None
        budget = TokenBudget(parse_budget(str(settings["budget"]))) if settings.get("budget") else None
        max_steps = int(settings.get("max_steps") or 120)
        config = _run_config(max_steps, info.name)

        memory = _open_memory(memory_path, memory_mode, info)
        checkpointer = SqliteSaver(conn)
        agent = agent_factory(model=info.model, checkpointer=checkpointer,
                              base_url=settings.get("base_url") or base_url, budget=budget, memory=memory)
        try:
            first_turn = checkpointer.get(config) is None
        except Exception:
            first_turn = True
        message = (_build_context_message(info.logs, info.code, question, baseline) if first_turn else question)
        # 会话中途在网页上改了来源 / 范围：和 CLI 的 source_note 一样，在下一条消息前告知模型
        pending_note = settings.pop("pending_note", None)
        if pending_note and not first_turn:
            message = f"{pending_note}\n\n{message}"
        if prefix:
            message = f"{prefix}\n\n{message}"

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
        renderer.phase("saving")
        if memory is not None:
            # 候选登记进记忆库；对话页收到事件后重新拉取本会话待确认的候选，直接在对话里请用户确认
            try:
                immediate = memory.finish_turn(question)
                pending = len(memory.session_due())
            except sqlite3.Error:
                immediate, pending = [], 0
            emit(custom("log_agent.memory", {"immediate": len(immediate), "pending": pending}))

        payload = build_payload(
            result, question=question, logs=info.logs, code=info.code, model=info.model,
            settings={**settings, "no_redact": no_redact},
        )
        store.record_turn(info.name, question, result.usage.get("total", 0), payload)
        if pending_note:
            latest = store.get(info.name)
            if latest is not None and latest.settings.get("pending_note") == pending_note:
                store.touch(info.name, latest.logs, latest.code, latest.model,
                            {k: v for k, v in latest.settings.items() if k != "pending_note"})
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
        if memory is not None:
            memory.store.close()
        conn.close()
