"""网页里的 skill 与记忆管理接口（仅本人）。

- Skill：列出各来源目录下的 `<名字>/SKILL.md`，查看、编辑、新建、删除。保存前按 deepagents 的规则校验 frontmatter，
  不合规的手册会被 SkillsMiddleware 静默跳过，所以这里直接拒绝保存并说明原因。
- 记忆：与 `log-agent memory` 子命令同一个库（~/.log-agent/memory.db），列出全部范围的记忆与待确认建议，
  支持新增、编辑、删除、采纳 / 拒绝建议。新增或采纳时发现相似记忆，先把相似项返回给前端让用户选「新增」还是「替换」。
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..memory import (
    KIND_LABELS,
    Candidate,
    Memory,
    MemoryStore,
    default_memory_path,
    project_key,
    project_label,
)
from ..skills import SKILL_FILE, find_project_skills_dir, user_skills_dir

MAX_SKILL_BYTES = 512 * 1024
MAX_SKILL_FILES = 200
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", re.DOTALL)


# ---------------------------------------------------------------------------
# Skill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ManagedSource:
    key: str
    label: str
    directory: Path
    hint: str


def skill_sources(extra: Sequence[str | Path], user_dir: Path | None, cwd: Path | None) -> list[ManagedSource]:
    """与 skills.resolve_skill_sources 同样的来源与优先级（低→高）。用户级目录即使不存在也列出，首次新建时创建。"""
    sources = [ManagedSource("user", "用户级", (user_dir or user_skills_dir()).expanduser(), "所有项目共享")]
    project = find_project_skills_dir(cwd)
    if project is not None:
        sources.append(ManagedSource("project", "项目级", project, "当前目录向上最近的 .log-agent/skills"))
    for index, item in enumerate(extra, start=1):
        path = Path(item).expanduser()
        sources.append(ManagedSource(f"extra-{index}", f"自定义（{path.name or path}）", path, "配置文件 skills = [...]"))
    return sources


def parse_skill(content: str, directory_name: str) -> tuple[dict[str, Any], list[str]]:
    """返回 (frontmatter, 问题列表)。问题非空时 SkillsMiddleware 会跳过这个 skill。"""
    import yaml

    match = _FRONTMATTER.match(content)
    if not match:
        return {}, ["缺少 YAML frontmatter：文件开头需要 --- 包围的 name / description"]
    try:
        data = yaml.safe_load(match.group(1))
    except yaml.YAMLError as exc:
        return {}, [f"frontmatter 不是合法的 YAML：{exc}"]
    if not isinstance(data, dict):
        return {}, ["frontmatter 需要是 key: value 形式"]
    problems = []
    name = str(data.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    if not name:
        problems.append("缺少 name")
    elif name != directory_name:
        problems.append(f"name「{name}」必须与目录名「{directory_name}」一致")
    if not description:
        problems.append("缺少 description：模型靠它判断什么时候用这本手册")
    elif len(description) > 1024:
        problems.append("description 超过 1024 个字符")
    return data, problems


def _skill_files(folder: Path) -> list[str]:
    files: list[str] = []
    for root, dirs, names in os.walk(folder):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if name.startswith("."):
                continue
            files.append(str((Path(root) / name).relative_to(folder)).replace(os.sep, "/"))
            if len(files) >= MAX_SKILL_FILES:
                return files
    return files


def _mtime(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).replace(microsecond=0).isoformat(sep=" ")
    except OSError:
        return None


def _is_linked(folder: Path) -> bool:
    return folder.is_symlink() or (folder / SKILL_FILE).is_symlink()


def _read_text(path: Path) -> str:
    if path.stat().st_size > MAX_SKILL_BYTES:
        raise HTTPException(413, f"{SKILL_FILE} 超过 {MAX_SKILL_BYTES // 1024} KB，请在本地编辑")
    return path.read_text(encoding="utf-8", errors="replace")


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".skill-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def list_skills(sources: Sequence[ManagedSource]) -> list[dict[str, Any]]:
    seen: dict[str, str] = {}
    result = []
    # 从高优先级往低走，才能标出被覆盖的同名 skill
    for source in reversed(sources):
        skills = []
        exists = source.directory.is_dir()
        if exists:
            try:
                children = sorted(source.directory.iterdir())
            except OSError:
                children = []
            for child in children:
                skill_file = child / SKILL_FILE
                if not child.is_dir() or not skill_file.is_file():
                    continue
                try:
                    data, problems = parse_skill(_read_text(skill_file), child.name)
                except (OSError, HTTPException) as exc:
                    data, problems = {}, [f"无法读取：{getattr(exc, 'detail', exc)}"]
                skills.append({
                    "name": child.name,
                    "description": str(data.get("description") or "").strip(),
                    "problems": problems,
                    "shadowed_by": seen.get(child.name),
                    "files": len(_skill_files(child)),
                    "updated_at": _mtime(skill_file),
                    "readonly": _is_linked(child),
                })
                if not problems:
                    seen.setdefault(child.name, source.label)
        result.append({
            "key": source.key, "label": source.label, "hint": source.hint,
            "directory": str(source.directory), "exists": exists, "skills": skills,
        })
    result.reverse()
    return result


class SkillCreate(BaseModel):
    source: str
    name: str = Field(max_length=64)
    content: str = Field(max_length=MAX_SKILL_BYTES)


class SkillUpdate(BaseModel):
    content: str = Field(max_length=MAX_SKILL_BYTES)


# ---------------------------------------------------------------------------
# 记忆
# ---------------------------------------------------------------------------


def _memory_dict(memory: Memory) -> dict[str, Any]:
    return {
        "id": memory.id,
        "kind": memory.kind,
        "kind_label": KIND_LABELS.get(memory.kind, memory.kind),
        "project": memory.project,
        "scope_label": memory.scope_label,
        "text": memory.text,
        "origin": memory.origin,
        "created_at": memory.created_at,
        "updated_at": memory.updated_at,
    }


def _candidate_dict(candidate: Candidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "kind": candidate.kind,
        "kind_label": KIND_LABELS.get(candidate.kind, candidate.kind),
        "project": candidate.project,
        "scope_label": project_label(candidate.project),
        "text": candidate.text,
        "signal": candidate.signal,
        "reason": candidate.reason,
        "occurrences": candidate.occurrences,
        "first_seen": candidate.first_seen,
        "last_seen": candidate.last_seen,
    }


class MemoryCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    kind: str
    project: str | None = None
    replace_id: int | None = None
    force: bool = False


class MemoryUpdate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class CandidateAccept(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    replace_id: int | None = None
    force: bool = False


class CandidateReject(BaseModel):
    permanent: bool = False


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


def register(
    app: FastAPI,
    *,
    require_owner: Callable[..., None],
    skill_dirs: Sequence[str | Path],
    skills_home: Path | None,
    skills_cwd: Path | None,
    memory_path: Path | None,
    session_projects: Callable[[], list[str]],
) -> None:
    owner = [Depends(require_owner)]

    def sources() -> list[ManagedSource]:
        return skill_sources(skill_dirs, skills_home, skills_cwd)

    def source_by_key(key: str) -> ManagedSource:
        for source in sources():
            if source.key == key:
                return source
        raise HTTPException(404, "skill 来源不存在")

    def skill_folder(
        key: str, name: str, *, must_exist: bool = True, writable: bool = False,
    ) -> tuple[ManagedSource, Path]:
        source = source_by_key(key)
        if not name or name in {".", ".."} or "/" in name or "\\" in name or name.startswith("."):
            raise HTTPException(400, "skill 名称无效")
        folder = source.directory / name
        if must_exist:
            if not (folder / SKILL_FILE).is_file():
                raise HTTPException(404, f"没有名为「{name}」的 skill")
            # 符号链接的 skill 只读：可以查看，但不在网页上改写或删除链接目标里的文件
            if _is_linked(folder):
                if writable:
                    raise HTTPException(409, f"「{name}」是符号链接，请到链接目标处在本地编辑")
            elif folder.resolve().parent != source.directory.resolve():
                raise HTTPException(400, "skill 目录不在来源目录内")
        return source, folder

    def skill_detail(source: ManagedSource, folder: Path) -> dict[str, Any]:
        content = _read_text(folder / SKILL_FILE)
        _, problems = parse_skill(content, folder.name)
        return {
            "source": source.key, "source_label": source.label, "name": folder.name,
            "path": str(folder / SKILL_FILE), "content": content, "problems": problems,
            "files": _skill_files(folder), "updated_at": _mtime(folder / SKILL_FILE),
            "readonly": _is_linked(folder),
        }

    def check_content(content: str, name: str) -> None:
        # 和 _read_text 用同一个字节上限，否则多字节文本写得进去却读不回来
        if len(content.encode("utf-8")) > MAX_SKILL_BYTES:
            raise HTTPException(413, f"{SKILL_FILE} 超过 {MAX_SKILL_BYTES // 1024} KB，请精简或在本地编辑")
        _, problems = parse_skill(content, name)
        if problems:
            raise HTTPException(422, "无法保存：" + "；".join(problems))

    @app.get("/api/skills", dependencies=owner)
    def skills_index() -> dict[str, Any]:
        return {"sources": list_skills(sources())}

    @app.get("/api/skills/{source}/{name}", dependencies=owner)
    def skill_get(source: str, name: str) -> dict[str, Any]:
        src, folder = skill_folder(source, name)
        return skill_detail(src, folder)

    @app.post("/api/skills", dependencies=owner)
    def skill_create(body: SkillCreate) -> dict[str, Any]:
        name = body.name.strip()
        if not _SKILL_NAME.match(name):
            raise HTTPException(422, "名称只能用小写字母、数字和单个连字符，例如 payment-timeout")
        src, folder = skill_folder(body.source, name, must_exist=False)
        if folder.exists():
            raise HTTPException(409, f"「{src.label}」里已经有名为「{name}」的 skill")
        check_content(body.content, name)
        try:
            folder.mkdir(parents=True)
            _atomic_write(folder / SKILL_FILE, body.content)
        except OSError as exc:
            raise HTTPException(500, f"创建失败：{exc}") from exc
        return skill_detail(src, folder)

    @app.put("/api/skills/{source}/{name}", dependencies=owner)
    def skill_update(source: str, name: str, body: SkillUpdate) -> dict[str, Any]:
        src, folder = skill_folder(source, name, writable=True)
        check_content(body.content, folder.name)
        try:
            _atomic_write(folder / SKILL_FILE, body.content)
        except OSError as exc:
            raise HTTPException(500, f"保存失败：{exc}") from exc
        return skill_detail(src, folder)

    @app.delete("/api/skills/{source}/{name}", dependencies=owner)
    def skill_delete(source: str, name: str) -> dict[str, Any]:
        _, folder = skill_folder(source, name, writable=True)
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            raise HTTPException(500, f"删除失败：{exc}") from exc
        return {"deleted": name}

    # ---- 记忆 --------------------------------------------------------------

    @contextmanager
    def store() -> Iterator[MemoryStore]:
        try:
            opened = MemoryStore(memory_path or default_memory_path())
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(503, f"记忆库不可用：{exc}") from exc
        try:
            yield opened
        except sqlite3.Error as exc:
            raise HTTPException(500, f"记忆库操作失败：{exc}") from exc
        finally:
            opened.close()

    def check_kind(kind: str) -> None:
        if kind not in KIND_LABELS:
            raise HTTPException(422, f"类型只能是 {' / '.join(KIND_LABELS)}")

    @app.get("/api/memory", dependencies=owner)
    def memory_index() -> dict[str, Any]:
        with store() as s:
            memories = s.memories(all_projects=True)
            pending = s.pending(all_projects=True)
            path = str(s.path)
        projects = {m.project for m in memories if m.project} | {c.project for c in pending if c.project}
        projects |= set(session_projects())
        return {
            "path": path,
            "kinds": [{"key": k, "label": v} for k, v in KIND_LABELS.items()],
            "projects": [{"key": p, "label": project_label(p)} for p in sorted(projects, key=project_label)],
            "memories": [_memory_dict(m) for m in memories],
            "pending": [_candidate_dict(c) for c in pending],
        }

    @app.post("/api/memory", dependencies=owner)
    def memory_create(body: MemoryCreate) -> dict[str, Any]:
        check_kind(body.kind)
        if not body.text.strip():
            raise HTTPException(422, "内容不能为空")
        with store() as s:
            if body.replace_id is None and not body.force:
                similar = s.similar_memories(body.text, body.kind, body.project)
                if similar:
                    return {"saved": False, "similar": [_memory_dict(m) for m in similar]}
            try:
                memory, updated = s.add(body.text, body.kind, body.project, origin="explicit", replace_id=body.replace_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        return {"saved": True, "updated": updated, "memory": _memory_dict(memory)}

    @app.patch("/api/memory/{memory_id}", dependencies=owner)
    def memory_update(memory_id: int, body: MemoryUpdate) -> dict[str, Any]:
        if not body.text.strip():
            raise HTTPException(422, "内容不能为空")
        with store() as s:
            memory = s.update(memory_id, body.text)
        if memory is None:
            raise HTTPException(404, f"没有编号为 #{memory_id} 的记忆")
        return _memory_dict(memory)

    @app.delete("/api/memory/{memory_id}", dependencies=owner)
    def memory_delete(memory_id: int) -> dict[str, Any]:
        with store() as s:
            memory = s.remove(memory_id)
        if memory is None:
            raise HTTPException(404, f"没有编号为 #{memory_id} 的记忆")
        return {"deleted": memory_id}

    def candidate_or_404(s: MemoryStore, candidate_id: int) -> Candidate:
        candidate = s._candidate(candidate_id)
        if candidate is None:
            raise HTTPException(404, "这条建议已被处理或已过期")
        return candidate

    @app.post("/api/memory/candidates/{candidate_id}/accept", dependencies=owner)
    def candidate_accept(candidate_id: int, body: CandidateAccept | None = None) -> dict[str, Any]:
        body = body or CandidateAccept()
        text = body.text.strip() if body.text else None
        with store() as s:
            candidate = candidate_or_404(s, candidate_id)
            if body.replace_id is None and not body.force:
                similar = s.similar_memories(text or candidate.text, candidate.kind, candidate.project)
                if similar:
                    return {"saved": False, "similar": [_memory_dict(m) for m in similar]}
            try:
                memory = s.accept(candidate, text, replace_id=body.replace_id)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
        return {"saved": True, "memory": _memory_dict(memory)}

    @app.post("/api/memory/candidates/{candidate_id}/reject", dependencies=owner)
    def candidate_reject(candidate_id: int, body: CandidateReject | None = None) -> dict[str, Any]:
        with store() as s:
            s.reject(candidate_or_404(s, candidate_id), permanent=bool(body and body.permanent))
        return {"rejected": candidate_id}


def session_memory(memory_path: Path | None, mode: str, info: Any) -> dict[str, Any]:
    """会话对话页用：本会话每轮实际注入的记忆、因篇幅被跳过的记忆，以及本会话里提出、等用户确认的候选。

    范围与挑选逻辑都与 runner 注入提示词时一致（全局 + 会话源码目录所属项目，按同一篇幅预算）。
    """
    from ..memory import normalize_mode, project_key, select_for_prompt

    mode = normalize_mode(mode) or "off"
    project = project_key(info.code)
    result: dict[str, Any] = {
        "mode": mode, "project": project, "project_label": project_label(project),
        "available": True, "memories": [], "skipped": [], "pending": [],
    }
    if mode == "off":
        return result
    try:
        opened = MemoryStore(memory_path or default_memory_path())
    except (sqlite3.Error, OSError):
        return {**result, "available": False}
    try:
        selected, skipped = select_for_prompt(opened.memories(project))
        result["memories"] = [_memory_dict(m) for m in selected]
        result["skipped"] = [_memory_dict(m) for m in skipped]
        result["pending"] = [_candidate_dict(c) for c in opened.pending(project) if info.name in c.sessions]
    except sqlite3.Error:
        result["available"] = False
    finally:
        opened.close()
    return result


def projects_from_sessions(db_path: Path) -> list[str]:
    """会话里用过的源码目录对应的项目，供新增记忆时选择范围。"""
    if not db_path.exists():
        return []
    from ..sessions import SessionStore

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            infos = SessionStore(conn).list()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    keys = set()
    for info in infos:
        if info.code:
            try:
                key = project_key(info.code)
            except OSError:
                continue
            if key:
                keys.add(key)
    return sorted(keys)
