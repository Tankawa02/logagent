"""流式事件消费与 Live 生命周期；保留既有渲染 API 的导入入口。"""

from __future__ import annotations

import time
import warnings
from typing import Any

from rich.console import Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

from .citations import CitationLinker, LinkedMarkdown
from .netguard import describe_api_error, retry_watch
from .render_common import (
    _clip,
    _path_name,
    content_to_text,
    display_path,
    format_duration,
    info_panel,
    print_stats,
    shimmer,
    shorten_path,
)
from .render_report import (
    MarkdownTail,
    ToolRecord,
    TurnResult,
    collect_ai_texts,
    collect_usage,
    condense_note,
    looks_like_report,
    note_line,
    parse_summary_line,
    print_evidence_check,
    split_complete_blocks,
    summary_banner,
    usage_from_message,
)
from .render_tools import (
    ToolLine,
    ToolRun,
    _sub_run,
    _summarize_meta,
    _tool_icon,
    _tool_parts,
    settled_tool_line,
    summarize_tool_output,
    tool_trail,
)
from .report import visible_report
from .subtrace import SubagentTracker
from .term import REFRESH_PER_SECOND, console, glyphs

__all__ = [
    "StreamRenderer",
    "_clip",
    "_path_name",
    "content_to_text",
    "display_path",
    "format_duration",
    "info_panel",
    "print_stats",
    "shimmer",
    "shorten_path",
    "MarkdownTail",
    "ToolRecord",
    "TurnResult",
    "collect_ai_texts",
    "collect_usage",
    "condense_note",
    "looks_like_report",
    "note_line",
    "parse_summary_line",
    "print_evidence_check",
    "split_complete_blocks",
    "summary_banner",
    "usage_from_message",
    "ToolLine",
    "ToolRun",
    "_sub_run",
    "_summarize_meta",
    "_tool_icon",
    "_tool_parts",
    "settled_tool_line",
    "summarize_tool_output",
    "tool_trail",
]

_PHASE_BY_KIND = {"log": "正在查看日志", "code": "正在阅读源码", "other": "正在执行工具"}

# 每个子代理仅展示最近几步，避免挤出底部状态栏。
_LIVE_SUBCALLS = 3


class StreamRenderer:
    """一轮 agent 执行的完整渲染。

    - 底部常驻 Live：流式正文预览 + 运行中的工具 + 动态状态栏；
      后台刷新线程让计时与动效在等待模型/工具时也持续跳动。
    - 上方永久区：完整 Markdown 块、已完成的工具行（verbose）。
    """

    def __init__(self, verbose: bool, linker: CitationLinker | None = None, budget: Any = None) -> None:
        self.verbose = verbose
        self.linker = linker
        self.budget = budget
        self.summary: tuple[str, str] | None = None
        self.start = time.perf_counter()
        self.spinner = Spinner(glyphs.spinner, style="accent")
        self.tail = MarkdownTail()
        self.running: list[ToolRun] = []
        self.seen_calls: set[str] = set()
        self.tool_count = 0
        self.usage = {"input": 0, "output": 0, "total": 0}
        self.pending_chunks = 0
        self.writing = False
        self.rendered_any = False
        self.printed_answer_rule = False
        self.interrupted = False
        self.answer_parts: list[str] = []
        self.records: list[ToolRecord] = []
        self.llm_calls: list[dict] = []
        self.pending_note = ""
        self.tracker = SubagentTracker()

    # ---- 统计（主代理 + 子代理）--------------------------------------------

    def _total_usage(self) -> dict[str, int]:
        sub = self.tracker.usage
        return {key: self.usage[key] + sub[key] for key in self.usage}

    def _total_tools(self) -> int:
        return self.tool_count + self.tracker.tool_count

    # ---- Live 视图 ---------------------------------------------------------

    def _phase(self) -> str:
        active = [run for run in self.running if not run.completed]
        if active:
            tasks = [run for run in active if run.name == "task"]
            if len(active) > 1:
                if len(tasks) == len(active):
                    return f"{len(tasks)} 个子代理并行取证"
                return f"并行执行 {len(active)} 个工具"
            if tasks:
                return "子代理取证中"
            return _PHASE_BY_KIND.get(active[0].kind, "正在执行工具")
        if self.writing:
            return "正在撰写"
        if self.rendered_any:
            return "继续推理"
        return "思考中"

    def _status_line(self, now: float) -> Text:
        line = Text("  ")
        frame = self.spinner.render(now)
        line.append_text(frame if isinstance(frame, Text) else Text(str(frame)))
        line.append(" ")
        line.append_text(shimmer(self._phase() + glyphs.ellipsis, now))

        sep = f"  {glyphs.sep}  "
        retry_note = retry_watch.active_note()
        if retry_note:
            line.append(sep)
            line.append(retry_note, style="warn")
        meta = Text(sep + format_duration(now - self.start), style="muted")
        approx = self._total_usage()["total"] + self.pending_chunks
        if approx:
            prefix = "~" if self.pending_chunks else ""
            meta.append(f"{sep}{prefix}{approx:,} tokens")
        tools = self._total_tools()
        if tools:
            meta.append(f"{sep}工具 {tools}")
        meta.append(f"{sep}Ctrl+C 中断")
        line.append_text(meta)
        line.no_wrap = True
        line.overflow = "ellipsis"
        return line

    def __rich__(self) -> RenderableType:
        now = time.perf_counter()
        parts: list[RenderableType] = []

        running = list(self.running)

        tool_lines: list[RenderableType] = []
        for run in running:
            if run.note:
                tool_lines.append(note_line(run.note))
            if run.completed:
                tool_lines.append(settled_tool_line(run))
                continue
            tool_lines.append(self._running_line(run, now))
            if run.name == "task":
                tool_lines.extend(self._live_subcalls(run, now))

        if self.tail.text.strip():
            reserved = 4 + len(tool_lines)
            self.tail.max_lines = max(1, min(12, console.size.height - reserved))
            parts.extend([self.tail, Text("")])

        parts.extend(tool_lines)

        if running:
            parts.append(Text(""))
        parts.append(self._status_line(now))
        return Group(*parts)

    def _running_line(self, run: ToolRun, now: float, nested: bool = False) -> ToolLine:
        icon = self.spinner.render(now)
        icon = icon.copy() if isinstance(icon, Text) else Text(str(icon))
        icon.stylize(f"tool.{run.kind}")
        meta = Text(format_duration(now - run.started), style="muted")
        return ToolLine(run, icon, meta, nested=nested)

    def _live_subcalls(self, task: ToolRun, now: float) -> list[RenderableType]:
        calls = self.tracker.children(task.call_id)
        hidden = max(0, len(calls) - _LIVE_SUBCALLS)
        lines: list[RenderableType] = []
        if hidden:
            more = Text(f"    {glyphs.branch} ", style="muted")
            more.append(f"{glyphs.ellipsis} 已执行 {hidden} 步", style="muted")
            more.no_wrap = True
            lines.append(more)
        for call in calls[hidden:]:
            run = _sub_run(call)
            lines.append(settled_tool_line(run, nested=True) if call.completed else self._running_line(run, now, nested=True))
        return lines

    # ---- 永久输出 -----------------------------------------------------------

    def _flush_answer(self, text: str) -> None:
        if not text.strip():
            return
        if not self.printed_answer_rule:
            console.print()
            console.print(Rule(Text("分析结果", style="accent.strong"), style="muted", characters=glyphs.rule))
            self.printed_answer_rule = True
        for part in self._split_summary(visible_report(text)):
            console.print()
            console.print(part if isinstance(part, Text) else self._markdown(part))
        self.answer_parts.append(text)
        self.rendered_any = True

    def _markdown(self, text: str) -> Markdown:
        if self.linker is None or not self.linker.enabled:
            return Markdown(text)
        return LinkedMarkdown(self.linker.apply(text))

    def _split_summary(self, text: str) -> list[str | Text]:
        """把本轮第一处"一句话结论"行换成高亮横幅，其余部分照常按 Markdown 渲染。"""
        if self.summary is not None:
            return [text]
        lines = text.split("\n")
        for i, line in enumerate(lines):
            parsed = parse_summary_line(line)
            if parsed is None:
                continue
            self.summary = parsed
            before, after = "\n".join(lines[:i]).strip(), "\n".join(lines[i + 1 :]).strip()
            return [p for p in (before, summary_banner(*parsed), after) if p]
        return [text]

    def _record(self, run: ToolRun, subagent: str = "", incomplete: bool = False) -> None:
        error = getattr(run.handle, "error", None)
        if incomplete:
            summary, failed = "未完成：本轮结束时仍在运行", False
        elif error:
            summary, failed = str(error).splitlines()[0], True
        else:
            summary, failed = summarize_tool_output(run.name, getattr(run.handle, "output", None))
        ended = (None if incomplete else getattr(run.handle, "ended", None)) or time.perf_counter()
        self.records.append(
            ToolRecord(
                run.name, dict(run.args), summary, failed, round(ended - run.started, 3), subagent, run.note,
                started=round(max(0.0, run.started - self.start), 3),
                incomplete=incomplete,
            )
        )

    def _settle(self, run: ToolRun) -> None:
        self._record(run)
        if self.verbose:
            if run.note:
                console.print(note_line(run.note))
            console.print(settled_tool_line(run))
        if run.name != "task":
            return
        subagent = str(run.args.get("subagent_type") or "general-purpose")
        for call in self.tracker.children(run.call_id):
            sub = _sub_run(call)
            self._record(sub, subagent)
            if self.verbose:
                console.print(settled_tool_line(sub, nested=True))

    def _settle_tools(self) -> None:
        still_running = []
        for run in self.running:
            if run.completed:
                self._settle(run)
            else:
                still_running.append(run)
        self.running = still_running

    # ---- 事件处理 -----------------------------------------------------------

    def _record_llm_call(self, output: Any, call_start: float, first_token: float | None,
                         usage: dict[str, int] | None) -> None:
        """usage 为 None 表示流在中途断了（中断 / 接口报错）：用量拿不到，记为未知而不是 0。"""
        now = time.perf_counter()
        metadata = getattr(output, "response_metadata", None) or {}
        incomplete = usage is None
        self.llm_calls.append({
            "started": round(max(0.0, call_start - self.start), 3),
            "seconds": round(now - call_start, 3),
            "first_token": round(first_token - call_start, 3) if first_token is not None else None,
            "input": None if incomplete else usage.get("input", 0),
            "output": None if incomplete else usage.get("output", 0),
            "tool_calls": None if incomplete else len(getattr(output, "tool_calls", None) or []),
            "model": str(metadata.get("model_name") or metadata.get("model") or ""),
            "finish_reason": str(metadata.get("finish_reason") or ""),
            "incomplete": incomplete,
        })

    def _on_message(self, message_stream: Any) -> None:
        self._settle_tools()
        self.pending_note = ""
        buffer = ""
        streamed = False
        # 工具调用前模型常先说一句"先看看整体分布"：流式阶段还不知道后面有没有工具调用，
        # 所以短文本先只放在 Live 预览里，消息结束后再决定是旁白还是报告正文。
        holding = True
        self.writing = False
        call_start = time.perf_counter()
        first_token: float | None = None
        try:
            for delta in message_stream.text:
                if not delta:
                    continue
                if not streamed:
                    first_token = time.perf_counter()
                    self._settle_tools()
                streamed = True
                self.writing = True
                self.rendered_any = True
                buffer += delta
                self.pending_chunks += 1
                if holding and looks_like_report(buffer):
                    holding = False
                if holding:
                    self.tail.text = buffer
                    continue
                flushable, remainder = split_complete_blocks(buffer)
                if flushable.strip():
                    self.tail.text = remainder
                    self._flush_answer(flushable)
                    buffer = remainder
                else:
                    self.tail.text = buffer
        except BaseException:
            # 中断（含浏览器停止）或接口报错：这次模型调用也要留在 trace 里，半截正文仍由 run() 保存
            self._record_llm_call(None, call_start, first_token, None)
            raise

        output = getattr(message_stream, "output", None)
        final_text = buffer if streamed else content_to_text(getattr(output, "content", ""))
        self.tail.text = ""
        self.writing = False
        self.pending_chunks = 0
        call_usage = usage_from_message(output) if output is not None else {}
        for key, value in call_usage.items():
            self.usage[key] += value
        self._record_llm_call(output, call_start, first_token, call_usage)
        final_text = final_text.strip()
        if holding and getattr(output, "tool_calls", None) and not looks_like_report(final_text):
            self.pending_note = condense_note(final_text)
            return
        self._flush_answer(final_text)

    def _on_tool(self, tool_stream: Any) -> None:
        name = getattr(tool_stream, "tool_name", "") or ""
        args = getattr(tool_stream, "input", None) or {}
        if not isinstance(args, dict):
            args = {"input": args}
        call_id = getattr(tool_stream, "tool_call_id", None) or f"{name}:{args}"
        if call_id in self.seen_calls:
            return
        self.seen_calls.add(call_id)

        self.tool_count += 1
        # 旁白只挂在这一批的第一个工具上，并行的其它工具不重复显示
        self.running.append(ToolRun(call_id=call_id, name=name, args=args, handle=tool_stream, note=self.pending_note))
        self.pending_note = ""
        self.rendered_any = True

    # ---- 入口 ---------------------------------------------------------------

    def _with_tracker(self, config: dict[str, Any] | None) -> dict[str, Any]:
        merged = dict(config or {})
        handlers = [self.tracker] + ([self.budget] if self.budget is not None else [])
        callbacks = merged.get("callbacks")
        if callbacks is None:
            merged["callbacks"] = handlers
        elif isinstance(callbacks, list):
            merged["callbacks"] = [*callbacks, *handlers]
        else:
            callbacks = callbacks.copy()
            for handler in handlers:
                callbacks.add_handler(handler, inherit=True)
            merged["callbacks"] = callbacks
        return merged

    def _on_phase(self, name: str) -> None:
        """收尾阶段钩子：终端有自己的输出，Web 渲染器覆盖它把阶段推给浏览器。"""

    def run(self, agent: Any, payload: dict[str, Any], config: dict[str, Any] | None = None) -> TurnResult:
        """执行一轮并渲染，返回本轮结果（报告正文、耗时、用量、工具记录、是否中断）。"""
        from langgraph.errors import GraphRecursionError

        final_state: Any = None
        error = ""
        config = self._with_tracker(config)
        if self.budget is not None:
            self.budget.reset()
        retry_watch.reset()
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message=".*v3 streaming protocol on Pregel is experimental.*")
            live = Live(
                self,
                console=console,
                refresh_per_second=REFRESH_PER_SECOND,
                vertical_overflow="crop",
                transient=True,
            )
            try:
                with live:
                    with agent.stream_events(payload, config=config, version="v3") as stream:
                        for kind, item in stream.interleave("messages", "tool_calls"):
                            if kind == "messages":
                                self._on_message(item)
                            elif kind == "tool_calls":
                                self._on_tool(item)
                        final_state = stream.output
                    self._settle_tools()
            except KeyboardInterrupt:
                self.interrupted = True
                # 已经流出来但还没固化的半个块也保留下来，用户能看到中断前的内容
                self._flush_answer(self.tail.text)
                self.tail.text = ""
            except GraphRecursionError:
                self._flush_answer(self.tail.text)
                self.tail.text = ""
                error = "已达到最大推理步数"
                console.print()
                console.print(
                    Text(
                        f"{glyphs.fail} {error}，agent 可能在反复搜索。可以换个更具体的问题，"
                        "或用 --max-steps 调大上限。",
                        style="warn",
                    )
                )
            except Exception as exc:
                message = describe_api_error(exc)
                if message is None:
                    raise
                self._flush_answer(self.tail.text)
                self.tail.text = ""
                error = message
                console.print()
                console.print(Text(f"{glyphs.fail} {message}", style="err"))
                if self.answer_parts:
                    console.print(Text("已输出的部分内容会照常保存。", style="muted"))

        if not self.interrupted and not error:
            if not self.printed_answer_rule and isinstance(final_state, dict):
                text = collect_ai_texts(final_state.get("messages", []))
                if text:
                    self._flush_answer(text)
            if not self.printed_answer_rule:
                console.print(Text("未获取到模型输出。", style="muted"))
            if self.usage["total"] == 0 and isinstance(final_state, dict):
                self.usage = collect_usage(final_state.get("messages", []))

        for run in self.running:
            if run.completed:
                self._settle(run)
            else:
                # 已经告诉过浏览器 / 终端开始了，trace 里也得有这一段，并标成未完成
                self._record(run, incomplete=True)
        self.running = []
        elapsed = time.perf_counter() - self.start
        usage = self._total_usage()
        trail = None if self.verbose else tool_trail(self.records)
        if trail is not None:
            console.print()
            console.print(trail)
        budget_hit = self.budget is not None and self.budget.wrapped
        if budget_hit:
            from .budget import format_tokens

            console.print(Text(
                f"{glyphs.notice} 接近 tokens 预算 {format_tokens(self.budget.limit)}，"
                "已让 agent 停止取证、基于现有证据收尾；需要查得更深可以调大 --budget。",
                style="warn",
            ))
        print_stats(elapsed, usage, self._total_tools(), self.interrupted or bool(error))
        result = TurnResult(
            report="\n\n".join(self.answer_parts),
            elapsed=round(elapsed, 3),
            usage=usage,
            tools=list(self.records),
            interrupted=self.interrupted,
            error=error,
            summary=self.summary[0] if self.summary else "",
            confidence=self.summary[1] if self.summary else "",
            budget_hit=budget_hit,
            llm_calls=list(self.llm_calls),
        )
        if result.structured_status != "valid":
            console.print(Text("未获取到有效结构化报告，保留原始回答；自动化异常判定为未知。", style="warn"))
        elif self.linker is not None:
            from .evidence import check_analysis

            self._on_phase("verifying")
            try:
                result.evidence_check = check_analysis(result.analysis, self.linker.log_paths, self.linker.code_dirs)
            except Exception as exc:
                from .redact import redact_log

                # 核对是附加步骤：异常不能丢弃报告，也不能被当成“未做核对”而通过 --fail-on。
                result.evidence_check = {
                    "status": "unverifiable", "total": 0, "verified": 0, "shifted": 0,
                    "mismatch": 0, "unresolved": 0, "items": [],
                    "error": redact_log(f"{type(exc).__name__}: {exc}"),
                }
            print_evidence_check(result.evidence_check)
        return result
