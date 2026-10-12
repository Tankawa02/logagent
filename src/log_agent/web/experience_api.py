"""网页上的排查经验管理：查看、确认候选、淘汰、编辑与删除（与 `log-agent experience` 子命令同一个经验库）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..experience import (
    FIELD_LIMITS,
    ORIGIN_LABELS,
    STATUS_LABELS,
    ExperienceStore,
    default_experience_path,
    enabled,
    reflection_enabled,
)
from ..memory import project_label


class ExperienceCreate(BaseModel):
    title: str = Field(min_length=1, max_length=FIELD_LIMITS["title"])
    trigger: str = Field(default="通用", max_length=FIELD_LIMITS["trigger"])
    strategy: str = Field(min_length=1, max_length=FIELD_LIMITS["strategy"])
    project: str | None = None


class ExperienceUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=FIELD_LIMITS["title"])
    trigger: str | None = Field(default=None, max_length=FIELD_LIMITS["trigger"])
    strategy: str | None = Field(default=None, min_length=1, max_length=FIELD_LIMITS["strategy"])


class ExperienceStatus(BaseModel):
    status: Literal["candidate", "active", "retired"]


def register(app: FastAPI, *, require_owner: Callable[..., None], experience_path: Path | None) -> None:
    owner = [Depends(require_owner)]

    @contextmanager
    def store() -> Iterator[ExperienceStore]:
        try:
            opened = ExperienceStore(experience_path or default_experience_path())
        except (sqlite3.Error, OSError) as exc:
            raise HTTPException(503, f"经验库不可用：{exc}") from exc
        try:
            yield opened
        except sqlite3.Error as exc:
            raise HTTPException(500, f"经验库操作失败：{exc}") from exc
        finally:
            opened.close()

    @app.get("/api/experience", dependencies=owner)
    def experience_index() -> dict[str, Any]:
        with store() as s:
            entries = s.list(all_projects=True)
            path = str(s.path)
        projects = sorted({e.project for e in entries if e.project}, key=project_label)
        return {
            "path": path,
            "enabled": enabled(),
            "reflection": reflection_enabled(),
            "statuses": [{"key": k, "label": v} for k, v in STATUS_LABELS.items()],
            "origins": [{"key": k, "label": v} for k, v in ORIGIN_LABELS.items()],
            "projects": [{"key": p, "label": project_label(p)} for p in projects],
            "entries": [e.to_dict() for e in entries],
        }

    @app.post("/api/experience", dependencies=owner)
    def experience_create(body: ExperienceCreate) -> dict[str, Any]:
        with store() as s:
            try:
                entry, created = s.add(body.title, body.trigger, body.strategy, body.project, status="active",
                                       origin="manual")
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return {"created": created, "entry": entry.to_dict()}

    @app.patch("/api/experience/{experience_id}", dependencies=owner)
    def experience_update(experience_id: int, body: ExperienceUpdate) -> dict[str, Any]:
        with store() as s:
            try:
                entry = s.update(experience_id, title=body.title, trigger=body.trigger, strategy=body.strategy)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        if entry is None:
            raise HTTPException(404, f"没有编号为 E{experience_id} 的经验")
        return entry.to_dict()

    @app.post("/api/experience/{experience_id}/status", dependencies=owner)
    def experience_status(experience_id: int, body: ExperienceStatus) -> dict[str, Any]:
        with store() as s:
            entry = s.set_status(experience_id, body.status)
        if entry is None:
            raise HTTPException(404, f"没有编号为 E{experience_id} 的经验")
        return entry.to_dict()

    @app.delete("/api/experience/{experience_id}", dependencies=owner)
    def experience_delete(experience_id: int) -> dict[str, Any]:
        with store() as s:
            entry = s.remove(experience_id)
        if entry is None:
            raise HTTPException(404, f"没有编号为 E{experience_id} 的经验")
        return {"deleted": experience_id}
