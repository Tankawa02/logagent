"""斜杠命令注册表：同一份定义驱动分发、帮助和输入补全。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from rich.table import Table
from rich.text import Text

from .chat_session import ChatSession
from .chat_session import new_session_name as _new_session_name
from .chat_state import remove_log
from .cli_context import _base_rows, _build_context_message
from .render import info_panel
from .term import console, glyphs


@dataclass(frozen=True)
class CommandResult:
    """命令默认停留在输入框；只有 retry 提交问题，exit 结束循环。"""

    question: str | None = None
    retry_prefix: str = ""
    exit: bool = False


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    handler: Callable[[ChatSession, str, str], CommandResult | None]


COMMANDS: dict[str, CommandSpec] = {}


Handler = Callable[[ChatSession, str, str], CommandResult | None]


def register(name: str, description: str) -> Callable[[Handler], Handler]:
    def decorate(handler: Handler) -> Handler:
        if name in COMMANDS:
            raise ValueError(f"重复注册命令：{name}")
        COMMANDS[name] = CommandSpec(name, description, handler)
        return handler

    return decorate


def dispatch(state: ChatSession, text: str) -> CommandResult:
    command, _, arg = text.partition(" ")
    command = command.lower()
    spec = COMMANDS.get(command)
    if spec is None:
        console.print(Text(f"未知命令 {command}，输入 /help 查看可用命令。", style="warn"))
        return CommandResult()
    return spec.handler(state, command, arg) or CommandResult()


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
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="accent", no_wrap=True)
    grid.add_column(style="muted")
    for spec in COMMANDS.values():
        grid.add_row(spec.name, spec.description)
    grid.add_row("Ctrl+C", "回答中按下只中断当前这一轮；在输入框按下退出")
    console.print(grid)


@register("/help", "显示可用命令")
def show_help(state: ChatSession, command: str, arg: str) -> None:
    _print_help()


@register("/history", "列出当前会话历史，/history [关键词] 搜索问题和回答")
def history(state: ChatSession, command: str, arg: str) -> None:
    entries = state.store.history(state.session, arg.strip())
    table = Table("轮次", "时间", "状态 / 异常判定", "问题", "结论", box=glyphs.box)
    for number, entry in entries:
        assessment = (entry.get("analysis") or {}).get("assessment", "unknown")
        table.add_row(
            str(number),
            Text(entry.get("generated_at") or "未知"),
            Text(f"{entry.get('status', '未知')} / {assessment}"),
            Text(entry.get("question", "")),
            Text(entry.get("summary") or "未保存结论"),
        )
    console.print(table if entries else Text("没有匹配的历史报告。", style="muted"))
    console.print(
        Text("轮次按原会话编号；旧版未保存的轮次无法查看。/show N 查看，/save --turn N 导出。", style="muted")
    )


@register("/show", "查看指定轮次的完整报告，/show 3")
def show_report(state: ChatSession, command: str, arg: str) -> None:
    from .chat_state import parse_turn_number

    try:
        number = parse_turn_number(arg.strip())
    except ValueError:
        console.print(Text("用法：/show <正整数轮次>", style="warn"))
        return
    entry = state.store.turn(state.session, number)
    if entry is None:
        console.print(Text("该轮次不存在或未保存报告。输入 /history 查看可用轮次。", style="warn"))
        return
    from rich.markdown import Markdown

    from .export import to_markdown

    console.print(Markdown(to_markdown(entry)))


@register("/settings", "查看有效模型、时区、时间范围、基线和预算")
def show_settings(state: ChatSession, command: str, arg: str) -> None:
    rows = [(key, str(value) if value is not None else "未设置") for key, value in state.settings.items()]
    rows.extend([("model", state.model), ("脱敏", "关闭" if state.no_redact else "开启")])
    console.print(info_panel(rows, "有效分析设置", state.session))


@register("/baseline", "调整正常时段，/baseline 13:00~13:30；/baseline off 取消")
@register("/window", "调整时间窗口，/window 14:00~14:30；/window off 取消")
def set_window(state: ChatSession, command: str, arg: str) -> None:
    from .compare import parse_range
    from .timefilter import TimeWindow, set_default_window

    raw = arg.strip()
    if not raw:
        console.print(Text(f"用法：{command} <起点~终点|off>（off 取消）", style="warn"))
        return
    try:
        window = TimeWindow() if raw.lower() == "off" else parse_range(raw)
    except ValueError as exc:
        console.print(Text(str(exc), style="warn"))
        return
    if command == "/window":
        state.settings["since"] = window.since.raw if window.since else None
        state.settings["until"] = window.until.raw if window.until else None
        set_default_window(state.settings["since"], state.settings["until"])
    else:
        state.baseline_window = window if window else None
        state.settings["baseline"] = raw if window else None
    state.persist()
    state.suggestions = []
    state.source_note = (
        "分析设置已更新，请用以下范围重新核实，之前的范围和基线不再适用。\n"
        + _build_context_message(state.log_paths, state.code_paths, "继续排查", state.baseline_window)
        + f"\n范围：{state.settings['since'] or '开头'} → {state.settings['until'] or '结尾'}；基线：{state.settings['baseline'] or '未设置'}。"
    )
    console.print(Text(f"{glyphs.ok} 已更新，下一次提问生效；已生成的报告保持原设置。", style="ok"))


@register("/remove-log", "移除会话中的日志，/remove-log <完整路径或唯一文件名>（不删除文件）")
def remove_source(state: ChatSession, command: str, arg: str) -> None:
    try:
        state.log_paths = remove_log(state.log_paths, arg)
    except ValueError as exc:
        console.print(Text(str(exc), style="warn"))
        return
    state.refresh_sources()
    state.source_note = "日志列表已更新，移出的日志不再作为本轮证据。当前来源和范围如下：\n" + _build_context_message(
        state.log_paths, state.code_paths, "继续排查", state.baseline_window
    )
    console.print(Text(f"{glyphs.ok} 已从会话移除日志，原文件未删除。", style="ok"))


@register("/sources", "查看当前会话使用的日志与源码")
def show_sources(state: ChatSession, command: str, arg: str) -> None:
    rows = _base_rows(state.log_paths, state.code_paths, state.model, state.base_url, state.skill_sources)
    console.print(info_panel(rows, "当前会话", state.session))


@register("/stats", "查看本次运行的累计用量")
def show_stats(state: ChatSession, command: str, arg: str) -> None:
    console.print(state.totals.line())


@register("/copy", "把上一条回答复制到剪贴板（Markdown 原文）")
def copy_report(state: ChatSession, command: str, arg: str) -> None:
    from .clipboard import ClipboardError, copy_text

    if state.last is None or not state.last.get("report"):
        console.print(Text("还没有可以复制的回答。", style="muted"))
        return
    try:
        method = copy_text(state.last["report"].strip() + "\n")
    except ClipboardError as exc:
        console.print(Text(f"{glyphs.fail} 复制失败：{exc}，可以改用 /save 保存成文件。", style="err"))
        return
    note = "（通过终端 OSC 52 写入，需终端支持）" if method == "OSC 52" else ""
    console.print(Text(f"{glyphs.ok} 已复制上一条回答{note}", style="ok"))


@register("/add-code", "给当前会话追加源码目录，/add-code <目录>")
@register("/add-log", "给当前会话追加日志文件，/add-log <路径或通配符>")
def add_sources(state: ChatSession, command: str, arg: str) -> None:
    added = _add_sources(command, arg, state.log_paths, state.code_paths)
    if added is None:
        return
    kind = "日志文件" if command == "/add-log" else "源码目录"
    if not added:
        console.print(Text(f"这个{kind}已经在当前会话里了。", style="muted"))
        return
    if command == "/add-log":
        state.log_paths = state.log_paths + added
    else:
        state.code_paths = state.code_paths + added
        if state.mem is not None:
            from .memory import project_key

            state.mem.project = project_key(state.code_paths)
    state.refresh_sources()
    if not state.first_turn:
        note = f"补充：我新增了{kind}，之后排查可以一并使用：\n" + "\n".join(f"  - {p}" for p in added)
        state.source_note = f"{state.source_note}\n\n{note}" if state.source_note else note
    for path in added:
        console.print(Text.assemble((f"{glyphs.ok} 已追加{kind} ", "ok"), (path, "accent")))
    if not state.first_turn:
        console.print(Text("会在下一条消息里告知 agent。", style="muted"))


@register("/new", "开一个新会话（当前会话已保存，可用 -s 续上）")
def new_session(state: ChatSession, command: str, arg: str) -> None:
    from .memory_cli import end_session

    end_session(state.mem)
    state.last_question = None
    state.last = None
    state.suggestions = []
    state.session = _new_session_name()
    if state.mem is not None:
        state.mem.session = state.session
    state.first_turn = True
    state.source_note = ""
    state.persist()
    console.print(Text.assemble((f"{glyphs.ok} 已切换到新会话 ", "ok"), (state.session, "accent")))


@register("/save-ticket", "保存上一轮工单，/save-ticket [--turn 轮次] [路径]")
@register("/save-brief", "保存上一轮速览，/save-brief [--turn 轮次] [路径]")
@register("/save", "保存报告，/save [--turn 轮次] [路径]，默认上一轮")
def save_report(state: ChatSession, command: str, arg: str) -> None:
    from .chat_state import parse_turn_selection
    from .export import write_report

    try:
        number, path_arg = parse_turn_selection(arg)
    except ValueError as exc:
        console.print(Text(str(exc), style="warn"))
        return
    data = state.store.turn(state.session, number) if number is not None else state.last
    if data is None:
        console.print(
            Text("该轮次不存在或未保存报告。" if number is not None else "还没有可以保存的回答。", style="warn")
        )
        return
    view = {"/save-brief": "brief", "/save-ticket": "ticket"}.get(command, "detailed")
    turn_label = f"-turn-{number}" if number is not None else ""
    default_name = f"log-agent-report-{view}{turn_label}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
    target = Path(path_arg) if path_arg else Path.cwd() / default_name
    fmt = "json" if target.suffix.lower() == ".json" else "markdown"
    try:
        saved = write_report(target, data, fmt, view)
    except OSError as exc:
        console.print(Text(f"{glyphs.fail} 保存失败: {exc}", style="err"))
        return
    console.print(Text.assemble((f"{glyphs.ok} 已保存 ", "ok"), (str(saved), "accent")))


@register("/retry", "重新回答上一个问题，可附补充要求，/retry [补充]")
def retry(state: ChatSession, command: str, arg: str) -> CommandResult | None:
    if state.last_question is None:
        console.print(Text("还没有可以重答的问题。", style="muted"))
        return None
    prefix = "请重新回答我上一个问题，重新核实证据，不要直接沿用上一次的结论。"
    if arg.strip():
        prefix += f"\n补充要求：{arg.strip()}"
    return CommandResult(question=state.last_question, retry_prefix=prefix)


@register("/forget", "删除记忆，/forget <编号…>")
@register("/memory", "查看记忆；/memory review 处理待确认的建议，/memory edit <编号> <新内容> 修改")
@register("/remember", "记住一条偏好或项目知识，/remember [-g] <内容>，-g 表示全局")
def memory_command(state: ChatSession, command: str, arg: str) -> None:
    from .memory_cli import handle_slash

    handle_slash(state.mem, command, arg)


@register("/quit", "退出")
@register("/exit", "退出")
def exit_chat(state: ChatSession, command: str, arg: str) -> CommandResult:
    return CommandResult(exit=True)
