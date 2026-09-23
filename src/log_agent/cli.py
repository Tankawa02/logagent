"""CLI 入口：log-agent analyze / chat / sessions"""

from __future__ import annotations

import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from . import __version__
from .render import StreamRenderer, TurnResult, format_duration, info_panel
from .term import IS_WINDOWS, console, glyphs, reset_cursor_line

app = typer.Typer(
    help="基于 deepagents 的 CLI 日志分析智能体：结合日志与源码定位问题根因。",
    add_completion=False,
    no_args_is_help=True,
)
sessions_app = typer.Typer(help="管理 chat 模式保存的会话。", no_args_is_help=True)
app.add_typer(sessions_app, name="sessions")

_EXIT_WORDS = {"exit", "quit", ":q", "退出", "结束", "/exit", "/quit"}
DEFAULT_MODEL = "openai:gpt-4.1"


class ReportFormat(StrEnum):
    markdown = "markdown"
    json = "json"


# ---------------------------------------------------------------------------
# 公共选项
# ---------------------------------------------------------------------------

LOG_HELP = "日志文件路径，可重复传多个；支持通配符（如 'logs/app*.log'）、.gz 压缩日志，'-' 表示从管道读取"
CODE_HELP = "源码目录路径（可选，可重复传多个以同时分析多个代码库）"

_opt_code = typer.Option(None, "--code", "-c", help=CODE_HELP, exists=True, file_okay=False)
_opt_model = typer.Option(None, "--model", "-m", help=f"模型，provider:model 格式；默认读 LOG_AGENT_MODEL，否则 {DEFAULT_MODEL}")
_opt_base_url = typer.Option(None, "--base-url", help="自定义 OpenAI 兼容接口地址；默认读环境变量 OPENAI_BASE_URL")
_opt_encoding = typer.Option(None, "--encoding", help="强制指定日志编码（如 gbk、utf-16）；默认自动探测")
_opt_no_redact = typer.Option(False, "--no-redact", help="关闭敏感信息脱敏（默认会打码 token、手机号、身份证、邮箱、IP 等）")
_opt_max_steps = typer.Option(120, "--max-steps", min=10, help="单轮最多推理步数，防止 agent 陷入反复搜索")
_opt_verbose = typer.Option(False, "--verbose", "-v", help="保留每一步工具调用与计划变化的完整记录")
_opt_since = typer.Option(None, "--since", help="只分析该时间之后的日志，如 '2026-06-09 14:00' 或 '14:00'")
_opt_until = typer.Option(None, "--until", help="只分析到该时间为止（按给出的精度包含整段，'14:05' 含 14:05:59）")


@app.callback()
def _load_config(ctx: typer.Context) -> None:
    """读取配置文件，作为 analyze / chat 各参数的默认值（命令行显式传入的仍然优先）。"""
    from .config import COMMANDS, ConfigError, apply_to_environment, cli_defaults, load_config, set_loaded

    command = ctx.invoked_subcommand
    if command not in COMMANDS:
        return
    try:
        config = load_config()
    except ConfigError as exc:
        _fail(str(exc))
    set_loaded(config)
    for warning in config.warnings:
        console.print(Text(f"{glyphs.fail} {warning}", style="warn"))
    if not config.files:
        return
    values = config.for_command(command)
    apply_to_environment(values)
    existing = dict(ctx.default_map or {})
    existing[command] = {**cli_defaults(values), **existing.get(command, {})}
    ctx.default_map = existing


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


def _fail(message: str) -> None:
    console.print(Text(f"{glyphs.fail} {message}", style="err"))
    raise typer.Exit(code=2)


def _prepare(
    log: list[str],
    code: list[Path] | None,
    encoding: str | None,
    no_redact: bool,
    since: str | None = None,
    until: str | None = None,
) -> tuple[list[str], list[str]]:
    """解析日志输入、应用编码 / 脱敏 / 时间窗口设置，返回 (日志绝对路径, 源码绝对路径)。"""
    from . import redact
    from .inputs import LogInputError, resolve_log_inputs
    from .logfile import set_forced_encoding
    from .timefilter import set_default_window

    try:
        set_forced_encoding(encoding)
    except LookupError:
        _fail(f"不认识的编码名: {encoding}")
    try:
        set_default_window(since, until)
    except ValueError as exc:
        _fail(str(exc))
    redact.set_enabled(not no_redact)
    try:
        log_paths = resolve_log_inputs(log)
    except LogInputError as exc:
        _fail(str(exc))
    code_paths = [str(c.expanduser().resolve()) for c in (code or [])]
    return log_paths, code_paths


def _resolve_model(model: str | None) -> str:
    return model or os.environ.get("LOG_AGENT_MODEL") or DEFAULT_MODEL


def _build_context_message(log_paths: list[str], code_paths: list[str], question: str) -> str:
    """把日志/源码路径和问题拼成给 agent 的首条消息。"""
    lines: list[str] = []
    if len(log_paths) == 1:
        lines.append(f"日志文件路径：{log_paths[0]}")
    else:
        lines.append(
            f"共提供了 {len(log_paths)} 份日志，请先分别调用 log_overview；"
            "追同一个请求时可用 trace_request 一次传入全部路径，按时间合并各日志：",
        )
        lines.extend(f"  {i}. {p}" for i, p in enumerate(log_paths, start=1))

    if code_paths:
        if len(code_paths) == 1:
            lines.append(f"源码目录路径：{code_paths[0]}")
        else:
            lines.append(f"共提供了 {len(code_paths)} 个源码目录，可分别检索：")
            lines.extend(f"  {i}. {p}" for i, p in enumerate(code_paths, start=1))
            lines.append(
                "（调用 list_code_files / read_code_file / grep_code 时，"
                "请用对应仓库的目录路径作为 code_dir，按需逐个排查。）"
            )
    else:
        lines.append("（本次未提供源码目录，只分析日志。）")

    from .timefilter import default_window

    window = default_window()
    if window:
        lines.append(
            f"时间窗口：{window.describe()}。log_overview / search_logs 不传 since/until 时会自动只看这个窗口；"
            "需要对比窗口之前的情况时可以显式传入其它时间。read_log_chunk 按行号读取，不受窗口限制。"
        )
    lines.append(f"\n用户问题：{question}")
    return "\n".join(lines)


def _base_rows(log_paths: list[str], code_paths: list[str], model: str, base_url: str | None) -> list[tuple[str, Text | str]]:
    from . import redact
    from .logfile import open_log

    log_value = Text()
    for i, path in enumerate(log_paths):
        if i:
            log_value.append("\n")
        log_value.append(path)
        try:
            meta = open_log(path)
            tags = [meta.encoding] + (["gzip"] if meta.gz else [])
            log_value.append(f"  {' · '.join(tags)}", style="muted")
        except OSError:
            pass

    code_value = Text("\n".join(code_paths)) if code_paths else Text("（无）", style="muted")
    rows: list[tuple[str, Text | str]] = [
        ("日志", log_value),
        ("源码", code_value),
        ("模型", Text(model, style="accent")),
    ]
    if base_url:
        rows.append(("接口", base_url))

    from .timefilter import default_window

    if default_window():
        rows.append(("时间", Text(default_window().describe(), style="accent")))
    rows.append(("脱敏", Text("开启", style="ok") if redact.is_enabled() else Text("已关闭", style="warn")))

    from .config import loaded

    if loaded().files:
        rows.append(("配置", Text("\n".join(str(p) for p in loaded().files), style="muted")))
    return rows


def _run_config(max_steps: int, thread_id: str | None = None) -> dict:
    # langgraph 的每个节点执行算一步，一次"模型 + 工具"大约 2-3 步
    config: dict = {"recursion_limit": max_steps * 3}
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    return config


# ---------------------------------------------------------------------------
# analyze
# ---------------------------------------------------------------------------


@app.command()
def analyze(
    log: list[str] = typer.Option(..., "--log", "-l", help=LOG_HELP),
    code: list[Path] = _opt_code,
    question: str = typer.Option(
        "请分析这份日志，定位异常的根因并给出修复建议。",
        "--question", "-q", help="你想让 agent 回答的具体问题",
    ),
    output: Path = typer.Option(None, "--output", "-o", help="把报告保存到文件（.md 或 .json）"),
    fmt: ReportFormat = typer.Option(None, "--format", "-f", help="报告格式；默认按 -o 的扩展名推断"),
    model: str = _opt_model,
    base_url: str = _opt_base_url,
    since: str = _opt_since,
    until: str = _opt_until,
    encoding: str = _opt_encoding,
    no_redact: bool = _opt_no_redact,
    max_steps: int = _opt_max_steps,
    verbose: bool = _opt_verbose,
) -> None:
    """单次分析日志，结合源码定位根因（一问一答）。"""
    _check_api_key()
    log_paths, code_paths = _prepare(log, code, encoding, no_redact, since, until)
    model = _resolve_model(model)
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")

    from .agent import build_agent

    reset_cursor_line()
    console.print(info_panel(_base_rows(log_paths, code_paths, model, base_url), "log-agent", f"v{__version__}"))
    with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
        agent = build_agent(model=model, base_url=base_url)

    payload = {"messages": [{"role": "user", "content": _build_context_message(log_paths, code_paths, question)}]}
    result = StreamRenderer(verbose=verbose).run(agent, payload, config=_run_config(max_steps))

    if output:
        from .export import build_payload, infer_format, write_report

        data = build_payload(result, question=question, logs=log_paths, code=code_paths, model=model)
        saved = write_report(output, data, infer_format(output, fmt.value if fmt else None))
        console.print(Text.assemble((f"{glyphs.ok} 报告已保存 ", "ok"), (str(saved), "accent")))

    if result.interrupted:
        raise typer.Exit(code=130)
    if result.error:
        raise typer.Exit(code=1)


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


class _ChatTotals:
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


def _new_session_name() -> str:
    return "chat-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def _print_help() -> None:
    from .chat_input import SLASH_COMMANDS

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="accent", no_wrap=True)
    grid.add_column(style="muted")
    for cmd, desc in SLASH_COMMANDS.items():
        grid.add_row(cmd, desc)
    grid.add_row("Ctrl+C", "回答中按下只中断当前这一轮；在输入框按下退出")
    console.print(grid)


@app.command()
def chat(
    log: list[str] = typer.Option(..., "--log", "-l", help=LOG_HELP.replace("，'-' 表示从管道读取", "")),
    code: list[Path] = _opt_code,
    model: str = _opt_model,
    base_url: str = _opt_base_url,
    session: str = typer.Option(
        None, "--session", "-s",
        help="会话名称；用相同名称可续上之前的对话。不指定时自动生成（形如 chat-20260609-165130）",
    ),
    db: Path = typer.Option(None, "--db", help="会话数据库文件路径（默认 ~/.log-agent/sessions.db）"),
    since: str = _opt_since,
    until: str = _opt_until,
    encoding: str = _opt_encoding,
    no_redact: bool = _opt_no_redact,
    max_steps: int = _opt_max_steps,
    verbose: bool = _opt_verbose,
) -> None:
    """多轮对话模式：连续追问，会话持久化到本地 SQLite，关掉终端后还能续上。"""
    if "-" in log:
        _fail("chat 模式需要在终端里输入问题，不能用 -l - 从管道读日志；请先把日志保存成文件，或改用 analyze。")
    _check_api_key()
    log_paths, code_paths = _prepare(log, code, encoding, no_redact, since, until)
    model = _resolve_model(model)
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")

    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from .agent import build_agent
    from .chat_input import ChatInput
    from .export import build_payload, write_report
    from .sessions import SessionStore, default_db_path, describe_source_change

    auto_session = session is None
    session = session or _new_session_name()
    db_path = db.expanduser().resolve() if db else default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    try:
        store = SessionStore(conn)
        previous = store.get(session)
        source_note = describe_source_change(previous, log_paths, code_paths) if previous else ""

        session_value = Text(session, style="ok")
        if auto_session:
            session_value.append("  (自动生成)", style="warn")
        elif previous:
            session_value.append(f"  (续上 {previous.turns} 轮)", style="muted")
        session_value.append(f"\n{db_path}", style="muted")

        footer: list[Text] = []
        if auto_session:
            footer.append(Text.assemble(("下次用 ", "muted"), (f"-s {session}", "accent"), (" 可续上这次对话", "muted")))
        footer.append(
            Text.assemble(
                ("输入问题开始对话", "muted"), (f" {glyphs.sep} ", "muted"),
                ("/help", "accent"), (" 查看命令", "muted"), (f" {glyphs.sep} ", "muted"),
                ("Ctrl+C", "accent"), (" 中断当前回答", "muted"),
            )
        )
        rows = _base_rows(log_paths, code_paths, model, base_url)
        rows.append(("会话", session_value))
        reset_cursor_line()
        console.print(info_panel(rows, "log-agent", f"多轮对话 {glyphs.sep} v{__version__}", footer))

        checkpointer = SqliteSaver(conn)
        with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
            agent = build_agent(model=model, checkpointer=checkpointer, base_url=base_url)

        def has_history(name: str) -> bool:
            try:
                return checkpointer.get({"configurable": {"thread_id": name}}) is not None
            except Exception:
                return False

        first_turn = not has_history(session)
        if not first_turn:
            console.print(Text(f"{glyphs.ok} 已加载会话 '{session}' 的历史，可直接继续追问。", style="ok"))
            if source_note:
                console.print(Text(f"{glyphs.todo_active} 日志/源码与上次不同，会在下一条消息里告知 agent。", style="warn"))
        store.touch(session, log_paths, code_paths, model)

        chat_input = ChatInput(db_path.parent / "history")
        totals = _ChatTotals()
        last: tuple[str, TurnResult] | None = None
        first_prompt = True

        while True:
            if not first_prompt:
                console.print()
                console.print(Rule(style="muted", characters=glyphs.rule))
            first_prompt = False
            try:
                user_input = chat_input.read().strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input:
                first_prompt = True
                continue
            lowered = user_input.lower()
            if lowered in _EXIT_WORDS:
                break

            if user_input.startswith("/"):
                command, _, arg = user_input.partition(" ")
                command = command.lower()
                first_prompt = True
                if command == "/help":
                    _print_help()
                elif command == "/sources":
                    console.print(info_panel(_base_rows(log_paths, code_paths, model, base_url), "当前会话", session))
                elif command == "/stats":
                    console.print(totals.line())
                elif command == "/new":
                    session = _new_session_name()
                    first_turn = True
                    source_note = ""
                    store.touch(session, log_paths, code_paths, model)
                    console.print(Text.assemble((f"{glyphs.ok} 已切换到新会话 ", "ok"), (session, "accent")))
                elif command == "/save":
                    if last is None:
                        console.print(Text("还没有可以保存的回答。", style="muted"))
                        continue
                    question, result = last
                    default_name = f"log-agent-report-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
                    target = Path(arg.strip().strip('"')) if arg.strip() else Path.cwd() / default_name
                    data = build_payload(result, question=question, logs=log_paths, code=code_paths, model=model)
                    fmt = "json" if target.suffix.lower() == ".json" else "markdown"
                    try:
                        saved = write_report(target, data, fmt)
                    except OSError as exc:
                        console.print(Text(f"{glyphs.fail} 保存失败: {exc}", style="err"))
                        continue
                    console.print(Text.assemble((f"{glyphs.ok} 已保存 ", "ok"), (str(saved), "accent")))
                else:
                    console.print(Text(f"未知命令 {command}，输入 /help 查看可用命令。", style="warn"))
                continue

            if first_turn:
                message = _build_context_message(log_paths, code_paths, user_input)
                first_turn = False
            elif source_note:
                message = f"{source_note}\n\n{user_input}"
            else:
                message = user_input
            source_note = ""

            payload = {"messages": [{"role": "user", "content": message}]}
            result = StreamRenderer(verbose=verbose).run(agent, payload, config=_run_config(max_steps, session))
            totals.add(result)
            store.record_turn(session, user_input, result.usage.get("total", 0))
            if result.report:
                last = (user_input, result)
            if result.interrupted:
                console.print(Text("本轮回答已中断，可以继续追问或换个问题。", style="muted"))
    finally:
        conn.close()

    console.print()
    if totals.turns:
        console.print(totals.line())
    console.print(Text.assemble(("已退出，会话已保存。下次用 ", "muted"), (f"-s {session}", "accent"), (" 续上。", "muted")))


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

_CONFIG_TEMPLATE = """\
# log-agent 项目配置：命令行没写的参数从这里取默认值（命令行 > 环境变量 > 本文件）。
# 相对路径以本文件所在目录为准。

# model = "openai:gpt-4.1"
# base_url = "https://your-gateway.example.com/v1"   # API key 仍放在环境变量 OPENAI_API_KEY
# code = ["./"]
# max_steps = 120
# timeout = 120        # 单次模型请求超时（秒）
# max_retries = 3      # 模型请求失败自动重试次数
# encoding = "gbk"
# no_redact = false

# [analyze]            # 只对 analyze 生效
# verbose = true

# [chat]               # 只对 chat 生效
# db = "~/.log-agent/sessions.db"
"""


@app.command("config")
def show_config(
    init: bool = typer.Option(False, "--init", help=f"在当前目录生成一份带注释的 {'.log-agent.toml'} 模板"),
) -> None:
    """查看当前生效的配置文件与配置项。"""
    from .config import PROJECT_FILE, ConfigError, find_project_config, load_config, user_config_path

    if init:
        target = Path.cwd() / PROJECT_FILE
        if target.exists():
            _fail(f"{target} 已存在，不会覆盖。")
        target.write_text(_CONFIG_TEMPLATE, encoding="utf-8")
        console.print(Text.assemble((f"{glyphs.ok} 已生成 ", "ok"), (str(target), "accent")))
        return

    try:
        config = load_config()
    except ConfigError as exc:
        _fail(str(exc))
    for warning in config.warnings:
        console.print(Text(f"{glyphs.fail} {warning}", style="warn"))

    searched = [user_config_path(), find_project_config() or Path.cwd() / PROJECT_FILE]
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="accent.strong", no_wrap=True)
    grid.add_column(overflow="fold")
    for path in searched:
        state = Text("已加载", style="ok") if path in config.files else Text("未找到", style="muted")
        grid.add_row(state, str(path))
    for path in config.files:
        if path not in searched:
            grid.add_row(Text("已加载", style="ok"), f"{path}  (LOG_AGENT_CONFIG)")
    console.print(grid)
    if not config.files:
        console.print(Text.assemble(("用 ", "muted"), ("log-agent config --init", "accent"), (" 生成项目配置模板", "muted")))
        return

    analyze_values, chat_values = config.for_command("analyze"), config.for_command("chat")
    console.print()
    if not analyze_values and not chat_values:
        console.print(Text("配置文件里还没有生效的配置项（模板中的项默认都是注释，去掉行首的 # 即可启用）。", style="muted"))
        return
    values = Table(box=glyphs.box, border_style="muted", header_style="accent.strong", show_edge=False, pad_edge=False)
    values.add_column("配置项", no_wrap=True)
    values.add_column("analyze", overflow="fold")
    values.add_column("chat", overflow="fold")
    for key in sorted(set(analyze_values) | set(chat_values)):
        values.add_row(key, str(analyze_values.get(key, "-")), str(chat_values.get(key, "-")))
    console.print(values)


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def _open_store(db: Path | None):
    import sqlite3

    from .sessions import SessionStore, default_db_path

    db_path = db.expanduser().resolve() if db else default_db_path()
    if not db_path.exists():
        console.print(Text("还没有任何会话。", style="muted"))
        raise typer.Exit()
    conn = sqlite3.connect(str(db_path))
    return conn, SessionStore(conn)


@sessions_app.command("list")
def sessions_list(db: Path = typer.Option(None, "--db", help="会话数据库文件路径")) -> None:
    """列出所有会话（按最近使用排序）。"""
    conn, store = _open_store(db)
    try:
        items = store.list()
    finally:
        conn.close()
    if not items:
        console.print(Text("还没有任何会话。", style="muted"))
        return

    from .render import shorten_path

    table = Table(box=glyphs.box, border_style="muted", header_style="accent.strong", show_edge=False, pad_edge=False)
    table.add_column("会话", style="ok", no_wrap=True)
    table.add_column("最近使用", style="muted", no_wrap=True)
    table.add_column("轮次", justify="right")
    table.add_column("tokens", justify="right", style="muted")
    table.add_column("日志", overflow="fold")
    table.add_column("首个问题", overflow="ellipsis", max_width=36)
    for item in items:
        logs = "\n".join(shorten_path(p, keep=2) for p in item.logs) or "-"
        table.add_row(item.name, item.updated_at, str(item.turns), f"{item.total_tokens:,}", logs, item.title or "-")
    console.print(table)
    console.print(Text.assemble(("用 ", "muted"), ("log-agent chat -l <日志> -s <会话>", "accent"), (" 续上对话", "muted")))


@sessions_app.command("rm")
def sessions_rm(
    names: list[str] = typer.Argument(..., help="要删除的会话名称，可传多个"),
    db: Path = typer.Option(None, "--db", help="会话数据库文件路径"),
) -> None:
    """删除会话及其全部对话记录。"""
    conn, store = _open_store(db)
    try:
        for name in names:
            if store.delete(name):
                console.print(Text.assemble((f"{glyphs.ok} 已删除 ", "ok"), (name, "accent")))
            else:
                console.print(Text(f"{glyphs.fail} 没有找到会话 {name}", style="warn"))
    finally:
        conn.close()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
