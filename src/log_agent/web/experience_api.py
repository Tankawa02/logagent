"""网页上的排查经验管理：查看、确认候选、淘汰、编辑、删除与精简合并（与 `log-agent experience` 子命令同一个经验库）。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from ..experience import (
    CONSOLIDATE_MAX_ITEMS,
    FIELD_LIMITS,
    ORIGIN_LABELS,
    STATUS_LABELS,
    ExperienceStore,
    default_experience_path,
    enabled,
    propose_consolidation,
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


class ConsolidateRequest(BaseModel):
    project: str | None = None


class MergeRequest(BaseModel):
    keep: int
    ids: list[int] = Field(min_length=2, max_length=CONSOLIDATE_MAX_ITEMS)
    title: str = Field(min_length=1, max_length=FIELD_LIMITS["title"])
    trigger: str = Field(default="通用", max_length=FIELD_LIMITS["trigger"])
    strategy: str = Field(min_length=1, max_length=FIELD_LIMITS["strategy"])
    reason: str = Field(default="", max_length=400)
    model: str = Field(default="", max_length=200)


def register(app: FastAPI, *, require_owner: Callable[..., None], experience_path: Path | None,
             post_models: Callable[[], list[Any]] | None = None) -> None:
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

    @app.get("/api/experience/{experience_id}/history", dependencies=owner)
    def experience_history(experience_id: int) -> dict[str, Any]:
        with store() as s:
            entry = s.get(experience_id)
            if entry is None:
                raise HTTPException(404, f"没有编号为 E{experience_id} 的经验")
            history = s.history(experience_id)
        return {"entry": entry.to_dict(), "events": history["events"], "runs": list(history["runs"].values())}

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

    @app.post("/api/experience/consolidate", dependencies=owner)
    def experience_consolidate(body: ConsolidateRequest) -> dict[str, Any]:
        """让收尾模型找出某个范围内的同类经验、起草合并后的内容；只返回建议，确认后再调 /merge。"""
        models = post_models() if post_models else []
        if not models:
            raise HTTPException(503, "当前服务不能调用模型：还没有 API Key（可在「设置」页填写），或启动时用了 --read-only")
        with store() as s:
            entries = [e for e in s.list(body.project, status=("candidate", "active")) if e.project == body.project]
        try:
            proposals, rejected, calls = propose_consolidation(models, entries)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc)) from exc
        model = next((str(c["model"]) for c in reversed(calls) if c.get("model")), "")
        return {"project": body.project, "scanned": len(entries), "proposals": proposals, "rejected": rejected,
                "model": model}

    @app.post("/api/experience/merge", dependencies=owner)
    def experience_merge(body: MergeRequest) -> dict[str, Any]:
        if body.keep not in body.ids:
            raise HTTPException(422, "保留的条目必须在合并列表里")
        with store() as s:
            try:
                entry = s.merge(body.keep, [i for i in body.ids if i != body.keep], title=body.title,
                                trigger=body.trigger, strategy=body.strategy, reason=body.reason, model=body.model)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        return entry.to_dict()

    @app.delete("/api/experience/{experience_id}", dependencies=owner)
    def experience_delete(experience_id: int) -> dict[str, Any]:
        with store() as s:
            entry = s.remove(experience_id)
        if entry is None:
            raise HTTPException(404, f"没有编号为 E{experience_id} 的经验")
        return {"deleted": experience_id}
