"""CLI 入口：log-agent analyze / chat / sessions"""

from __future__ import annotations

import os
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import typer
from rich.table import Table
from rich.text import Text

from . import __version__
from .chat_session import ChatSession
from .chat_session import new_session_name as _new_session_name
from .cli_context import _base_rows, _build_context_message, _run_config, _skill_sources
from .render import StreamRenderer, info_panel
from .report import ReportView
from .term import console, glyphs, reset_cursor_line

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
_opt_encoding = typer.Option(None, "--encoding", envvar="LOG_AGENT_ENCODING", help="强制指定日志编码（如 gbk、utf-16）；默认自动探测")
_opt_timezone = typer.Option(
    "UTC", "--timezone", envvar="LOG_AGENT_TIMEZONE",
    help="无时区日志和时间边界使用的时区，如 +08:00；默认 UTC，已有偏移保持不变",
)
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
    from .config import (
        COMMANDS,
        ConfigError,
        apply_log_settings,
        apply_to_environment,
        cli_defaults,
        load_config,
        set_loaded,
    )

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
    try:
        apply_log_settings(values)
    except ConfigError as exc:
        _fail(str(exc))
    apply_to_environment(values)
    existing = dict(ctx.default_map or {})
    existing[command] = {**cli_defaults(values), **existing.get(command, {})}
    ctx.default_map = existing


def _check_api_key() -> None:
    from .onboarding import print_key_hints

    if os.environ.get("OPENAI_API_KEY"):
        return
    console.print(Text("缺少 OPENAI_API_KEY 环境变量。", style="bold err"))
    print_key_hints()
    console.print(Text.assemble(("  或运行 ", "muted"), ("log-agent init", "accent"), (" 按步骤引导配置。", "muted")))
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


def _configure_timezone(value: str) -> None:
    from .timefilter import set_default_timezone

    try:
        set_default_timezone(value)
    except ValueError as exc:
        _fail(str(exc))


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
    """--fail-on：发现问题且可信度达到门槛时返回 3；缺少有效结构化判定时返回 4。"""
    if result.finding is None:
        console.print(Text(f"{glyphs.notice} 报告缺少有效结构化判定或状态为 unknown，无法判定是否发现问题（退出码 4）。", style="warn"))
        return EXIT_UNDECIDED
    if not result.finding:
        return 0
    rank = _CONFIDENCE_RANK.get(result.confidence, 1)
    check = getattr(result, "evidence_check", None)
    if check is not None and not check.get("verified") and not check.get("shifted"):
        console.print(Text(f"{glyphs.notice} 报告的证据均未通过原文核对，无法据此判定发现问题（退出码 4）。", style="warn"))
        return EXIT_UNDECIDED
    check = check or {}
    if check.get("mismatch"):
        # 有证据与原文不符：自动化门槛按降一级的可信度判断，报告本身的可信度不改
        rank -= 1
        console.print(Text(f"{glyphs.notice} 有 {check['mismatch']} 条证据与原文不符，--fail-on 按降一级的可信度判断。", style="warn"))
    return EXIT_FINDING if rank >= _FAIL_ON_RANK[fail_on] else 0


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
    view: ReportView = typer.Option(ReportView.detailed, "--view", help="导出用途：brief 速览 / detailed 详细分析 / ticket 工单"),
    model: str = _opt_model,
    base_url: str = _opt_base_url,
    since: str = _opt_since,
    until: str = _opt_until,
    encoding: str = _opt_encoding,
    timezone: str = _opt_timezone,
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
    _configure_timezone(timezone)
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

            data = build_payload(
                result, question=question, logs=log_paths, code=code_paths, model=model,
                settings={"since": since, "until": until, "timezone": timezone, "baseline": baseline,
                          "encoding": encoding, "budget": budget, "max_steps": max_steps, "no_redact": no_redact},
            )
            saved = write_report(output, data, infer_format(output, fmt.value if fmt else None), view.value)
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


@app.command()
def chat(
    ctx: typer.Context,
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
    timezone: str = _opt_timezone,
    no_redact: bool = _opt_no_redact,
    max_steps: int = _opt_max_steps,
    budget: str = _opt_budget,
    baseline: str = _opt_baseline,
    verbose: bool = _opt_verbose,
    skills: list[Path] = _opt_skills,
    memory: MemoryMode = _opt_memory,
) -> None:
    """多轮对话模式：连续追问，会话持久化到本地 SQLite，关掉终端后还能续上。"""
    if log and "-" in log:
        _fail("chat 模式需要在终端里输入问题，不能用 -l - 从管道读日志；请先把日志保存成文件，或改用 analyze。")
    from .sessions import default_db_path

    db_path = db.expanduser().resolve() if db else default_db_path()
    reused_sources = False
    stored = None
    if resume or session:
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
    from .chat_state import SETTING_KEYS, legacy_last_turn, restored_value

    saved_settings = stored.settings if stored else {}
    settings = dict(zip(SETTING_KEYS, (since, until, timezone, baseline, encoding, budget, max_steps), strict=True))
    settings = {key: restored_value(ctx, key, value, saved_settings) for key, value in settings.items()}
    since, until, timezone, baseline, encoding, budget, max_steps = (settings[key] for key in SETTING_KEYS)
    if stored:
        model = restored_value(ctx, "model", model, {"model": stored.model})
    _configure_timezone(timezone)
    token_budget = _make_budget(budget)
    baseline_window = _parse_baseline(baseline)
    _check_api_key()
    log_paths, code_paths = _prepare(log, code, encoding, no_redact, since, until)
    model = _resolve_model(model)
    base_url = base_url or os.environ.get("OPENAI_BASE_URL")
    skill_sources = _skill_sources(skills)

    import sqlite3

    from langgraph.checkpoint.sqlite import SqliteSaver

    from .agent import build_agent
    from .chat_input import ChatInput
    from .chat_loop import run_chat_loop
    from .citations import CitationLinker
    from .memory_cli import end_session, open_session, status_row
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
        if previous:
            source_note += "\n本次有效分析设置如下，替代之前的时间范围与基线（未设置的表示不限或取消）：\n"
            source_note += _build_context_message(log_paths, code_paths, "继续排查", baseline_window)
            source_note += f"\n范围：{since or '开头'} → {until or '结尾'}；基线：{baseline or '未设置'}。"

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
                console.print(Text(f"{glyphs.notice} 下一次提问会同步当前来源与分析设置。", style="muted"))
        store.touch(session, log_paths, code_paths, model, settings)

        suggestions: list[str] = []
        if first_turn:
            suggestions = _show_suggestions(log_paths)

        chat_input = ChatInput(db_path.parent / "history")
        last = store.last_turn(session)
        if last is None and not first_turn:
            # get_state 会重建增量 checkpoint，直接读取 SQLite checkpoint 可能没有 messages。
            last = legacy_last_turn(agent.get_state(_run_config(max_steps, session)).values, model)
        last_question = last["question"] if last else None
        if last:
            console.print(Text(f"上次问题：{last_question}", style="muted"))
            console.print(Text(f"上次结果（{last['status']}）：{last.get('summary') or last['report'][:300] or '未生成回答'}", style="muted"))
            if last.get("provenance") == "legacy_unknown":
                console.print(Text("旧版会话未保存逐轮来源，恢复的报告将标注来源未知。", style="warn"))
        state = ChatSession(
            store=store, session=session, log_paths=log_paths, code_paths=code_paths,
            model=model, settings=settings, linker=linker, mem=mem, base_url=base_url,
            skill_sources=skill_sources, no_redact=no_redact, baseline_window=baseline_window,
            first_turn=first_turn, source_note=source_note, suggestions=suggestions,
            last=last, last_question=last_question,
        )
        run_chat_loop(state, chat_input, agent, verbose=verbose, token_budget=token_budget, max_steps=max_steps)
        end_session(mem)
    finally:
        conn.close()
        if mem is not None:
            mem.store.close()

    console.print()
    if state.totals.turns:
        console.print(state.totals.line())
    console.print(Text.assemble(("已退出，会话已保存。下次用 ", "muted"), (f"-s {state.session}", "accent"), (" 续上。", "muted")))


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
    timezone: str = _opt_timezone,
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
    _configure_timezone(timezone)
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


@app.command("inspect")
def inspect_logs(
    log: list[str] = typer.Option(..., "--log", "-l", help=LOG_HELP),
    since: str = _opt_since,
    until: str = _opt_until,
    timezone: str = _opt_timezone,
    encoding: str = _opt_encoding,
    no_redact: bool = _opt_no_redact,
) -> None:
    """本地检查日志格式、识别覆盖率、窗口和样例，不调用模型、不需要 API Key。"""
    from .diagnostics import inspect_log
    from .timefilter import default_window

    _configure_timezone(timezone)
    paths, _ = _prepare(log, None, encoding, no_redact, since, until)
    console.print(Text(f"本地预检查 · 时区 {timezone} · 不调用模型", style="muted"))
    failed = False
    for path in paths:
        try:
            with console.status("正在本地扫描日志…", spinner=glyphs.spinner):
                report = inspect_log(path, default_window())
            console.print(Text(report))
        except (OSError, EOFError) as exc:
            failed = True
            console.print(Text(f"读取失败：{path}：{exc}", style="err"))
    if failed:
        raise typer.Exit(1)


@app.command()
def doctor(
    command: str = typer.Option("analyze", "--command", help="检查 analyze / chat / watch / inspect 的有效配置"),
    ping: bool = typer.Option(
        False, "--ping",
        help="真正向模型服务发一次最小请求，验证 Key、网关和模型名是否可用（消耗极少量 token）",
    ),
    ping_timeout: float = typer.Option(20.0, "--ping-timeout", min=1, help="--ping 的超时秒数，不自动重试"),
) -> None:
    """检查有效配置、凭据是否设置和依赖兼容性；加 --ping 时再真正连一次模型服务。"""
    from .config import COMMANDS, ConfigError
    from .diagnostics import configuration_checks, dependency_checks, effective_config, safe_config_value

    if command not in COMMANDS:
        _fail("--command 必须是 analyze、chat、watch 或 inspect")
    try:
        config, values, origins = effective_config(app, command)
    except (ConfigError, ValueError, TypeError) as exc:
        _fail(str(exc))
    mode = "会发送一次最小请求验证模型服务" if ping else "不连接模型服务（加 --ping 实际验证 Key 和网关）"
    console.print(Text(f"诊断 · {command} · {mode}", style="muted"))
    for path in config.files:
        console.print(Text(f"配置文件：{path}"))
    for warning in config.warnings:
        console.print(Text(warning, style="warn"))
    table = Table("配置项", "有效值", "来源", box=glyphs.box)
    for key, value in values.items():
        table.add_row(key, safe_config_value(key, value), origins[key])
    console.print(table)
    checks = configuration_checks(values) + dependency_checks()
    for ok, message in checks:
        console.print(Text(f"{glyphs.ok if ok else glyphs.fail} {message}", style="ok" if ok else "err"))
    local_failed = any(not ok for ok, _ in checks)
    ping_failed = ping and not _doctor_ping(values, ping_timeout)
    if local_failed:
        console.print(Text("请修正配置；依赖缺失或版本不匹配时运行 uv sync。", style="warn"))
    if ping_failed:
        console.print(Text("按上面的提示检查 OPENAI_API_KEY、网关地址（--base-url / OPENAI_BASE_URL）和模型名（-m / LOG_AGENT_MODEL）。",
                           style="warn"))
    if local_failed or ping_failed:
        raise typer.Exit(1)


def _doctor_ping(values: dict, timeout: float) -> bool:
    """doctor --ping：用有效配置里的模型和接口发一次最小请求。返回是否通过。"""
    from .probe import api_key, endpoint_label
    from .probe import ping as probe_ping

    if "model" not in values:
        console.print(Text("该命令不调用模型，跳过 --ping。", style="muted"))
        return True
    if not api_key():
        console.print(Text(f"{glyphs.fail} 未设置 OPENAI_API_KEY，无法连接模型服务", style="err"))
        return False
    model, base_url = values["model"], values.get("base_url")
    with console.status("正在向模型服务发送最小请求…", spinner=glyphs.spinner):
        result = probe_ping(model, base_url, timeout=timeout)
    where = f"{model} @ {endpoint_label(base_url)}"
    if not result.ok:
        console.print(Text(f"{glyphs.fail} 模型服务不可用（{where}）：{result.message}", style="err"))
        return False
    served = f" · 实际模型 {result.served_model}" if result.served_model else ""
    console.print(Text(f"{glyphs.ok} 模型服务可用（{where}）· {result.latency:.2f}s{served} · {result.message}",
                       style="ok"))
    return True


@app.command()
def init(
    log: list[str] = typer.Option(None, "--log", "-l", help="用来抽样识别格式的日志，可重复传、支持通配符；不传时自动找当前目录的 *.log"),
    model: str = typer.Option(None, "--model", "-m", help="模型，provider:model 格式；不传时交互选择"),
    base_url: str = typer.Option(None, "--base-url", help="OpenAI 兼容网关地址；带凭据的地址不会写进配置"),
    code: list[Path] = typer.Option(None, "--code", "-c", help=CODE_HELP, exists=True, file_okay=False),
    timezone: str = typer.Option(None, "--timezone", help="日志时间不带时区时使用的时区；不传时按抽样结果询问"),
    ping: bool = typer.Option(None, "--ping/--no-ping", help="生成前发一次最小请求验证 Key（默认：交互模式下询问，--yes 时不验证）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="不提问，全部使用检测到的默认值（适合脚本 / CI）"),
    force: bool = typer.Option(False, "--force", help="覆盖已有的 .log-agent.toml"),
) -> None:
    """引导配置：检查 Key、选模型、抽样日志识别格式，生成 .log-agent.toml（Key 不会写进文件）。"""
    from .onboarding import run_init

    code_exit = run_init(logs=log, model=model, base_url=base_url, code=code, timezone=timezone, ping=ping,
                         yes=yes, force=force)
    if code_exit:
        raise typer.Exit(code_exit)


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
# timezone = "+08:00"      # 无偏移日志和时间边界的时区，默认 UTC
# no_redact = false
# memory = "suggest"   # 长期记忆：suggest 提议并请你确认 / explicit 只记你明确要求的 / off 关闭

# [analyze]            # 只对 analyze 生效
# verbose = true

# [chat]               # 只对 chat 生效
# db = "~/.log-agent/sessions.db"
"""


@app.command("config")
def show_config(
    init: bool = typer.Option(False, "--init", help="在当前目录生成一份带注释的 .log-agent.toml 模板（想要引导式配置用 log-agent init）"),
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
    search: str = typer.Option(None, "--search", "-S", help="按会话名、日志路径、历史问题或回答搜索（不区分大小写）"),
) -> None:
    """列出所有会话（按最近使用排序）。"""
    conn, store = _open_store(db)
    try:
        items = store.search(search) if search else store.list()
    finally:
        conn.close()
    if search and not items:
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


# ---------------------------------------------------------------------------
# serve：可选的 Web 界面
# ---------------------------------------------------------------------------

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _web_agent_factory(*, model: str, checkpointer, base_url: str | None, budget):
    # 调用时再取 build_agent，测试里替换 agent.build_agent 就能换成脚本化模型
    from . import agent as agent_module

    return agent_module.build_agent(
        model=model, checkpointer=checkpointer, base_url=base_url, skill_dirs=[], memory=None, budget=budget,
    )


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="监听地址；要让同事打开分享链接可用 0.0.0.0（务必保留访问令牌）"),
    port: int = typer.Option(8765, "--port", "-p", min=1, max=65535, help="监听端口"),
    db: Path = typer.Option(None, "--db", help="会话数据库文件路径（默认与 chat 相同：~/.log-agent/sessions.db）"),
    base_url: str = _opt_base_url,
    token: str = typer.Option(
        None, "--token", envvar="LOG_AGENT_WEB_TOKEN",
        help="本人访问令牌；默认每次启动随机生成。分享链接不受影响，各自独立",
    ),
    no_token: bool = typer.Option(False, "--no-token", help="不校验访问令牌（只允许监听本机地址时使用）"),
    public_url: str = typer.Option(
        None, "--public-url", help="生成分享链接用的外部地址，如 https://logs.example.com（经反向代理访问时设置）",
    ),
    read_only: bool = typer.Option(False, "--read-only", help="只读：不提供网页续问，不需要 API Key"),
    no_redact: bool = typer.Option(
        False, "--no-redact", help="本人视图显示未脱敏的日志原文（分享链接始终脱敏）",
    ),
    open_browser: bool = typer.Option(False, "--open", help="启动后自动打开浏览器"),
) -> None:
    """启动 Web 界面：错误时间线、报告与证据左右对照、网页续问、会话分享链接。"""
    try:
        import uvicorn

        from .web.app import STATIC_DIR, WebConfig, create_app
    except ImportError:
        _fail(
            "Web 界面需要额外依赖：uv tool install --reinstall 'log-agent[web] @ git+https://github.com/yourorg/log-agent.git'"
            "（本地开发：uv sync --extra web）"
        )
    import secrets

    from .config import ConfigError, apply_log_settings, apply_to_environment, load_config, set_loaded
    from .sessions import default_db_path

    # serve 读 chat 的配置：自定义日志格式影响时间线解析，db / base_url 默认值与 chat 保持一致
    try:
        config = load_config()
        set_loaded(config)
        values = config.for_command("chat")
        apply_log_settings(values)
        apply_to_environment(values)
    except ConfigError as exc:
        _fail(str(exc))
    for warning in config.warnings:
        console.print(Text(f"{glyphs.fail} {warning}", style="warn"))
    if no_token and host not in _LOOPBACK_HOSTS:
        _fail("--no-token 只能和本机地址一起用：监听其他地址时，任何能访问到端口的人都能读你的日志与源码。")

    db_value = db or (Path(values["db"]) if values.get("db") else None)
    db_path = db_value.expanduser().resolve() if db_value else default_db_path()
    base_url = base_url or values.get("base_url") or os.environ.get("OPENAI_BASE_URL")
    can_chat = not read_only and bool(os.environ.get("OPENAI_API_KEY"))
    access = None if no_token else (token or secrets.token_urlsafe(18))
    web = WebConfig(
        db_path=db_path, token=access, agent_factory=None if read_only else _web_agent_factory,
        base_url=base_url, can_chat=can_chat, public_url=public_url.rstrip("/") if public_url else None,
        redact_owner=not no_redact, loopback=host in _LOOPBACK_HOSTS,
    )

    shown_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{'[' + shown_host + ']' if ':' in shown_host else shown_host}:{port}/"
    open_url = url + (f"?token={access}" if access else "")
    rows = [
        ("地址", Text(open_url, style="accent")),
        ("会话库", Text(str(db_path) + ("" if db_path.exists() else "  (尚不存在，先用 chat 产生会话)"), style="muted")),
        ("续问", Text("可用" if can_chat else ("只读模式" if read_only else "不可用：缺少 OPENAI_API_KEY"),
                      style="ok" if can_chat else "warn")),
        ("脱敏", Text("本人视图关闭，分享链接仍脱敏" if no_redact else "开启", style="warn" if no_redact else "muted")),
    ]
    if not (STATIC_DIR / "index.html").is_file():
        rows.append(("前端", Text("未找到构建产物：cd web && npm ci && npm run build", style="warn")))
    footer = [Text("把地址里的令牌当作密码，不要发给别人；交接用页面里的「分享」生成只读链接。", style="muted")]
    if host not in _LOOPBACK_HOSTS:
        footer.append(Text(f"{glyphs.notice} 正在监听 {host}，局域网内能访问到该端口的人都能看到登录页。", style="warn"))
    console.print(info_panel(rows, "log-agent serve", f"Web 界面 {glyphs.sep} v{__version__}", footer))

    if open_browser:
        import webbrowser

        webbrowser.open(open_url)
    uvicorn.run(create_app(web), host=host, port=port, log_level="warning")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
