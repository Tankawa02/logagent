"""CLI 入口：log-agent analyze --log <日志路径> --code <代码目录>"""

from __future__ import annotations

import os
import time
import warnings
from pathlib import Path

import typer
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from .agent import build_agent

# 工具名 -> (图标, 图标颜色, 友好中文名, 主要参数字段)，用于美化工具调用展示
# 日志类工具用黄色 ▤，源码类工具用蓝色 ◆，其它用青色 •
_TOOL_META = {
    "read_log_chunk": ("▤", "yellow", "读取日志片段", ("path", "start", "end")),
    "search_logs": ("▤", "yellow", "搜索日志", ("path", "pattern")),
    "list_code_files": ("◆", "blue", "列出源码文件", ("code_dir",)),
    "read_code_file": ("◆", "blue", "读取源码", ("code_dir", "rel_path")),
    "grep_code": ("◆", "blue", "检索源码", ("code_dir", "pattern")),
    "write_todos": ("•", "cyan", "规划任务", ()),
}

# 工具参数里属于"路径"的字段，展示时做截断美化
_PATH_FIELDS = {"path", "code_dir", "rel_path"}

# todo 状态 -> (图标, 颜色)
_TODO_STATUS = {
    "completed": ("✓", "green"),
    "in_progress": ("▶", "yellow"),
    "pending": ("○", "bright_black"),
}

app = typer.Typer(
    help="基于 deepagents 的 CLI 日志分析智能体：结合日志与源码定位问题根因。",
    add_completion=False,
)
# 终端很宽时把正文限制在 100 列以内，避免一行两百字符影响可读性
_terminal_width = Console().width
console = Console(width=min(_terminal_width, 100))


def _check_api_key() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        console.print(
            "[bold red]缺少 OPENAI_API_KEY 环境变量。[/bold red]\n"
            "请先设置：[cyan]export OPENAI_API_KEY=sk-...[/cyan]"
        )
        raise typer.Exit(code=1)


def _build_context_message(log_path: str, code_paths: list[str], question: str) -> str:
    """把日志/源码路径和问题拼成给 agent 的首条消息。"""
    context_lines = [f"日志文件路径：{log_path}"]
    if code_paths:
        if len(code_paths) == 1:
            context_lines.append(f"源码目录路径：{code_paths[0]}")
        else:
            # 多个代码库：逐个列出，并提示 agent 每个工具调用都要带上对应的 code_dir
            context_lines.append(f"共提供了 {len(code_paths)} 个源码目录，可分别检索：")
            for i, p in enumerate(code_paths, start=1):
                context_lines.append(f"  {i}. {p}")
            context_lines.append(
                "（调用 list_code_files / read_code_file / grep_code 时，"
                "请用对应仓库的目录路径作为 code_dir，按需逐个排查。）"
            )
    else:
        context_lines.append("（本次未提供源码目录，只分析日志。）")
    context_lines.append(f"\n用户问题：{question}")
    return "\n".join(context_lines)


def _shorten_path(value: str, keep: int = 3) -> str:
    """把长路径截断成 …/最后几段 的形式，避免撑爆一行。"""
    parts = str(value).rstrip("/").split("/")
    if len(parts) <= keep + 1:
        return str(value)
    return "…/" + "/".join(parts[-keep:])


def _info_panel(rows: list[tuple[str, str]], title: str, footer: str = "") -> Panel:
    """用两列对齐的 grid 构建启动信息面板，标签列等宽对齐。"""
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="bold cyan", no_wrap=True)
    grid.add_column(overflow="fold")
    for label, value in rows:
        grid.add_row(label, Text.from_markup(value) if "[" in value else value)
    body: Group | Table = grid
    if footer:
        body = Group(grid, Text(""), Text.from_markup(footer))
    return Panel.fit(body, title=f"[bold]{title}[/bold]", border_style="cyan", box=box.ROUNDED)


def _format_code_paths(code_paths: list[str]) -> str:
    """把源码目录列表格式化成 Panel 里展示的字符串。"""
    if not code_paths:
        return "（无）"
    return "\n".join(code_paths)


@app.command()
def analyze(
    log: Path = typer.Option(
        ..., "--log", "-l", help="日志文件路径", exists=True, dir_okay=False, readable=True
    ),
    code: list[Path] = typer.Option(
        None, "--code", "-c",
        help="源码目录路径（可选，可重复传多个以同时分析多个代码库）",
        exists=True, file_okay=False,
    ),
    question: str = typer.Option(
        "请分析这份日志，定位异常的根因并给出修复建议。",
        "--question", "-q", help="你想让 agent 回答的具体问题",
    ),
    model: str = typer.Option(
        "openai:gpt-4.1", "--model", "-m", help="模型，provider:model 格式"
    ),
    base_url: str = typer.Option(
        None, "--base-url",
        help="自定义 OpenAI 兼容接口地址（如自建网关/代理）；默认读环境变量 OPENAI_BASE_URL",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="流式打印 agent 的每一步（工具调用 / 思考）"
    ),
) -> None:
    """单次分析日志文件，结合源码定位根因（一问一答）。可传多个 -c 同时分析多个代码库。"""
    _check_api_key()

    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    log_path = str(log.expanduser().resolve())
    code_paths = [str(c.expanduser().resolve()) for c in (code or [])]

    user_message = _build_context_message(log_path, code_paths, question)

    rows = [
        ("日志", log_path),
        ("源码", _format_code_paths(code_paths)),
        ("模型", model),
    ]
    if base_url:
        rows.append(("接口", base_url))
    console.print(_info_panel(rows, title="log-agent"))

    agent = build_agent(model=model, base_url=base_url)
    payload = {"messages": [{"role": "user", "content": user_message}]}

    if verbose:
        _run_streaming(agent, payload)
    else:
        start = time.perf_counter()
        with console.status("[cyan]分析中...[/cyan]", spinner="dots"):
            result = agent.invoke(payload)
        elapsed = time.perf_counter() - start
        console.print()
        console.print(Rule("[bold cyan]分析结果[/bold cyan]", style="dim"))
        console.print()
        console.print(Markdown(_collect_ai_texts(result["messages"])))
        _print_stats(elapsed, _collect_usage(result["messages"]))


@app.command()
def chat(
    log: Path = typer.Option(
        ..., "--log", "-l", help="日志文件路径", exists=True, dir_okay=False, readable=True
    ),
    code: list[Path] = typer.Option(
        None, "--code", "-c",
        help="源码目录路径（可选，可重复传多个以同时分析多个代码库）",
        exists=True, file_okay=False,
    ),
    model: str = typer.Option(
        "openai:gpt-4.1", "--model", "-m", help="模型，provider:model 格式"
    ),
    base_url: str = typer.Option(
        None, "--base-url",
        help="自定义 OpenAI 兼容接口地址（如自建网关/代理）；默认读环境变量 OPENAI_BASE_URL",
    ),
    session: str = typer.Option(
        None, "--session", "-s",
        help="会话名称，不同名称的对话历史互相隔离；用相同名称可续上之前的对话。"
             "不指定时自动生成一个唯一会话名（形如 chat-20260609-165130）",
    ),
    db: Path = typer.Option(
        None, "--db",
        help="会话数据库文件路径（默认 ~/.log-agent/sessions.db）",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="流式打印 agent 的每一步（工具调用 / 思考）"
    ),
) -> None:
    """多轮对话模式：连续追问，agent 记住整段对话；会话持久化到本地 SQLite，关掉终端后还能续上。"""
    _check_api_key()

    # 延迟导入，单次 analyze 不需要它
    import sqlite3
    from datetime import datetime

    from langgraph.checkpoint.sqlite import SqliteSaver

    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    log_path = str(log.expanduser().resolve())
    code_paths = [str(c.expanduser().resolve()) for c in (code or [])]

    # 未指定会话名时，自动生成一个带时间戳的唯一会话名，并在面板中提示用户
    auto_session = session is None
    if auto_session:
        session = "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S")

    # 会话数据库位置：��认放在 ~/.log-agent/sessions.db
    db_path = db.expanduser().resolve() if db else Path.home() / ".log-agent" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    session_value = f"[green]{session}[/green]"
    if auto_session:
        session_value += "  [yellow](自动生成)[/yellow]"
    session_value += f"\n[dim]{db_path}[/dim]"

    footer = ""
    if auto_session:
        footer += f"[dim]提示：下次用 -s {session} 可续上这次对话。[/dim]\n"
    footer += "[dim]输入问题开始对话；输入 exit / quit / 退出 结束。[/dim]"

    rows = [
        ("日志", log_path),
        ("源码", _format_code_paths(code_paths)),
        ("模型", model),
    ]
    if base_url:
        rows.append(("接口", base_url))
    rows.append(("会话", session_value))
    console.print(_info_panel(rows, title="log-agent · 多轮对话", footer=footer))

    # SqliteSaver 把对话状态持久化到本地文件，靠 thread_id(=会话名) 串起多轮并跨进程恢复
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        checkpointer = SqliteSaver(conn)
        agent = build_agent(model=model, checkpointer=checkpointer, base_url=base_url)
        config = {"configurable": {"thread_id": session}}

        # 若该会话已有历史，提示用户这是续接而非新开
        try:
            resumed = checkpointer.get(config) is not None
        except Exception:
            resumed = False
        if resumed:
            console.print(f"[green]已加载会话 '{session}' 的历史，可直接继续追问。[/green]")

        # 只有全新会话才需要在首轮带上日志/源码路径上下文
        first_turn = not resumed
        first_prompt = True
        while True:
            # 第二轮起在新提问前画一条淡色分隔线，区分上下轮
            if not first_prompt:
                console.print()
                console.print(Rule(style="bright_black"))
            first_prompt = False
            try:
                user_input = console.input("\n[bold cyan]你> [/bold cyan]").strip()
            except (EOFError, KeyboardInterrupt):
                console.print("\n[dim]已退出（会话已保存）。[/dim]")
                break

            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit"} or user_input in {"退出", "结束"}:
                console.print("[dim]已退出（会话已保存）。[/dim]")
                break

            # 首轮把日志/源码路径作为上下文一起带上，之后只发用户的问题
            if first_turn:
                message = _build_context_message(log_path, code_paths, user_input)
                first_turn = False
            else:
                message = user_input

            payload = {"messages": [{"role": "user", "content": message}]}

            if verbose:
                _run_streaming(agent, payload, config=config)
            else:
                start = time.perf_counter()
                with console.status("[cyan]思考中...[/cyan]", spinner="dots"):
                    result = agent.invoke(payload, config=config)
                elapsed = time.perf_counter() - start
                console.print()
                console.print(Markdown(_collect_ai_texts(result["messages"])))
                _print_stats(elapsed, _collect_usage(result["messages"]))
    finally:
        conn.close()


def _split_complete_blocks(text: str) -> tuple[str, str]:
    """把已完整的 Markdown 块与还在生成中的尾部拆开（Claude Code 式增量固化）。

    以空行作为块边界，且绝不在未闭合的 ``` / ~~~ 代码围栏内部切分，
    保证固化出去的部分总是可以独立渲染的合法 Markdown。
    返回 (可固化部分, 剩余未完成部分)。
    """
    lines = text.split("\n")
    in_fence = False
    last_safe = -1  # 最后一个可安全切分的行号（空行且不在代码围栏内）
    # 最后一行很可能还没接收完整，永远留在剩余部分里
    for i, line in enumerate(lines[:-1]):
        stripped = line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        elif not in_fence and stripped == "":
            last_safe = i
    if last_safe < 0:
        return "", text
    return "\n".join(lines[:last_safe]), "\n".join(lines[last_safe + 1 :])


def _run_streaming(agent, payload, config=None) -> None:
    """用 LangChain v3 event streaming 逐 token 渲染整个执行过程。

    v3 的 `stream_events(..., version="v3")` 返回 typed projections：
      - `messages`：每次模型调用的文本增量，用 Live 实时刷新；
      - `tool_calls`：工具执行生命周期，在工具开始时渲染过程提示。
    AI 文本可能分多段（详细报告 + 收尾），每段独立用一个 Live 渲染，工具调用穿插其间。
    """
    seen_calls: set[str] = set()
    rendered_any = False
    printed_answer_rule = False
    start = time.perf_counter()
    # 已完成模型调用的精确 token 累计；进行中的调用用增量片段数粗略估算
    usage_totals = {"input": 0, "output": 0, "total": 0}
    # 常驻状态栏的可变状态：当前正在流式输出的文本 + 进行中调用的片段计数
    view_state = {"buffer": "", "pending_chunks": 0}
    spinner = Spinner("dots", style="cyan")

    def _stats_renderable():
        """每次刷新时重新计算耗时与 token，配合 Live 自动刷新实现持续跳动。"""
        elapsed = time.perf_counter() - start
        approx = usage_totals["total"] + view_state["pending_chunks"]
        prefix = "~" if view_state["pending_chunks"] else ""
        spinner.update(
            text=Text.from_markup(
                f" [dim]⏱ {_format_duration(elapsed)}  ·  tokens {prefix}{approx:,}[/dim]"
            )
        )
        return spinner

    class _LiveView:
        """常驻底部的动态视图：流式正文预览（若有）+ 实时统计行。

        Live 的后台刷新线程每次刷新都会重新调用 __rich__，因此即使主线程
        阻塞在等待模型/工具返回，计时器也会持续跳动（类似 Claude Code）。

        关键：这里**只渲染正文的末尾若干行纯文本预览**，且高度按终端实际高度
        自适应封顶。绝不在 Live 内渲染完整且不断增长的 Markdown——否则当报告
        高度超过终端高度时，Rich 无法正确回退覆盖旧内容，会反复重打、抖动。
        完整 Markdown 在每段结束后由 _render_message_stream 一次性固化到上方。
        """

        def __rich__(self):
            stats = _stats_renderable()
            buffer = view_state["buffer"]
            if not buffer:
                return stats
            # 预留 4 行给统计行/边距，其余留给预览，且至少 1 行、最多 6 行
            avail = max(1, min(6, console.size.height - 4))
            lines = buffer.splitlines() or [buffer]
            tail = lines[-avail:]
            preview = Text("\n".join(tail), style="dim", no_wrap=False, overflow="ellipsis")
            return Group(preview, Text(""), stats)

    def _accumulate_usage(message) -> None:
        """模型单次调用结束后，把精确的 usage_metadata 累加进总量。"""
        usage = _usage_from_message(message) if message is not None else None
        if usage:
            for key in usage_totals:
                usage_totals[key] += usage[key]

    def _flush_answer(text: str) -> None:
        """把一段完整的 Markdown 永久固化打印到 Live 区域上方。"""
        nonlocal printed_answer_rule, rendered_any
        if not text.strip():
            return
        # 第一块正式回答前画一条"分析结果"分隔线，让结论与工具流水区分开
        if not printed_answer_rule:
            console.print()
            console.print(Rule("[bold cyan]分析结果[/bold cyan]", style="dim"))
            printed_answer_rule = True
        console.print()
        console.print(Markdown(text))
        rendered_any = True

    def _render_message_stream(message_stream) -> None:
        """渲染 v3 messages projection 里的单次模型输出（Claude Code 式）。

        增量固化：每当缓冲区里凑齐了完整的 Markdown 块（以空行为界、
        不切开代码围栏），立刻把它固化打印到 Live 上方；Live 区只保留
        "正在生成中的半个块"的预览。这样正文随生成源源不断地长出来，
        而不是攒到最后一次性输出。
        """
        nonlocal rendered_any
        segment_streamed = False

        for delta in message_stream.text:
            if not delta:
                continue
            view_state["buffer"] += delta
            view_state["pending_chunks"] += 1
            rendered_any = True
            segment_streamed = True
            # 凑齐完整块就固化，剩余未完成部分继续留在 Live 预览里
            flushable, remainder = _split_complete_blocks(view_state["buffer"])
            if flushable.strip():
                _flush_answer(flushable)
                view_state["buffer"] = remainder

        # 该段结束：把最后剩余的尾部也固化掉。
        # 兜底：本段完全没推流式文本时，取 message.output 的最终内容。
        if segment_streamed:
            final_text = view_state["buffer"].strip()
        else:
            final_text = _content_to_text(getattr(message_stream.output, "content", "")).strip()
        view_state["buffer"] = ""
        view_state["pending_chunks"] = 0
        _accumulate_usage(getattr(message_stream, "output", None))
        _flush_answer(final_text)

    def _render_tool_stream(tool_stream) -> None:
        """渲染 v3 tool_calls projection 里的单次工具执行开始事件。"""
        nonlocal rendered_any
        name = getattr(tool_stream, "tool_name", "")
        args = getattr(tool_stream, "input", None) or {}
        call_id = getattr(tool_stream, "tool_call_id", None) or f"{name}:{args}"
        if call_id in seen_calls:
            return
        seen_calls.add(call_id)

        if name == "write_todos":
            todos = args.get("todos") or []
            _render_todos(todos)
            rendered_any = rendered_any or bool(todos)
            return

        _render_tool_call({"id": call_id, "name": name, "args": args})
        rendered_any = True

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*v3 streaming protocol on Pregel is experimental.*",
        )
        # 常驻 Live：整轮执行期间状态栏一直显示在底部；后台刷新线程让计时
        # 持续跳动（包括工具执行 / 等待模型响应的��隙）。期间的 console.print
        # 输出会自动固化到 Live 区域上方。
        with Live(
            _LiveView(),
            console=console,
            refresh_per_second=10,
            vertical_overflow="crop",
            transient=True,
        ):
            with agent.stream_events(payload, config=config, version="v3") as stream:
                for name, item in stream.interleave("messages", "tool_calls"):
                    if name == "messages":
                        _render_message_stream(item)
                    elif name == "tool_calls":
                        _render_tool_stream(item)

                final_state = stream.output

    # 兜底：若 projection 没有产出任何可见内容，尝试从最终 state 中提取本轮 AI 正文。
    if not rendered_any and isinstance(final_state, dict):
        text = _collect_ai_texts(final_state.get("messages", []))
        if text and text != "[未获取到模型输出]":
            console.print(Markdown(text))
            rendered_any = True

    if not rendered_any:
        console.print("[dim]未获取到模型输出。[/dim]")

    # 兜底：若流式过程中没拿到任何 usage（部分网关不在流式 chunk 上带用量），
    # 尝试从最终 state 的消息里汇总。
    if usage_totals["total"] == 0 and isinstance(final_state, dict):
        usage_totals = _collect_usage(final_state.get("messages", []))

    _print_stats(time.perf_counter() - start, usage_totals)


def _render_tool_call(tc: dict) -> None:
    """把单次工具调用渲染成美观的输出；write_todos 渲染成任务进度面板。"""
    name = tc.get("name", "")
    args = tc.get("args") or {}

    if name == "write_todos":
        _render_todos(args.get("todos") or [])
        return

    icon, color, label, fields = _TOOL_META.get(name, ("•", "cyan", name, ()))
    # 只挑关键参数，简洁展示；路径类参数截断成 …/末尾几段，避免撑爆一行
    parts = []
    for f in fields:
        if f in args and args[f] not in (None, ""):
            value = args[f]
            if f in _PATH_FIELDS:
                value = _shorten_path(str(value))
            parts.append(f"[cyan]{f}[/cyan]=[white]{value}[/white]")
    detail = "  ".join(parts)
    line = Text.from_markup(f"  [{color}]{icon}[/{color}] [bold]{label}[/bold]")
    if detail:
        line.append_text(Text.from_markup(f"  [dim]{detail}[/dim]"))
    console.print(line)


def _render_todos(todos: list[dict]) -> None:
    """把 todo 列表渲染成��个任务进度面板：已完成 / 进行中 / 待办 + 进度统计。"""
    if not todos:
        return

    rows = []
    done = 0
    for item in todos:
        status = item.get("status", "pending")
        content = item.get("content", "")
        icon, color = _TODO_STATUS.get(status, ("○", "bright_black"))
        if status == "completed":
            done += 1
            text = Text.from_markup(f"[{color}]{icon}[/{color}]  [strike dim]{content}[/strike dim]")
        elif status == "in_progress":
            text = Text.from_markup(f"[{color}]{icon}[/{color}]  [bold {color}]{content}[/bold {color}]")
        else:
            text = Text.from_markup(f"[{color}]{icon}[/{color}]  [white]{content}[/white]")
        rows.append(text)

    total = len(todos)
    # 进度条：已完成比例
    filled = int(round((done / total) * 12)) if total else 0
    bar = f"[green]{'━' * filled}[/green][bright_black]{'━' * (12 - filled)}[/bright_black]"
    header = Text.from_markup(f"{bar}  [bold]{done}/{total}[/bold] 已完成")

    body = Group(header, Text(""), *rows)
    console.print(
        Panel(
            body,
            title="[bold]任务进度[/bold]",
            border_style="cyan",
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def _format_duration(seconds: float) -> str:
    """把秒数格式化成易读的时长字符串。"""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m{secs:.0f}s"


def _usage_from_message(msg) -> dict:
    """从单条 AI 消息中提取 token 用量（usage_metadata），缺失时返回全 0。"""
    usage = getattr(msg, "usage_metadata", None) or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
    }


def _collect_usage(messages) -> dict:
    """汇总本轮（自上一条 human 消息之后）所有 AI 消息的 token 用量。"""
    totals = {"input": 0, "output": 0, "total": 0}
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", "")
        if msg_type == "human":
            break
        if msg_type != "ai":
            continue
        usage = _usage_from_message(msg)
        for key in totals:
            totals[key] += usage[key]
    return totals


def _print_stats(elapsed: float, usage: dict) -> None:
    """在回答末尾打印一条��计分隔线：总耗时 + token 用量（↑输入 ↓输出）。"""
    if usage["total"] > 0:
        title = (
            f"[dim]⏱ {_format_duration(elapsed)} · "
            f"↑ {usage['input']:,} ↓ {usage['output']:,} · 共 {usage['total']:,} tokens[/dim]"
        )
    else:
        title = f"[dim]⏱ {_format_duration(elapsed)} · tokens 未知（模型未返回用量）[/dim]"
    console.print()
    console.print(Rule(title, style="bright_black", align="right"))


def _collect_ai_texts(messages) -> str:
    """收集本轮所有 AI 文本消息并拼成完整报告。

    模型会分多条 AI 消息产出（详细报告 + 收尾总结），只取 messages[-1] 会丢掉
    前面的详细报告。这里从末尾往前扫，把本轮（直到上一条 human 消息为止）的所有
    AI 文本按时间顺序拼接，从而保留完整报告。
    """
    collected: list[str] = []
    for msg in reversed(messages):
        msg_type = getattr(msg, "type", "")
        if msg_type == "human":
            # 到达本轮用户提问，停止（不跨越到上一轮）
            break
        if msg_type != "ai":
            continue
        text = _content_to_text(getattr(msg, "content", "")).strip()
        if text:
            collected.append(text)
    if not collected:
        return "[未获取到模型输出]"
    # collected 是逆序的，翻转回正常时间顺序
    return "\n\n".join(reversed(collected))


def _content_to_text(content) -> str:
    """把消息 content 统一转成纯文本（兼容字符串与结构化内容块列表）。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                # 只取文本块，忽略其它类型（如思考块、引用块等）
                if block.get("type") == "text" and isinstance(block.get("text"), str):
                    parts.append(block["text"])
        return "\n".join(parts)
    return str(content) if content else ""


def main() -> None:
    app()


if __name__ == "__main__":
    main()
