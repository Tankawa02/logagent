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


def _register_memory_app() -> None:
    from .memory_cli import memory_app

    app.add_typer(memory_app, name="memory")


_register_memory_app()

_EXIT_WORDS = {"exit", "quit", ":q", "退出", "结束", "/exit", "/quit"}
DEFAULT_MODEL = "openai:gpt-4.1"


class ReportFormat(StrEnum):
    markdown = "markdown"
    json = "json"


class FailOn(StrEnum):
    high = "high"
    medium = "medium"
    low = "low"


_CONFIDENCE_RANK = {"高": 3, "中": 2, "低": 1}
_FAIL_ON_RANK = {FailOn.high: 3, FailOn.medium: 2, FailOn.low: 1}
EXIT_FINDING = 3
EXIT_UNDECIDED = 4


class MemoryMode(StrEnum):
    suggest = "suggest"
    explicit = "explicit"
    off = "off"


# ---------------------------------------------------------------------------
# 公共选项
# ---------------------------------------------------------------------------

LOG_HELP = "日志文件路径，可重复传多个；支持通配符（如 'logs/app*.log'）、.gz 压缩日志，'-' 表示从管道读取"
CODE_HELP = "源码目录路径（可选，可重复传多个以同时分析多个代码库）"

_opt_code = typer.Option(None, "--code", "-c", help=CODE_HELP, exists=True, file_okay=False)
_opt_skills = typer.Option(
    None, "--skills",
    help="额外的 skill 目录（每个子目录一份 SKILL.md 排查手册），可重复传；"
    "~/.log-agent/skills 与项目内 .log-agent/skills 会自动加载",
    exists=True, file_okay=False,
)
_opt_model = typer.Option(None, "--model", "-m", help=f"模型，provider:model 格式；默认读 LOG_AGENT_MODEL，否则 {DEFAULT_MODEL}")
_opt_base_url = typer.Option(None, "--base-url", help="自定义 OpenAI 兼容接口地址；默认读环境变量 OPENAI_BASE_URL")
_opt_encoding = typer.Option(None, "--encoding", help="强制指定日志编码（如 gbk、utf-16）；默认自动探测")
_opt_no_redact = typer.Option(False, "--no-redact", help="关闭敏感信息脱敏（默认会打码 token、手机号、身份证、邮箱、IP 等）")
_opt_max_steps = typer.Option(120, "--max-steps", min=10, help="单轮最多推理步数，防止 agent 陷入反复搜索")
_opt_budget = typer.Option(
    None, "--budget",
    help="单轮 tokens 上限，如 200k、1.5m；用到 80% 时让 agent 停止取证、基于现有证据收尾出报告",
)
_opt_baseline = typer.Option(
    None, "--baseline",
    help="正常时段，如 '13:00~13:30'；agent 会先把它和问题时段（--since/--until，没有则为全文）做对比",
)
_opt_verbose = typer.Option(False, "--verbose", "-v", help="保留每一步工具调用（含结果摘要、耗时）的完整记录")
_opt_since = typer.Option(None, "--since", help="只分析该时间之后的日志，如 '2026-06-09 14:00' 或 '14:00'")
_opt_until = typer.Option(None, "--until", help="只分析到该时间为止（按给出的精度包含整段，'14:05' 含 14:05:59）")
_opt_memory = typer.Option(
    MemoryMode.suggest, "--memory",
    help="长期记忆：suggest 会从对话里提议值得记住的内容并请你确认；explicit 只记你明确要求的；off 关闭",
)


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


def _build_context_message(log_paths: list[str], code_paths: list[str], question: str, baseline=None) -> str:
    """把日志/源码路径和问题拼成给 agent 的首条消息。baseline 是 --baseline 解析出的 TimeWindow。"""
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
    if baseline:
        target = "上面的时间窗口" if window else "全文"
        lines.append(
            f"对比基线：用户给出的正常时段是 {baseline.describe()}。请先对每份日志调用 compare_windows"
            f"（baseline_since=\"{baseline.since.raw}\"，baseline_until=\"{baseline.until.raw}\"，"
            f"目标时段为{target}），再围绕新出现和明显增多的错误深入排查。"
        )
    lines.append(f"\n用户问题：{question}")
    return "\n".join(lines)


def _skill_sources(skills: list[Path] | None):
    from .skills import resolve_skill_sources

    return resolve_skill_sources(skills or [])


def _skills_value(sources) -> Text:
    value = Text()
    for i, source in enumerate(sources):
        if i:
            value.append("\n")
        value.append(f"{source.skill_count()} 个", style="accent")
        value.append(f"  {source.directory}", style="muted")
    return value


def _base_rows(
    log_paths: list[str], code_paths: list[str], model: str, base_url: str | None, skill_sources=(),
) -> list[tuple[str, Text | str]]:
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
    if skill_sources:
        rows.append(("Skills", _skills_value(skill_sources)))

    from .timefilter import default_window

    if default_window():
        rows.append(("时间", Text(default_window().describe(), style="accent")))
    rows.append(("脱敏", Text("开启", style="ok") if redact.is_enabled() else Text("已关闭", style="warn")))

    from .config import loaded

    if loaded().files:
        rows.append(("配置", Text("\n".join(str(p) for p in loaded().files), style="muted")))
    return rows


def _make_budget(text: str | None):
    if not text:
        return None
    from .budget import TokenBudget, parse_budget

    try:
        return TokenBudget(parse_budget(text))
    except ValueError as exc:
        _fail(str(exc))


def _parse_baseline(text: str | None):
    if not text:
        return None
    from .compare import parse_range

    try:
        return parse_range(text)
    except ValueError as exc:
        _fail(f"--baseline {exc}")


def _extra_rows(rows: list, budget, baseline) -> None:
    if baseline:
        rows.append(("基线", Text(baseline.describe(), style="accent")))
    if budget is not None:
        from .budget import format_tokens

        rows.append(("预算", Text.assemble(
            (f"{format_tokens(budget.limit)} tokens", "accent"),
            (f"  单轮，用到 {format_tokens(budget.threshold)} 时收尾", "muted"),
        )))


def _finding_exit_code(result, fail_on: FailOn) -> int:
    """--fail-on：发现问题且可信度达到门槛时返回 3；报告里没有一句话结论、无法判定时返回 4。"""
    if result.finding is None:
        console.print(Text(f"{glyphs.notice} 报告里没有一句话结论，无法判定是否发现问题（退出码 4）。", style="warn"))
        return EXIT_UNDECIDED
    if not result.finding:
        return 0
    rank = _CONFIDENCE_RANK.get(result.confidence, 1)
    return EXIT_FINDING if rank >= _FAIL_ON_RANK[fail_on] else 0


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
    budget: str = _opt_budget,
    baseline: str = _opt_baseline,
    fail_on: FailOn = typer.Option(
        None, "--fail-on", case_sensitive=False,
        help="发现问题且可信度不低于该级别时以退出码 3 结束（high / medium / low），便于接入 CI 与定时巡检",
    ),
    verbose: bool = _opt_verbose,
    skills: list[Path] = _opt_skills,
    memory: MemoryMode = _opt_memory,
) -> None:
    """单次分析日志，结合源码定位根因（一问一答）。"""
    _check_api_key()
    token_budget = _make_budget(budget)
    baseline_window = _parse_baseline(baseline)
    log_paths, code_paths = _prepare(log, code, encoding, no_redact, since, until)
    model = _resolve_model(model)
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    skill_sources = _skill_sources(skills)

    import sys

    from .agent import build_agent
    from .memory_cli import after_turn, end_session, open_session, status_row

    mem = open_session(memory.value, code_paths, "analyze-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    # 管道运行或输出到文件时不弹确认，候选留到下次 chat / log-agent memory review 再处理
    can_ask = sys.stdin.isatty() and sys.stdout.isatty() and output is None and "-" not in log
    try:
        reset_cursor_line()
        rows = _base_rows(log_paths, code_paths, model, base_url, skill_sources)
        _extra_rows(rows, token_budget, baseline_window)
        memory_row = status_row(mem, "analyze")
        if memory_row:
            rows.append(memory_row)
        console.print(info_panel(rows, "log-agent", f"v{__version__}"))
        with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
            agent = build_agent(
                model=model, base_url=base_url, skill_dirs=skills or [], memory=mem, budget=token_budget,
            )

        content = _build_context_message(log_paths, code_paths, question, baseline_window)
        payload = {"messages": [{"role": "user", "content": content}]}
        from .citations import CitationLinker

        linker = CitationLinker(log_paths, code_paths)
        result = StreamRenderer(verbose=verbose, linker=linker, budget=token_budget).run(
            agent, payload, config=_run_config(max_steps)
        )

        if output:
            from .export import build_payload, infer_format, write_report

            data = build_payload(result, question=question, logs=log_paths, code=code_paths, model=model)
            saved = write_report(output, data, infer_format(output, fmt.value if fmt else None))
            console.print(Text.assemble((f"{glyphs.ok} 报告已保存 ", "ok"), (str(saved), "accent")))

        after_turn(mem, question, ask=False)
        if can_ask:
            end_session(mem)
    finally:
        if mem is not None:
            mem.store.close()

    if result.interrupted:
        raise typer.Exit(code=130)
    if result.error:
        raise typer.Exit(code=1)
    if fail_on is not None:
        code = _finding_exit_code(result, fail_on)
        if code:
            raise typer.Exit(code=code)


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


def _stored_session(db_path: Path, name: str | None, latest: bool):
    """查出要续上的会话；--resume 且没有任何会话时直接报错，-s 指定的会话不存在时返回 None（按新会话处理）。"""
    import sqlite3

    from .sessions import SessionStore

    if not db_path.is_file():
        info = None
    else:
        conn = sqlite3.connect(str(db_path))
        try:
            store = SessionStore(conn)
            info = store.get(name) if name else store.latest()
        finally:
            conn.close()
    if info is None and latest and not name:
        _fail("还没有可以续上的会话，请先用 -l 指定日志开始一次对话。")
    if info is None and name and latest:
        _fail(f"没有名为 '{name}' 的会话，可以用 log-agent sessions list 查看已有会话。")
    return info


def _show_suggestions(log_paths: list[str]) -> list[str]:
    """新会话开场时列出候选问题（本地统计，不耗 tokens）；扫描出错或没有错误日志时静默跳过。"""
    from .suggest import suggest_questions

    try:
        with console.status(Text("正在扫描日志里的高频错误…", style="muted"), spinner=glyphs.spinner):
            questions = suggest_questions(log_paths)
    except Exception:
        return []
    if not questions:
        return []
    console.print(Text("日志里的高频错误，输入编号直接提问，也可以自己输入问题：", style="muted"))
    for i, question in enumerate(questions, start=1):
        console.print(Text.assemble((f"  {i}  ", "accent"), (question, "")))
    return questions


def _add_sources(command: str, arg: str, log_paths: list[str], code_paths: list[str]) -> list[str] | None:
    """解析 /add-log、/add-code 的参数，返回新增的绝对路径；参数有误时打印原因并返回 None。"""
    from .inputs import LogInputError, resolve_log_inputs

    target = arg.strip().strip('"').strip("'")
    usage = "/add-log <日志路径或通配符>" if command == "/add-log" else "/add-code <源码目录>"
    if not target:
        console.print(Text(f"用法：{usage}", style="warn"))
        return None
    if command == "/add-log":
        if target == "-":
            console.print(Text("chat 模式不能从管道追加日志，请先把日志保存成文件。", style="warn"))
            return None
        try:
            resolved = resolve_log_inputs([target])
        except LogInputError as exc:
            console.print(Text(f"{glyphs.fail} {exc}", style="err"))
            return None
        return [p for p in dict.fromkeys(resolved) if p not in log_paths]
    path = Path(target).expanduser().resolve()
    if not path.is_dir():
        console.print(Text(f"{glyphs.fail} 源码目录不存在：{path}", style="err"))
        return None
    return [] if str(path) in code_paths else [str(path)]


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
    log: list[str] = typer.Option(
        None, "--log", "-l",
        help=LOG_HELP.replace("，'-' 表示从管道读取", "") + "；续上已有会话时可省略，沿用上次的日志",
    ),
    code: list[Path] = _opt_code,
    model: str = _opt_model,
    base_url: str = _opt_base_url,
    session: str = typer.Option(
        None, "--session", "-s",
        help="会话名称；用相同名称可续上之前的对话。不指定时自动生成（形如 chat-20260609-165130）",
    ),
    resume: bool = typer.Option(False, "--resume", "-r", help="续上最近一次会话，沿用它的日志与源码"),
    db: Path = typer.Option(None, "--db", help="会话数据库文件路径（默认 ~/.log-agent/sessions.db）"),
    since: str = _opt_since,
    until: str = _opt_until,
    encoding: str = _opt_encoding,
    no_redact: bool = _opt_no_redact,
    max_steps: int = _opt_max_steps,
    budget: str = _opt_budget,
    baseline: str = _opt_baseline,
    verbose: bool = _opt_verbose,
    skills: list[Path] = _opt_skills,
    memory: MemoryMode = _opt_memory,
) -> None:
    """多轮对话模式：连续追问，会话持久化到本地 SQLite，关掉终端后还能续上。"""
    token_budget = _make_budget(budget)
    baseline_window = _parse_baseline(baseline)
    if log and "-" in log:
        _fail("chat 模式需要在终端里输入问题，不能用 -l - 从管道读日志；请先把日志保存成文件，或改用 analyze。")
    from .sessions import default_db_path

    db_path = db.expanduser().resolve() if db else default_db_path()
    reused_sources = False
    if resume or (session and not log):
        stored = _stored_session(db_path, session, resume)
        if stored is not None:
            session = stored.name
            if not log:
                missing = [p for p in stored.logs if not Path(p).is_file()]
                if missing:
                    _fail(f"会话 '{stored.name}' 上次使用的日志已不存在：{missing[0]}\n请用 -l 重新指定日志文件。")
                log, reused_sources = list(stored.logs), True
                code = code or [Path(p) for p in stored.code]
    if not log:
        _fail("请用 -l 指定日志文件；续上已有会话时可以只写 -s <会话名>，或用 --resume 续上最近一次。")
    _check_api_key()
    log_paths, code_paths = _prepare(log, code, encoding, no_redact, since, until)
    model = _resolve_model(model)
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    skill_sources = _skill_sources(skills)

    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from .agent import build_agent
    from .chat_input import ChatInput
    from .citations import CitationLinker
    from .clipboard import ClipboardError, copy_text
    from .export import build_payload, write_report
    from .memory_cli import after_turn, end_session, handle_slash, open_session, status_row
    from .sessions import SessionStore, describe_source_change

    auto_session = session is None
    session = session or _new_session_name()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    linker = CitationLinker(log_paths, code_paths)

    mem = open_session(memory.value, code_paths, session)
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
            if reused_sources:
                session_value.append("  沿用上次的日志与源码", style="muted")
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
        rows = _base_rows(log_paths, code_paths, model, base_url, skill_sources)
        _extra_rows(rows, token_budget, baseline_window)
        rows.append(("会话", session_value))
        memory_row = status_row(mem, "chat")
        if memory_row:
            rows.append(memory_row)
        reset_cursor_line()
        console.print(info_panel(rows, "log-agent", f"多轮对话 {glyphs.sep} v{__version__}", footer))

        checkpointer = SqliteSaver(conn)
        with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
            agent = build_agent(
                model=model, checkpointer=checkpointer, base_url=base_url, skill_dirs=skills or [], memory=mem,
                budget=token_budget,
            )

        def has_history(name: str) -> bool:
            try:
                return checkpointer.get({"configurable": {"thread_id": name}}) is not None
            except Exception:
                return False

        first_turn = not has_history(session)
        if not first_turn:
            console.print(Text(f"{glyphs.ok} 已加载会话 '{session}' 的历史，可直接继续追问。", style="ok"))
            if source_note:
                console.print(Text(f"{glyphs.notice} 日志/源码与上次不同，会在下一条消息里告知 agent。", style="warn"))
        store.touch(session, log_paths, code_paths, model)

        suggestions: list[str] = []
        if first_turn:
            suggestions = _show_suggestions(log_paths)

        chat_input = ChatInput(db_path.parent / "history")
        totals = _ChatTotals()
        last: tuple[str, TurnResult] | None = None
        last_question: str | None = None
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

            retry_prefix = ""
            if suggestions and user_input.isdigit() and 1 <= int(user_input) <= len(suggestions):
                user_input = suggestions[int(user_input) - 1]
                console.print(Text.assemble((f"{glyphs.notice} ", "accent"), (user_input, "muted")))
            elif lowered == "/retry" or lowered.startswith("/retry "):
                if last_question is None:
                    console.print(Text("还没有可以重答的问题。", style="muted"))
                    first_prompt = True
                    continue
                extra = user_input[len("/retry"):].strip()
                user_input = last_question
                retry_prefix = "请重新回答我上一个问题，重新核实证据，不要直接沿用上一次的结论。"
                if extra:
                    retry_prefix += f"\n补充要求：{extra}"

            if user_input.startswith("/"):
                command, _, arg = user_input.partition(" ")
                command = command.lower()
                first_prompt = True
                if handle_slash(mem, command, arg):
                    continue
                if command == "/help":
                    _print_help()
                elif command == "/sources":
                    rows = _base_rows(log_paths, code_paths, model, base_url, skill_sources)
                    console.print(info_panel(rows, "当前会话", session))
                elif command == "/stats":
                    console.print(totals.line())
                elif command == "/copy":
                    if last is None:
                        console.print(Text("还没有可以复制的回答。", style="muted"))
                        continue
                    try:
                        method = copy_text(last[1].report.strip() + "\n")
                    except ClipboardError as exc:
                        console.print(Text(f"{glyphs.fail} 复制失败：{exc}，可以改用 /save 保存成文件。", style="err"))
                        continue
                    note = "（通过终端 OSC 52 写入，需终端支持）" if method == "OSC 52" else ""
                    console.print(Text(f"{glyphs.ok} 已复制上一条回答{note}", style="ok"))
                elif command in ("/add-log", "/add-code"):
                    added = _add_sources(command, arg, log_paths, code_paths)
                    if added is None:
                        continue
                    kind = "日志文件" if command == "/add-log" else "源码目录"
                    if not added:
                        console.print(Text(f"这个{kind}已经在当前会话里了。", style="muted"))
                        continue
                    if command == "/add-log":
                        log_paths = log_paths + added
                    else:
                        code_paths = code_paths + added
                    linker = CitationLinker(log_paths, code_paths)
                    store.touch(session, log_paths, code_paths, model)
                    if not first_turn:
                        note = f"补充：我新增了{kind}，之后排查可以一并使用：\n" + "\n".join(f"  - {p}" for p in added)
                        source_note = f"{source_note}\n\n{note}" if source_note else note
                    for path in added:
                        console.print(Text.assemble((f"{glyphs.ok} 已追加{kind} ", "ok"), (path, "accent")))
                    if not first_turn:
                        console.print(Text("会在下一条消息里告知 agent。", style="muted"))
                elif command == "/new":
                    end_session(mem)
                    last_question = None
                    session = _new_session_name()
                    if mem is not None:
                        mem.session = session
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
                message = _build_context_message(log_paths, code_paths, user_input, baseline_window)
                first_turn = False
            elif source_note:
                message = f"{source_note}\n\n{user_input}"
            else:
                message = user_input
            if retry_prefix:
                message = f"{retry_prefix}\n\n{message}"
            source_note = ""
            suggestions = []
            last_question = user_input

            payload = {"messages": [{"role": "user", "content": message}]}
            result = StreamRenderer(verbose=verbose, linker=linker, budget=token_budget).run(
                agent, payload, config=_run_config(max_steps, session)
            )
            totals.add(result)
            store.record_turn(session, user_input, result.usage.get("total", 0))
            if result.report:
                last = (user_input, result)
            if result.interrupted:
                console.print(Text("本轮回答已中断，可以继续追问或换个问题。", style="muted"))
            after_turn(mem, user_input)
        end_session(mem)
    finally:
        conn.close()
        if mem is not None:
            mem.store.close()

    console.print()
    if totals.turns:
        console.print(totals.line())
    console.print(Text.assemble(("已退出，会话已保存。下次用 ", "muted"), (f"-s {session}", "accent"), (" 续上。", "muted")))


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


@app.command()
def watch(
    log: list[str] = typer.Option(..., "--log", "-l", help="要盯着的日志文件，可多次指定（不支持 gzip 和管道）"),
    code: list[Path] = _opt_code,
    pattern: str = typer.Option(None, "--pattern", "-p", help="触发分析的正则；默认是 ERROR / FATAL 级别的行"),
    question: str = typer.Option(
        "这批新出现的错误是什么原因？请定位根因并给出修复建议。", "--question", "-q", help="每次触发时问 agent 的问题",
    ),
    debounce: float = typer.Option(10.0, "--debounce", min=1, help="新错误停止出现多少秒后开始分析，把一波错误攒到一起"),
    cooldown: float = typer.Option(120.0, "--cooldown", min=0, help="两次分析之间至少间隔多少秒，避免持续报错时反复消耗"),
    once: bool = typer.Option(False, "--once", help="分析一次后退出：适合复现一次问题、看完结果就走"),
    model: str = _opt_model,
    base_url: str = _opt_base_url,
    encoding: str = _opt_encoding,
    no_redact: bool = _opt_no_redact,
    max_steps: int = _opt_max_steps,
    budget: str = _opt_budget,
    verbose: bool = _opt_verbose,
    skills: list[Path] = _opt_skills,
) -> None:
    """追踪模式：像 tail -F 一样盯着日志，出现新的错误时自动分析这一批。"""
    if "-" in log:
        _fail("watch 需要跟随真实文件，不能用 -l - 从管道读取。")
    _check_api_key()
    token_budget = _make_budget(budget)
    log_paths, code_paths = _prepare(log, code, encoding, no_redact)
    model = _resolve_model(model)
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    skill_sources = _skill_sources(skills)

    import time

    from .watch import POLL_INTERVAL, FollowError, LogFollower, TriggerBatch, batch_question, build_matcher

    try:
        matcher = build_matcher(pattern)
        followers = [LogFollower(path) for path in log_paths]
    except FollowError as exc:
        _fail(str(exc))

    from .agent import build_agent
    from .citations import CitationLinker

    rows = _base_rows(log_paths, code_paths, model, base_url, skill_sources)
    rule = Text(f"正则 {pattern}", style="accent") if pattern else Text("ERROR / FATAL 级别", style="accent")
    rule.append(f"  攒 {debounce:g} 秒 {glyphs.sep} 间隔至少 {cooldown:g} 秒", style="muted")
    rows.append(("触发", rule))
    _extra_rows(rows, token_budget, None)
    reset_cursor_line()
    console.print(info_panel(rows, "log-agent", f"追踪模式 {glyphs.sep} v{__version__}"))
    with console.status(Text("正在加载模型与工具…", style="muted"), spinner=glyphs.spinner):
        agent = build_agent(model=model, base_url=base_url, skill_dirs=skills or [], budget=token_budget)
    linker = CitationLinker(log_paths, code_paths)

    def waiting() -> None:
        console.print(Text(
            f"{glyphs.notice} 正在监控新写入的日志，出现新错误时自动分析 {glyphs.sep} Ctrl+C 退出", style="muted",
        ))

    batch = TriggerBatch(debounce=debounce, max_wait=max(60.0, debounce * 3))
    last_run = float("-inf")
    announced = False
    analyses = 0
    waiting()
    try:
        while True:
            now = time.monotonic()
            for follower in followers:
                rotations = follower.rotations
                for lineno, line in follower.poll():
                    if matcher(line):
                        batch.add(str(follower.path), lineno, line, now)
                if follower.rotations != rotations:
                    console.print(Text(f"{glyphs.notice} {follower.path.name} 被截断或轮转，已从头继续跟随", style="warn"))
            if batch.count and not announced:
                announced = True
                console.print(Text(
                    f"{glyphs.notice} 发现新错误，再等 {debounce:g} 秒看是否还有后续…", style="warn",
                ))
            if batch.ready(now) and now - last_run >= cooldown:
                hits = batch.drain()
                announced = False
                console.print()
                console.rule(Text(f"{datetime.now():%H:%M:%S}  新增 {hits.count} 条", style="accent"), style="muted")
                content = _build_context_message(
                    log_paths, code_paths, batch_question(hits, question, matched_by_pattern=bool(pattern)),
                )
                result = StreamRenderer(verbose=verbose, linker=linker, budget=token_budget).run(
                    agent, {"messages": [{"role": "user", "content": content}]}, config=_run_config(max_steps),
                )
                analyses += 1
                last_run = time.monotonic()
                if once:
                    break
                if result.interrupted:
                    console.print(Text("本次分析已中断，继续监控；再按 Ctrl+C 退出。", style="muted"))
                console.print()
                waiting()
            time.sleep(POLL_INTERVAL)
    except KeyboardInterrupt:
        console.print()
        console.print(Text(f"已停止监控，共分析 {analyses} 次。", style="muted"))


# ---------------------------------------------------------------------------
# config
# ---------------------------------------------------------------------------

_CONFIG_TEMPLATE = """\
# log-agent 项目配置：命令行没写的参数从这里取默认值（命令行 > 环境变量 > 本文件）。
# 相对路径以本文件所在目录为准。

# model = "openai:gpt-4.1"
# base_url = "https://your-gateway.example.com/v1"   # API key 仍放在环境变量 OPENAI_API_KEY
# code = ["./"]
# skills = ["./ops/skills"]   # 额外的 skill 目录；.log-agent/skills 与 ~/.log-agent/skills 会自动加载
# max_steps = 120
# budget = "300k"      # 单轮 tokens 上限，用到 80% 时自动收尾出报告
# timeout = 120        # 单次模型请求超时（秒）
# max_retries = 3      # 模型请求失败自动重试次数
# encoding = "gbk"
# no_redact = false
# memory = "suggest"   # 长期记忆：suggest 提议并请你确认 / explicit 只记你明确要求的 / off 关闭

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
def sessions_list(
    db: Path = typer.Option(None, "--db", help="会话数据库文件路径"),
    search: str = typer.Option(None, "--search", "-S", help="按会话名、首个问题或日志路径筛选（不区分大小写）"),
) -> None:
    """列出所有会话（按最近使用排序）。"""
    conn, store = _open_store(db)
    try:
        items = store.list()
    finally:
        conn.close()
    if search:
        keyword = search.casefold()
        items = [
            item for item in items
            if keyword in " ".join([item.name, item.title or "", *item.logs]).casefold()
        ]
        if not items:
            console.print(Text(f"没有匹配 '{search}' 的会话。", style="muted"))
            return
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
    console.print(Text.assemble(("用 ", "muted"), ("log-agent chat -s <会话>", "accent"), (" 续上对话（沿用上次的日志与源码），", "muted"),
            ("--resume", "accent"), (" 续上最近一次", "muted")))


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
