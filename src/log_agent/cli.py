"""CLI 入口：log-agent analyze --log <日志路径> --code <代码目录>"""

from __future__ import annotations

import os
from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from .agent import build_agent

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
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="流式打印 agent 的每一步（工具调用 / 思考）"
    ),
) -> None:
    """单次分析日志文件，结合源码定位根因（一问一答）。"""
    _check_api_key()

    log_path = str(log.expanduser().resolve())
    code_path = str(code.expanduser().resolve()) if code else None

    user_message = _build_context_message(log_path, code_path, question)

    console.print(
        Panel.fit(
            f"[bold]日志:[/bold] {log_path}\n"
            f"[bold]源码:[/bold] {code_path or '（无）'}\n"
            f"[bold]模型:[/bold] {model}",
            title="log-agent",
            border_style="cyan",
        )
    )

    agent = build_agent(model=model)
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
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="流式打印 agent 的每一步（工具调用 / 思考）"
    ),
) -> None:
    """多轮对话模式：连续追问，agent 记住整段对话和已读过的日志。"""
    _check_api_key()

    # 延迟导入，单次 analyze 不需要它
    from langgraph.checkpoint.memory import InMemorySaver

    log_path = str(log.expanduser().resolve())
    code_path = str(code.expanduser().resolve()) if code else None

    console.print(
        Panel.fit(
            f"[bold]日志:[/bold] {log_path}\n"
            f"[bold]源码:[/bold] {code_path or '（无）'}\n"
            f"[bold]模型:[/bold] {model}\n"
            f"[dim]输入问题开始对话；输入 exit / quit / 退出 结束。[/dim]",
            title="log-agent · 多轮对话",
            border_style="cyan",
        )
    )

    # checkpointer 在进程内保存对话状态，靠固定 thread_id 串起多轮
    agent = build_agent(model=model, checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "log-agent-session"}}

    first_turn = True
    while True:
        try:
            user_input = console.input("\n[bold cyan]你> [/bold cyan]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]已退出。[/dim]")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"} or user_input in {"退出", "结束"}:
            console.print("[dim]已退出。[/dim]")
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


def _run_streaming(agent, payload, config=None) -> None:
    """流式打印执行过程，并在最后渲染最终回答。"""
    final_text = ""
    for chunk in agent.stream(payload, config=config, stream_mode="values"):
        messages = chunk.get("messages", [])
        if not messages:
            continue
        last = messages[-1]
        tool_calls = getattr(last, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                console.print(
                    f"[dim]→ 调用工具[/dim] [yellow]{tc.get('name')}[/yellow] "
                    f"[dim]{tc.get('args')}[/dim]"
                )
        content = getattr(last, "content", "")
        if isinstance(content, str) and content.strip():
            final_text = content
    console.rule("[bold green]最终报告[/bold green]")
    console.print(Markdown(final_text))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
