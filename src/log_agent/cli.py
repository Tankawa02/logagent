"""CLI 入口：log-agent analyze --log <日志路径> --code <代码目录>"""

from __future__ import annotations

import os
import warnings
from pathlib import Path

import typer
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from .agent import build_agent

# 工具名 -> (友好中文名, 主要参数字段)，用于美化工具调用展示
_TOOL_META = {
    "read_log_chunk": ("读取日志片段", ("path", "start", "end")),
    "search_logs": ("搜索日志", ("path", "pattern")),
    "list_code_files": ("列出源码文件", ("code_dir",)),
    "read_code_file": ("读取源码", ("code_dir", "rel_path")),
    "grep_code": ("检索源码", ("code_dir", "pattern")),
    "write_todos": ("规划任务", ()),
}

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
console = Console()


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


def _format_code_paths(code_paths: list[str]) -> str:
    """把源码目录列表格式化成 Panel 里展示的字符串。"""
    if not code_paths:
        return "（无）"
    if len(code_paths) == 1:
        return code_paths[0]
    return "\n      ".join(code_paths)


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

    console.print(
        Panel.fit(
            f"[bold]日志:[/bold] {log_path}\n"
            f"[bold]源码:[/bold] {_format_code_paths(code_paths)}\n"
            f"[bold]模型:[/bold] {model}"
            + (f"\n[bold]接口:[/bold] {base_url}" if base_url else ""),
            title="log-agent",
            border_style="cyan",
        )
    )

    agent = build_agent(model=model, base_url=base_url)
    payload = {"messages": [{"role": "user", "content": user_message}]}

    if verbose:
        _run_streaming(agent, payload)
    else:
        with console.status("[cyan]分析中...[/cyan]", spinner="dots"):
            result = agent.invoke(payload)
        console.print(Markdown(_collect_ai_texts(result["messages"])))


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

    # 会话数据库位置：默认放在 ~/.log-agent/sessions.db
    db_path = db.expanduser().resolve() if db else Path.home() / ".log-agent" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    session_line = f"[bold]会话:[/bold] [green]{session}[/green]"
    if auto_session:
        session_line += "  [yellow](自动生成)[/yellow]"
    session_line += f"  [dim]({db_path})[/dim]\n"
    resume_hint = (
        f"[dim]提示：下次用 -s {session} 可续上这次对话。[/dim]\n"
        if auto_session else ""
    )

    console.print(
        Panel.fit(
            f"[bold]日志:[/bold] {log_path}\n"
            f"[bold]源码:[/bold] {_format_code_paths(code_paths)}\n"
            f"[bold]模型:[/bold] {model}\n"
            + (f"[bold]接口:[/bold] {base_url}\n" if base_url else "")
            + session_line
            + resume_hint
            + f"[dim]输入问题开始对话；输入 exit / quit / 退出 结束。[/dim]",
            title="log-agent · 多轮对话",
            border_style="cyan",
        )
    )

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
        while True:
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
                with console.status("[cyan]思考中...[/cyan]", spinner="dots"):
                    result = agent.invoke(payload, config=config)
                console.print(Markdown(_collect_ai_texts(result["messages"])))
    finally:
        conn.close()


def _run_streaming(agent, payload, config=None) -> None:
    """用 LangChain v3 event streaming 逐 token 渲染整个执行过程。

    v3 的 `stream_events(..., version="v3")` 返回 typed projections：
      - `messages`：每次模型调用的文本增量，用 Live 实时刷新；
      - `tool_calls`：工具执行生命周期，在工具开始时渲染过程提示。
    AI 文本可能分多段（详细报告 + 收尾），每段独立用一个 Live 渲染，工具调用穿插其间。
    """
    seen_calls: set[str] = set()
    rendered_any = False

    def _render_message_stream(message_stream) -> None:
        """渲染 v3 messages projection 里的单次模型输出。"""
        nonlocal rendered_any
        buffer = ""
        live: Live | None = None
        streamed_text = False

        def _stop_live() -> None:
            nonlocal live
            if live is not None:
                live.update(Markdown(buffer))
                live.stop()
                live = None

        for delta in message_stream.text:
            if not delta:
                continue
            if live is None:
                console.print()
                live = Live(console=console, refresh_per_second=12, vertical_overflow="visible")
                live.start()
            buffer += delta
            live.update(Markdown(buffer))
            streamed_text = True
            rendered_any = True

        _stop_live()

        # 兜底：部分模型/网关可能不逐 token 推文本，但 v3 message.output 仍会给最终消息。
        final_text = (buffer or _content_to_text(getattr(message_stream.output, "content", ""))).strip()
        if final_text and not streamed_text:
            console.print()
            console.print(Markdown(final_text))
            rendered_any = True

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


def _render_tool_call(tc: dict) -> None:
    """把单次工具调用渲染成美观的输出；write_todos 渲染成任务进度面板。"""
    name = tc.get("name", "")
    args = tc.get("args") or {}

    if name == "write_todos":
        _render_todos(args.get("todos") or [])
        return

    label, fields = _TOOL_META.get(name, (name, ()))
    # 只挑关键参数，简洁展示，避免把整个 args 字典 dump 出来
    parts = []
    for f in fields:
        if f in args and args[f] not in (None, ""):
            parts.append(f"[cyan]{f}[/cyan]=[white]{args[f]}[/white]")
    detail = "  ".join(parts)
    line = Text.from_markup(f"  [dim]•[/dim] [bold]{label}[/bold]")
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
