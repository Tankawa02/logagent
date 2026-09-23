"""CLI 入口：log-agent analyze --log <日志路径> --code <代码目录>"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.rule import Rule
from rich.text import Text

from . import __version__
from .render import StreamRenderer, info_panel
from .term import IS_WINDOWS, console, glyphs

app = typer.Typer(
    help="基于 deepagents 的 CLI 日志分析智能体：结合日志与源码定位问题根因。",
    add_completion=False,
)

_EXIT_WORDS = {"exit", "quit", ":q", "退出", "结束"}


def _check_api_key() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    console.print(Text("缺少 OPENAI_API_KEY 环境变量。", style="bold err"))
    if IS_WINDOWS:
        hints = [
            ("PowerShell", '$env:OPENAI_API_KEY="sk-..."'),
            ("CMD", "set OPENAI_API_KEY=sk-..."),
            ("永久生效", 'setx OPENAI_API_KEY "sk-..."（需重开终端）'),
        ]
    else:
        hints = [("Shell", 'export OPENAI_API_KEY="sk-..."')]
    for label, command in hints:
        console.print(Text.assemble(("  ", ""), (f"{label}: ", "muted"), (command, "accent")))
    raise typer.Exit(code=1)


def _build_context_message(log_path: str, code_paths: list[str], question: str) -> str:
    """把日志/源码路径和问题拼成给 agent 的首条消息。"""
    context_lines = [f"日志文件路径：{log_path}"]
    if code_paths:
        if len(code_paths) == 1:
            context_lines.append(f"源码目录路径：{code_paths[0]}")
        else:
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


def _base_rows(log_path: str, code_paths: list[str], model: str, base_url: str | None) -> list[tuple[str, Text | str]]:
    code_value = Text("\n".join(code_paths)) if code_paths else Text("（无）", style="muted")
    rows: list[tuple[str, Text | str]] = [
        ("日志", log_path),
        ("源码", code_value),
        ("模型", Text(model, style="accent")),
    ]
    if base_url:
        rows.append(("接口", base_url))
    return rows


def _resolve_paths(log: Path, code: list[Path] | None) -> tuple[str, list[str]]:
    return str(log.expanduser().resolve()), [str(c.expanduser().resolve()) for c in (code or [])]


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
        False, "--verbose", "-v", help="保留每一步工具调用与计划变化的完整记录"
    ),
) -> None:
    """单次分析日志文件，结合源码定位根因（一问一答）。可传多个 -c 同时分析多个代码库。"""
    _check_api_key()

    from .agent import build_agent

    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    log_path, code_paths = _resolve_paths(log, code)

    console.print(info_panel(_base_rows(log_path, code_paths, model, base_url), "log-agent", f"v{__version__}"))

    with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
        agent = build_agent(model=model, base_url=base_url)

    payload = {"messages": [{"role": "user", "content": _build_context_message(log_path, code_paths, question)}]}
    interrupted = StreamRenderer(verbose=verbose).run(agent, payload)
    if interrupted:
        raise typer.Exit(code=130)


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
        False, "--verbose", "-v", help="保留每一步工具调用与计划变化的完整记录"
    ),
) -> None:
    """多轮对话模式：连续追问，agent 记住整段对话；会话持久化到本地 SQLite，关掉终端后还能续上。"""
    _check_api_key()

    import sqlite3
    from datetime import datetime

    from langgraph.checkpoint.sqlite import SqliteSaver

    from .agent import build_agent

    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    log_path, code_paths = _resolve_paths(log, code)

    auto_session = session is None
    if auto_session:
        session = "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S")

    db_path = db.expanduser().resolve() if db else Path.home() / ".log-agent" / "sessions.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    session_value = Text(session, style="ok")
    if auto_session:
        session_value.append("  (自动生成)", style="warn")
    session_value.append(f"\n{db_path}", style="muted")

    footer: list[Text] = []
    if auto_session:
        footer.append(Text.assemble(("下次用 ", "muted"), (f"-s {session}", "accent"), (" 可续上这次对话", "muted")))
    footer.append(
        Text.assemble(
            ("输入问题开始对话", "muted"),
            (f" {glyphs.sep} ", "muted"),
            ("Ctrl+C", "accent"),
            (" 中断当前回答", "muted"),
            (f" {glyphs.sep} ", "muted"),
            ("exit", "accent"),
            (" 退出", "muted"),
        )
    )

    rows = _base_rows(log_path, code_paths, model, base_url)
    rows.append(("会话", session_value))
    console.print(info_panel(rows, "log-agent", f"多轮对话 {glyphs.sep} v{__version__}", footer))

    # SqliteSaver 把对话状态持久化到本地文件，靠 thread_id(=会话名) 串起多轮并跨进程恢复
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        checkpointer = SqliteSaver(conn)
        with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
            agent = build_agent(model=model, checkpointer=checkpointer, base_url=base_url)
        config = {"configurable": {"thread_id": session}}

        try:
            resumed = checkpointer.get(config) is not None
        except Exception:
            resumed = False
        if resumed:
            console.print(Text(f"{glyphs.ok} 已加载会话 '{session}' 的历史，可直接继续追问。", style="ok"))

        first_turn = not resumed
        first_prompt = True
        prompt = Text.assemble(("\n", ""), (f"{glyphs.prompt} ", "accent.strong"))
        while True:
            if not first_prompt:
                console.print()
                console.print(Rule(style="muted", characters=glyphs.rule))
            first_prompt = False
            try:
                user_input = console.input(prompt).strip()
            except (EOFError, KeyboardInterrupt):
                console.print(Text("\n已退出（会话已保存）。", style="muted"))
                break

            if not user_input:
                first_prompt = True
                continue
            if user_input.lower() in _EXIT_WORDS:
                console.print(Text("已退出（会话已保存）。", style="muted"))
                break

            if first_turn:
                message = _build_context_message(log_path, code_paths, user_input)
                first_turn = False
            else:
                message = user_input

            payload = {"messages": [{"role": "user", "content": message}]}
            interrupted = StreamRenderer(verbose=verbose).run(agent, payload, config=config)
            if interrupted:
                console.print(Text("本轮回答已中断，可以继续追问或换个问题。", style="muted"))
    finally:
        conn.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
