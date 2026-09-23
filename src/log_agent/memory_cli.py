"""记忆的终端交互：启动面板状态、候选确认、chat 斜杠命令、`log-agent memory` 子命令。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from rich.table import Table
from rich.text import Text

from .memory import (
    FEATURE_HINT_AFTER_SESSIONS,
    KIND_LABELS,
    Candidate,
    Memory,
    MemorySession,
    MemoryStore,
    default_memory_path,
    default_scope,
    guess_kind,
    project_key,
    project_label,
)
from .term import console, glyphs

memory_app = typer.Typer(help="管理长期记忆（表达偏好、术语、项目事实）。", no_args_is_help=True)

_REMEMBER_USAGE = "用法：/remember [-g] <内容>（-g 保存为全局，否则按内容归到本项目或全局）"
_MEMORY_USAGE = "用法：/memory [list | review | rm <编号…> | edit <编号> <新内容>]"


def open_session(mode: str, code_paths: list[str], session: str) -> MemorySession | None:
    if mode == "off":
        return None
    try:
        store = MemoryStore(default_memory_path())
    except (sqlite3.Error, OSError) as exc:
        console.print(Text(f"{glyphs.fail} 记忆库不可用，本次不使用记忆：{exc}", style="warn"))
        return None
    return MemorySession(store=store, mode=mode, project=project_key(code_paths), session=session)


def status_row(mem: MemorySession | None, command: str) -> tuple[str, Text] | None:
    """启动面板里的"记忆"一行；顺带累计运行次数，决定要不要给一次功能提示。"""
    if mem is None:
        return None
    store = mem.store
    try:
        runs = int(store.meta("runs", "0") or 0) + 1
        store.set_meta("runs", str(runs))
        memories = mem.memories()
        pending = store.pending(mem.project)
        hint = not memories and runs >= FEATURE_HINT_AFTER_SESSIONS and not store.meta("feature_hint_shown")
        if hint:
            store.set_meta("feature_hint_shown", "1")
    except sqlite3.Error:
        return None

    value = Text()
    if memories:
        shared = sum(1 for m in memories if m.project is None)
        value.append(f"{len(memories)} 条", style="accent")
        if mem.project:
            value.append(f"  全局 {shared} {glyphs.sep} {project_label(mem.project)} {len(memories) - shared}", style="muted")
    else:
        value.append("0 条", style="muted")
        if hint:
            how = '说"记住……"或输入 /remember 保存常用信息' if command == "chat" else '在 chat 里说"记住……"，或用 log-agent memory add'
            value.append(f"  {how}", style="accent")
    if mem.mode == "explicit":
        value.append("  只记你明确要求的", style="muted")
    if pending:
        how = "输入 /memory review 处理" if command == "chat" else "用 log-agent memory review 处理"
        value.append(f"\n{len(pending)} 条建议待确认，{how}", style="warn")
    return "记忆", value


def after_turn(mem: MemorySession | None, user_input: str, *, ask: bool = True) -> None:
    """一轮结束：登记候选；用户明确要求记住或纠正了 agent 时马上确认。"""
    if mem is None:
        return
    try:
        immediate = mem.finish_turn(user_input)
    except sqlite3.Error as exc:
        console.print(Text(f"{glyphs.fail} 记录记忆建议失败：{exc}", style="warn"))
        return
    if ask:
        confirm(mem, immediate)


def end_session(mem: MemorySession | None) -> None:
    """会话结束：统一问一次在本会话里再次出现、达到确认条件的候选。"""
    if mem is None:
        return
    try:
        due = mem.session_due()
    except sqlite3.Error:
        return
    confirm(mem, due)


# ---------------------------------------------------------------------------
# 候选确认
# ---------------------------------------------------------------------------


def _candidate_line(candidate: Candidate) -> Text:
    return Text.assemble(
        ("  ", ""),
        (f"[{project_label(candidate.project)} {glyphs.sep} {KIND_LABELS.get(candidate.kind, candidate.kind)}] ", "muted"),
        (candidate.text, "accent"),
        (f"  （{candidate.reason}）", "muted"),
    )


def confirm(mem: MemorySession, candidates: list[Candidate], *, force: bool = False) -> None:
    """逐条请用户确认。force=False 时跳过本会话已经问过的候选。Ctrl+C / 输入结束视为"稍后"。"""
    todo = [c for c in candidates if force or c.id not in mem.asked]
    if not todo:
        return
    store = mem.store
    console.print()
    console.print(Text(f"有 {len(todo)} 条内容可能值得记住：", style="accent.strong"))
    for candidate in todo:
        mem.asked.add(candidate.id)
        console.print(_candidate_line(candidate))
        default = "y" if candidate.signal == "explicit" else "s"
        prompt = Text.assemble(
            ("  保存？", ""),
            ("[y] 是  [n] 否  [e] 编辑  [N] 不再提示  [s] 稍后", "muted"),
            (f"（回车 = {default}）", "muted"),
            (" ", ""),
        )
        try:
            answer = console.input(prompt).strip() or default
            if answer.lower() == "e":
                edited = console.input(Text("  改成：", style="muted")).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print(Text("  已跳过，之后可以用 /memory review 处理。", style="muted"))
            return
        try:
            if answer == "N":
                store.reject(candidate, permanent=True)
                console.print(Text("  好的，以后不再提示这条。", style="muted"))
            elif answer.lower() in ("y", "yes", "是"):
                _saved(store.accept(candidate))
            elif answer.lower() == "e":
                if edited:
                    _saved(store.accept(candidate, edited))
                else:
                    console.print(Text("  内容为空，已跳过。", style="muted"))
            elif answer.lower() in ("n", "no", "否"):
                store.reject(candidate, permanent=False)
                console.print(Text("  好的，不保存。", style="muted"))
            else:
                console.print(Text("  稍后可以用 /memory review 处理。", style="muted"))
        except sqlite3.Error as exc:
            console.print(Text(f"  {glyphs.fail} 保存失败：{exc}", style="err"))


def _saved(memory: Memory, updated: bool = False) -> None:
    verb = "已更新" if updated else "已记住"
    console.print(
        Text.assemble(
            (f"  {glyphs.ok} {verb} ", "ok"),
            (f"#{memory.id}", "accent"),
            (f"  {memory.scope_label} {glyphs.sep} {KIND_LABELS.get(memory.kind, memory.kind)}", "muted"),
        )
    )


# ---------------------------------------------------------------------------
# chat 斜杠命令
# ---------------------------------------------------------------------------


def handle_slash(mem: MemorySession | None, command: str, arg: str) -> bool:
    """处理 /remember、/memory、/forget；不是这几个命令时返回 False。"""
    if command not in ("/remember", "/memory", "/forget"):
        return False
    if mem is None:
        console.print(Text("记忆已关闭（memory = off），可以在配置文件或 --memory 里打开。", style="muted"))
        return True
    try:
        if command == "/remember":
            _remember(mem.store, arg, mem.project)
        elif command == "/forget":
            _forget(mem.store, arg.split())
        else:
            sub, _, rest = arg.strip().partition(" ")
            sub = sub.lower()
            if sub in ("", "list", "ls"):
                print_memories(mem.memories(), mem.project)
            elif sub == "review":
                pending = mem.store.pending(mem.project)
                if pending:
                    confirm(mem, pending, force=True)
                else:
                    console.print(Text("没有待确认的建议。", style="muted"))
            elif sub in ("rm", "del", "forget"):
                _forget(mem.store, rest.split())
            elif sub == "edit":
                _edit(mem.store, rest)
            elif sub == "add":
                _remember(mem.store, rest, mem.project)
            else:
                console.print(Text(_MEMORY_USAGE, style="warn"))
    except sqlite3.Error as exc:
        console.print(Text(f"{glyphs.fail} 记忆库操作失败：{exc}", style="err"))
    return True


def _remember(store: MemoryStore, arg: str, project: str | None, kind: str | None = None) -> None:
    text = arg.strip()
    force_global = False
    for flag in ("-g ", "--global "):
        if text.startswith(flag):
            force_global, text = True, text[len(flag):].strip()
    if not text:
        console.print(Text(_REMEMBER_USAGE, style="warn"))
        return
    kind = kind or guess_kind(text)
    scope = None if force_global else default_scope(kind, project)
    memory, updated = store.add(text, kind, scope, origin="explicit")
    _saved(memory, updated)


def _parse_ids(items: list[str]) -> list[int] | None:
    ids = []
    for item in items:
        item = item.lstrip("#")
        if not item.isdigit():
            return None
        ids.append(int(item))
    return ids or None


def _forget(store: MemoryStore, items: list[str]) -> None:
    ids = _parse_ids(items)
    if ids is None:
        console.print(Text("用法：/forget <编号…>，编号可以用 /memory 查看", style="warn"))
        return
    removed = False
    for memory_id in ids:
        if store.remove(memory_id):
            removed = True
            console.print(Text.assemble((f"{glyphs.ok} 已删除 ", "ok"), (f"#{memory_id}", "accent")))
        else:
            console.print(Text(f"{glyphs.fail} 没有编号为 #{memory_id} 的记忆", style="warn"))
    if removed:
        console.print(
            Text("下一轮起不再使用；本会话之前的对话里 agent 已经见过它，要完全不受影响可以 /new 开新会话。", style="muted")
        )


def _edit(store: MemoryStore, arg: str) -> None:
    head, _, text = arg.strip().partition(" ")
    ids = _parse_ids([head]) if head else None
    if ids is None or not text.strip():
        console.print(Text("用法：/memory edit <编号> <新内容>", style="warn"))
        return
    memory = store.update(ids[0], text)
    if memory is None:
        console.print(Text(f"{glyphs.fail} 没有编号为 #{ids[0]} 的记忆", style="warn"))
    else:
        _saved(memory, updated=True)


def print_memories(memories: list[Memory], project: str | None = None) -> None:
    if not memories:
        console.print(Text('还没有记忆。说"记住……"或用 /remember <内容> 保存。', style="muted"))
        return
    table = Table(box=glyphs.box, border_style="muted", header_style="accent.strong", show_edge=False, pad_edge=False)
    table.add_column("#", justify="right", style="accent", no_wrap=True)
    table.add_column("范围", style="muted", no_wrap=True)
    table.add_column("类型", no_wrap=True)
    table.add_column("内容", overflow="fold")
    for memory in memories:
        scope = memory.scope_label
        if memory.project is not None and memory.project == project:
            scope += "（本项目）"
        table.add_row(str(memory.id), scope, KIND_LABELS.get(memory.kind, memory.kind), memory.text)
    console.print(table)


# ---------------------------------------------------------------------------
# log-agent memory 子命令（不需要日志和 API Key）
# ---------------------------------------------------------------------------

_opt_project = typer.Option(
    None, "--code", "-c", help="按这个源码目录所在的项目过滤 / 归类（默认：列出全部；新增时只存全局）",
    exists=True, file_okay=False,
)


def _open_store() -> MemoryStore:
    try:
        return MemoryStore(default_memory_path())
    except (sqlite3.Error, OSError) as exc:
        console.print(Text(f"{glyphs.fail} 打开记忆库失败：{exc}", style="err"))
        raise typer.Exit(code=1) from exc


def _project_of(code: Path | None) -> str | None:
    return project_key([str(code)]) if code else None


@memory_app.command("list")
def memory_list(code: Path = _opt_project) -> None:
    """列出记忆；传 -c 时只列全局与该项目的。"""
    store = _open_store()
    try:
        project = _project_of(code)
        print_memories(store.memories(project, all_projects=project is None), project)
        pending = store.pending(project, all_projects=project is None)
        if pending:
            console.print(Text.assemble(
                (f"另有 {len(pending)} 条建议待确认，用 ", "muted"), ("log-agent memory review", "accent"), (" 处理", "muted"),
            ))
    finally:
        store.close()


@memory_app.command("add")
def memory_add(
    text: str = typer.Argument(..., help="要记住的内容"),
    code: Path = _opt_project,
    global_: bool = typer.Option(False, "--global", "-g", help="保存为全局记忆（对所有项目生效）"),
    kind: str = typer.Option(None, "--kind", "-k", help="类型：preference / term / fact；默认按内容推断"),
) -> None:
    """保存一条记忆。"""
    if kind is not None and kind not in KIND_LABELS:
        console.print(Text(f"{glyphs.fail} --kind 只能是 {' / '.join(KIND_LABELS)}", style="err"))
        raise typer.Exit(code=2)
    store = _open_store()
    try:
        _remember(store, ("-g " if global_ else "") + text, _project_of(code), kind)
    finally:
        store.close()


@memory_app.command("rm")
def memory_rm(ids: list[str] = typer.Argument(..., help="要删除的记忆编号，可传多个")) -> None:
    """删除记忆。"""
    store = _open_store()
    try:
        _forget(store, ids)
    finally:
        store.close()


@memory_app.command("edit")
def memory_edit(
    memory_id: int = typer.Argument(..., help="记忆编号"),
    text: str = typer.Argument(..., help="新内容"),
) -> None:
    """修改一条记忆的内容。"""
    store = _open_store()
    try:
        _edit(store, f"{memory_id} {text}")
    finally:
        store.close()


@memory_app.command("review")
def memory_review(code: Path = _opt_project) -> None:
    """逐条确认待定的记忆建议。"""
    store = _open_store()
    try:
        project = _project_of(code)
        pending = store.pending(project, all_projects=project is None)
        if not pending:
            console.print(Text("没有待确认的建议。", style="muted"))
            return
        confirm(MemorySession(store=store, mode="suggest", project=project, session="review"), pending, force=True)
    finally:
        store.close()
