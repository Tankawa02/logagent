"""CLI 入口：log-agent analyze --log <日志路径> --code <代码目录>"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich import box
from rich.console import Console, Group
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


def _build_context_message(log_path: str, code_path: str | None, question: str) -> str:
    """把日志/源码路径和问题拼成给 agent 的首条消息。"""
    context_lines = [f"日志文件路径：{log_path}"]
    if code_path:
        context_lines.append(f"源码目录路径：{code_path}")
    else:
        context_lines.append("（本次未提供源码目录，只分析日志。）")
    context_lines.append(f"\n用户问题：{question}")
    return "\n".join(context_lines)


@app.command()
def analyze(
    log: Path = typer.Option(
        ..., "--log", "-l", help="日志文件路径", exists=True, dir_okay=False, readable=True
    ),
    code: Path = typer.Option(
        None, "--code", "-c", help="源码目录路径（可选）", exists=True, file_okay=False
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
    """单次分析日志文件，结合源码定位根因（一问一答）。"""
    _check_api_key()

    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    log_path = str(log.expanduser().resolve())
    code_path = str(code.expanduser().resolve()) if code else None

    user_message = _build_context_message(log_path, code_path, question)

    console.print(
        Panel.fit(
            f"[bold]日志:[/bold] {log_path}\n"
            f"[bold]源码:[/bold] {code_path or '（无）'}\n"
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
        final = result["messages"][-1].content
        console.print(Markdown(final if isinstance(final, str) else str(final)))


@app.command()
def chat(
    log: Path = typer.Option(
        ..., "--log", "-l", help="日志文件路径", exists=True, dir_okay=False, readable=True
    ),
    code: Path = typer.Option(
        None, "--code", "-c", help="源码目录路径（可选）", exists=True, file_okay=False
    ),
    model: str = typer.Option(
        "openai:gpt-4.1", "--model", "-m", help="模型，provider:model 格式"
    ),
    base_url: str = typer.Option(
        None, "--base-url",
        help="自定义 OpenAI 兼容接口地址（如自建网关/代理）；默认读环境变量 OPENAI_BASE_URL",
    ),
    session: str = typer.Option(
        "default", "--session", "-s",
        help="会话名称，不同名称的对话历史互相隔离；用相同名称可续上之前的对话",
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
    from langgraph.checkpoint.sqlite import SqliteSaver

    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    log_path = str(log.expanduser().resolve())
    code_path = str(code.expanduser().resolve()) if code else None

    # 会话数据库位置：默认放在 ~/.log-agent/sessions.db
    db_path = db.expanduser().resolve() if db else Path.home() / ".log-agent" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    console.print(
        Panel.fit(
            f"[bold]日志:[/bold] {log_path}\n"
            f"[bold]源码:[/bold] {code_path or '（无）'}\n"
            f"[bold]模型:[/bold] {model}\n"
            + (f"[bold]接口:[/bold] {base_url}\n" if base_url else "")
            + f"[bold]会话:[/bold] {session}  [dim]({db_path})[/dim]\n"
            f"[dim]输入问题开始对话；输入 exit / quit / 退出 结束。[/dim]",
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
                message = _build_context_message(log_path, code_path, user_input)
                first_turn = False
            else:
                message = user_input

            payload = {"messages": [{"role": "user", "content": message}]}

            if verbose:
                _run_streaming(agent, payload, config=config)
            else:
                with console.status("[cyan]思考中...[/cyan]", spinner="dots"):
                    result = agent.invoke(payload, config=config)
                final = result["messages"][-1].content
                console.print(
                    Markdown(final if isinstance(final, str) else str(final))
                )
    finally:
        conn.close()


def _run_streaming(agent, payload, config=None) -> None:
    """流式打印执行过程，并在最后渲染最终回答。

    流式只负责展示中间的工具调用过程；最终报告统一从最后一个状态快照里取
    messages[-1]，与非 verbose 模式（agent.invoke）保持完全一致，避免遗漏内容。
    """
    last_chunk = None
    seen_calls: set[str] = set()
    for chunk in agent.stream(payload, config=config, stream_mode="values"):
        last_chunk = chunk
        messages = chunk.get("messages", [])
        if not messages:
            continue
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None)
        if not tool_calls:
            continue
        for tc in tool_calls:
            # 同一条 tool_call 可能在多个快照里重复出现，按 id 去重避免重复打印
            call_id = tc.get("id") or f"{tc.get('name')}:{tc.get('args')}"
            if call_id in seen_calls:
                continue
            seen_calls.add(call_id)
            _render_tool_call(tc)

    console.print()
    console.rule("[bold green]最终报告[/bold green]")
    final = _extract_final_text(last_chunk)
    console.print(Markdown(final))


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
    """把 todo 列表渲染成一个任务进度面板：已完成 / 进行中 / 待办 + 进度统计。"""
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


def _extract_final_text(chunk) -> str:
    """从状态快照里取最终回答，与 agent.invoke 的取法一致（含非字符串兜底）。"""
    if not chunk:
        return "[未获取到模型输出]"
    messages = chunk.get("messages", [])
    if not messages:
        return "[未获取到模型输出]"
    content = messages[-1].content
    return content if isinstance(content, str) else str(content)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
