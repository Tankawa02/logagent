"""`log-agent experience`：查看、确认、淘汰排查经验，把稳定的经验提升为 skill（不需要日志和 API Key）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import typer
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .experience import (
    ORIGIN_LABELS,
    STATUS_LABELS,
    Experience,
    ExperienceStore,
    default_experience_path,
    promote_to_skill,
)
from .memory import project_key
from .term import console, glyphs

experience_app = typer.Typer(help="管理排查经验（从过往排查里提炼的可复用策略）。", no_args_is_help=True)

_opt_project = typer.Option(
    None, "--code", "-c", help="只看全局与这个源码目录所在项目的经验（默认列出全部）", exists=True, file_okay=False,
)
_opt_db = typer.Option(None, "--db", help="经验库路径（默认 ~/.log-agent/experience.db）")


def _open(db: Path | None) -> ExperienceStore:
    try:
        return ExperienceStore(db or default_experience_path())
    except (sqlite3.Error, OSError) as exc:
        console.print(Text(f"{glyphs.fail} 打开经验库失败：{exc}", style="err"))
        raise typer.Exit(code=1) from exc


def _entries(store: ExperienceStore, code: Path | None, status: str | tuple[str, ...] | None) -> list[Experience]:
    if code is None:
        return store.list(all_projects=True, status=status)
    return store.list(project_key([str(code)]), status=status)


def print_entries(entries: list[Experience]) -> None:
    if not entries:
        console.print(Text("没有符合条件的经验。", style="muted"))
        return
    table = Table(box=None, pad_edge=False, show_edge=False, header_style="muted")
    for column in ("编号", "状态", "范围", "经验", "有用 / 有害", "来源"):
        table.add_column(column, overflow="fold")
    for entry in entries:
        body = Text(entry.title, style="bold")
        body.append(f"\n适用：{entry.trigger or '通用'}", style="muted")
        body.append(f"\n做法：{entry.strategy}")
        status = STATUS_LABELS.get(entry.status, entry.status)
        if entry.promotable:
            status += "（可提升）"
        table.add_row(f"E{entry.id}", status, entry.scope_label, body, f"{entry.helpful} / {entry.harmful}",
                      ORIGIN_LABELS.get(entry.origin, entry.origin))
    console.print(table)


def _ids(items: list[str]) -> list[int]:
    ids = []
    for item in items:
        raw = item.strip().lstrip("Ee")
        if not raw.isdigit():
            console.print(Text(f"{glyphs.fail} 编号不合法：{item}", style="err"))
            raise typer.Exit(code=2)
        ids.append(int(raw))
    return ids


def _set_status(db: Path | None, items: list[str], status: str, verb: str) -> None:
    store = _open(db)
    try:
        for experience_id in _ids(items):
            entry = store.set_status(experience_id, status)
            if entry is None:
                console.print(Text(f"{glyphs.fail} 没有编号 E{experience_id} 的经验", style="err"))
            else:
                console.print(Text(f"{glyphs.ok} 已{verb} E{entry.id}：{entry.title}", style="ok"))
    finally:
        store.close()


@experience_app.command("list")
def experience_list(
    code: Path = _opt_project,
    status: str = typer.Option(None, "--status", help="只看某种状态：candidate / active / retired / promoted"),
    db: Path = _opt_db,
) -> None:
    """列出经验（默认列出候选与生效的）。"""
    if status and status not in STATUS_LABELS:
        raise typer.BadParameter(f"--status 只能是 {' / '.join(STATUS_LABELS)}")
    store = _open(db)
    try:
        print_entries(_entries(store, code, status or ("candidate", "active")))
    finally:
        store.close()


@experience_app.command("review")
def experience_review(code: Path = _opt_project, db: Path = _opt_db) -> None:
    """逐条确认候选经验：y 生效、n 删除、s 跳过。"""
    store = _open(db)
    try:
        candidates = _entries(store, code, "candidate")
        if not candidates:
            console.print(Text("没有待确认的候选经验。", style="muted"))
            return
        for entry in candidates:
            print_entries([entry])
            choice = Prompt.ask("生效这条经验？", choices=["y", "n", "s"], default="s", console=console)
            if choice == "y":
                store.set_status(entry.id, "active")
                console.print(Text(f"{glyphs.ok} 已生效 E{entry.id}", style="ok"))
            elif choice == "n":
                store.remove(entry.id)
                console.print(Text(f"已删除 E{entry.id}", style="muted"))
    finally:
        store.close()


@experience_app.command("accept")
def experience_accept(ids: list[str] = typer.Argument(..., help="候选经验编号，可传多个"), db: Path = _opt_db) -> None:
    """让候选经验生效（之后的排查会参考它）。"""
    _set_status(db, ids, "active", "生效")


@experience_app.command("retire")
def experience_retire(ids: list[str] = typer.Argument(..., help="经验编号，可传多个"), db: Path = _opt_db) -> None:
    """淘汰经验（保留记录，不再注入）。"""
    _set_status(db, ids, "retired", "淘汰")


@experience_app.command("rm")
def experience_rm(ids: list[str] = typer.Argument(..., help="经验编号，可传多个"), db: Path = _opt_db) -> None:
    """彻底删除经验。"""
    store = _open(db)
    try:
        for experience_id in _ids(ids):
            entry = store.remove(experience_id)
            if entry is None:
                console.print(Text(f"{glyphs.fail} 没有编号 E{experience_id} 的经验", style="err"))
            else:
                console.print(Text(f"{glyphs.ok} 已删除 E{entry.id}：{entry.title}", style="ok"))
    finally:
        store.close()


@experience_app.command("add")
def experience_add(
    title: str = typer.Argument(..., help="一句话标题"),
    strategy: str = typer.Option(..., "--strategy", "-s", help="具体做法"),
    trigger: str = typer.Option("通用", "--trigger", "-t", help="适用条件，默认通用"),
    code: Path = typer.Option(None, "--code", "-c", help="只对这个源码目录所在的项目生效（默认全局）",
                              exists=True, file_okay=False),
    db: Path = _opt_db,
) -> None:
    """手动添加一条经验，直接生效。"""
    store = _open(db)
    try:
        project = project_key([str(code)]) if code else None
        try:
            entry, created = store.add(title, trigger, strategy, project, status="active", origin="manual")
        except ValueError as exc:
            console.print(Text(f"{glyphs.fail} 没有保存：{exc}", style="err"))
            raise typer.Exit(code=2) from exc
        verb = "已添加" if created else "与已有经验相似，已合并到"
        console.print(Text(f"{glyphs.ok} {verb} E{entry.id}：{entry.title}", style="ok"))
    finally:
        store.close()


@experience_app.command("promote")
def experience_promote(
    experience_id: str = typer.Argument(..., help="经验编号"),
    skills_dir: Path = typer.Option(None, "--skills-dir", help="写到哪个 skill 目录（默认 ~/.log-agent/skills）"),
    force: bool = typer.Option(False, "--force", help="有用票不够时也提升"),
    db: Path = _opt_db,
) -> None:
    """把长期有用的经验写成 skill（SKILL.md），之后由 skill 机制按需加载，不再占用经验段。"""
    from .skills import user_skills_dir

    store = _open(db)
    try:
        entry = store.get(_ids([experience_id])[0])
        if entry is None:
            console.print(Text(f"{glyphs.fail} 没有编号 {experience_id} 的经验", style="err"))
            raise typer.Exit(code=2)
        if not entry.promotable and not force:
            console.print(Text(f"{glyphs.fail} E{entry.id} 还不满足提升条件（生效、有用 ≥5 次且没有有害票），"
                               "确认要提升可以加 --force", style="err"))
            raise typer.Exit(code=2)
        path = promote_to_skill(entry, skills_dir or user_skills_dir())
        store.set_status(entry.id, "promoted")
        console.print(Text(f"{glyphs.ok} 已写入 {path}，E{entry.id} 标记为已提升，不再注入经验段", style="ok"))
    finally:
        store.close()


@experience_app.command("path")
def experience_path() -> None:
    """显示经验库文件位置。"""
    console.print(str(default_experience_path()))
