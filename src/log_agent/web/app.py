"""`log-agent serve` 的 FastAPI 应用。

两种访问身份：
- 本人：启动时生成访问令牌（像 Jupyter 一样打印在终端里），首次用 `?token=` 打开后换成 HttpOnly
  Cookie。能看全部会话、在网页里续问、创建 / 撤销分享链接。
- 分享链接：`/s/<token>` 只读访问单个会话（报告、时间线、证据原文、导出），始终脱敏，不能续问。

同一组只读接口挂在两个前缀下：`/api/sessions/{name}/...`（本人）和 `/api/share/{token}/...`（分享）。
"""

from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import threading
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from .. import __version__
from ..evidence import SourceResolver
from ..redact import mask_private_keys, redact_log
from ..sessions import SessionInfo, SessionStore
from .shares import ShareStore
from .sources import ViewSourceResolver
from .workspace import CreateSessionRequest

STATIC_DIR = Path(__file__).parent / "static"
COOKIE = "log_agent_auth"
CSRF_HEADER = "x-log-agent-request"
SHARE_TTL_CHOICES = {None, 1, 24, 24 * 7, 24 * 30}


@dataclass
class WebConfig:
    db_path: Path
    token: str | None
    agent_factory: Callable[..., Any] | None = None
    base_url: str | None = None
    can_chat: bool = True
    public_url: str | None = None
    redact_owner: bool = True
    loopback: bool = True
    default_model: str = "openai:gpt-4.1"


@dataclass
class Scope:
    name: str
    read_only: bool
    redact: bool


# ---------------------------------------------------------------------------
# 数据访问
# ---------------------------------------------------------------------------


@contextmanager
def _connect(config: WebConfig) -> Iterator[sqlite3.Connection]:
    if not config.db_path.exists():
        raise HTTPException(404, f"会话数据库不存在：{config.db_path}（先用 log-agent chat 产生会话）")
    conn = sqlite3.connect(str(config.db_path), check_same_thread=False)
    try:
        yield conn
    finally:
        conn.close()


def _redacted_copy(value: Any, enabled: bool, resolver: SourceResolver | None = None) -> Any:
    """Copy display text, retaining only validated source navigation identifiers."""
    if isinstance(value, str):
        return redact_log(value, enabled=True) if enabled else value
    if isinstance(value, dict):
        return {
            (redact_log(key, enabled=True) if enabled and isinstance(key, str) else key):
            item if (enabled and key == "source" and isinstance(item, str)
                          and resolver is not None and resolver.resolve(item) is not None)
            else _redacted_copy(item, enabled, resolver)
            for key, item in value.items()
        }
    if isinstance(value, list):
        result = []
        i = 0
        while i < len(value):
            if enabled and isinstance(value[i], str):
                # PEM markers and body may span adjacent list items. Preserve each
                # item's original line count so the JSON shape remains unchanged.
                end = i + 1
                while end < len(value) and isinstance(value[end], str):
                    end += 1
                rows = mask_private_keys("\n".join(value[i:end])).split("\n")
                offset = 0
                for item in value[i:end]:
                    count = item.count("\n") + 1
                    result.append(redact_log("\n".join(rows[offset:offset + count]), enabled=True))
                    offset += count
                i = end
            else:
                result.append(_redacted_copy(value[i], enabled, resolver))
                i += 1
        return result
    return value


def _turn_brief(number: int, payload: dict[str, Any]) -> dict[str, Any]:
    analysis = payload.get("analysis") or {}
    check = payload.get("evidence_check") or {}
    return {
        "turn": number,
        "question": payload.get("question", ""),
        "generated_at": payload.get("generated_at"),
        "status": payload.get("status"),
        "summary": payload.get("summary"),
        "assessment": analysis.get("assessment") if payload.get("schema_version") else None,
        "confidence": analysis.get("confidence"),
        "evidence_status": check.get("status"),
        "issues": len(analysis.get("issues") or []),
    }


def _session_dict(info: SessionInfo) -> dict[str, Any]:
    # Keep the name-prefix fallback for legacy sessions without origin metadata.
    origin = "analyze" if info.settings.get("origin") == "analyze" or info.name.startswith("analyze-") else "chat"
    return {
        "name": info.name,
        "origin": origin,
        "title": info.title,
        "logs": [{"path": p, "name": Path(p).name, "exists": Path(p).is_file()} for p in info.logs],
        "code": [{"path": p, "name": Path(p).name} for p in info.code],
        "model": info.model,
        "turns": info.turns,
        "total_tokens": info.total_tokens,
        "created_at": info.created_at,
        "updated_at": info.updated_at,
        "settings": info.settings,
    }


def _shared_session_dict(info: SessionInfo) -> dict[str, Any]:
    """Only read-only UI metadata; source handles never contain owner-local directories."""
    return {
        "name": info.name,
        "origin": "analyze" if info.settings.get("origin") == "analyze" or info.name.startswith("analyze-") else "chat",
        "title": info.title,
        "logs": [{"path": f"log/{i}", "name": Path(p).name, "exists": Path(p).is_file()}
                 for i, p in enumerate(info.logs)],
        "code": [{"path": f"code/{i}", "name": Path(p).name} for i, p in enumerate(info.code)],
        "model": info.model,
        "turns": info.turns,
        "total_tokens": info.total_tokens,
        "created_at": info.created_at,
        "updated_at": info.updated_at,
        "settings": {key: info.settings[key] for key in ("since", "until", "timezone", "baseline")
                     if key in info.settings},
    }


def _shared_copy(value: Any, info: SessionInfo) -> Any:
    """Replace registered local paths and allowlist display settings in shared responses."""
    if isinstance(value, str):
        paths = [(p, f"{kind}/{i}") for kind, entries in (("log", info.logs), ("code", info.code))
                 for i, p in enumerate(entries)]
        for path, handle in sorted(paths, key=lambda pair: len(pair[0]), reverse=True):
            value = value.replace(path, handle)
        return value
    if isinstance(value, list):
        return [_shared_copy(item, info) for item in value]
    if isinstance(value, dict):
        result = {key: _shared_copy(
            {k: v for k, v in item.items() if k in {"since", "until", "timezone", "baseline"}}
            if key == "settings" and isinstance(item, dict) else item, info,
        ) for key, item in value.items()}
        # Browser source handles use URL separators, including on Windows hosts.
        source = result.get("source")
        if isinstance(source, str) and source.startswith("code/"):
            result["source"] = source.replace("\\", "/")
        return result
    return value


def _load(conn: sqlite3.Connection, name: str) -> tuple[SessionStore, SessionInfo]:
    store = SessionStore(conn)
    info = store.get(name)
    if info is None:
        raise HTTPException(404, f"会话不存在：{name}")
    return store, info


def _tz(info: SessionInfo):
    from ..timefilter import parse_timezone

    try:
        return parse_timezone(info.settings.get("timezone") or "UTC")
    except ValueError:
        from datetime import UTC

        return UTC


# ---------------------------------------------------------------------------
# 应用
# ---------------------------------------------------------------------------


class ShareRequest(BaseModel):
    ttl_hours: int | None = Field(default=24 * 7)


def create_app(config: WebConfig) -> FastAPI:
    app = FastAPI(title="log-agent", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        # 分享链接的 token 在 URL 里：不让它经 Referer 泄露给页面上的外部链接
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        return response

    # ---- 身份 --------------------------------------------------------------

    def is_owner(request: Request) -> bool:
        if config.token is None:
            return True
        supplied = request.cookies.get(COOKIE) or ""
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            supplied = auth[7:].strip()
        return bool(supplied) and secrets.compare_digest(supplied, config.token)

    def require_owner(request: Request) -> None:
        if not is_owner(request):
            raise HTTPException(401, "需要访问令牌：请使用 log-agent serve 启动时打印的链接打开")
        # 写操作要求自定义请求头：跨站表单 / 简单请求带不上它，配合 SameSite Cookie 防 CSRF
        if request.method not in {"GET", "HEAD"} and request.headers.get(CSRF_HEADER) != "1":
            raise HTTPException(403, f"缺少请求头 {CSRF_HEADER}")

    def owner_scope(name: str, _: None = Depends(require_owner)) -> Scope:
        return Scope(name=name, read_only=False, redact=config.redact_owner)

    def share_scope(token: str) -> Scope:
        with _connect(config) as conn:
            name = ShareStore(conn).resolve(token)
        if name is None:
            raise HTTPException(404, "分享链接无效、已过期或已被撤销")
        return Scope(name=name, read_only=True, redact=True)

    # ---- 元信息与会话列表（仅本人）-----------------------------------------

    @app.get("/api/meta")
    def meta(request: Request) -> dict[str, Any]:
        owner = is_owner(request)
        return {
            "version": __version__,
            "authenticated": owner,
            "can_chat": owner and config.can_chat and config.agent_factory is not None,
            "share_base": _share_base(request),
            "loopback": config.loopback and not config.public_url,
            "db": str(config.db_path) if owner else None,
            "default_model": config.default_model if owner else None,
        }

    # ---- 新建分析：浏览本机文件、列出模型、登记会话（仅本人）-----------------

    @app.get("/api/fs/list", dependencies=[Depends(require_owner)])
    def fs_list(path: str = "", hidden: bool = False) -> dict[str, Any]:
        from .workspace import list_directory

        return list_directory(path or None, show_hidden=hidden)

    @app.get("/api/fs/places", dependencies=[Depends(require_owner)])
    def fs_places() -> list[dict[str, str]]:
        from .workspace import places

        if not config.db_path.exists():
            return places(None)
        with _connect(config) as conn:
            return places(conn)

    @app.get("/api/models", dependencies=[Depends(require_owner)])
    def models() -> dict[str, Any]:
        from .workspace import list_models

        if not config.can_chat:
            return {"default": config.default_model, "models": []}
        return list_models(config.base_url, config.default_model)

    @app.post("/api/sessions", dependencies=[Depends(require_owner)])
    def create_session(body: CreateSessionRequest) -> dict[str, Any]:
        from ..chat_session import new_session_name
        from .workspace import validate_request

        if config.agent_factory is None or not config.can_chat:
            raise HTTPException(503, "当前服务不能新建分析：缺少 OPENAI_API_KEY，或启动时用了 --read-only")
        logs, code, settings = validate_request(body)
        model = (body.model or "").strip() or config.default_model
        if ":" not in model:
            model = f"openai:{model}"
        name = new_session_name()
        config.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(config.db_path), check_same_thread=False)
        try:
            store = SessionStore(conn)
            store.touch(name, logs, code, model, settings)
            info = store.get(name)
        finally:
            conn.close()
        return _session_dict(info)

    @app.get("/api/sessions", dependencies=[Depends(require_owner)])
    def list_sessions(q: str = "") -> list[dict[str, Any]]:
        if not config.db_path.exists():
            return []
        with _connect(config) as conn:
            store = SessionStore(conn)
            items = store.search(q) if q.strip() else store.list()
            result = []
            for info in items:
                last = store.last_turn(info.name)
                result.append({**_session_dict(info), "last": _turn_brief(info.turns, last) if last else None})
            return result

    @app.delete("/api/sessions/{name}")
    def delete_session(scope: Scope = Depends(owner_scope)) -> dict[str, Any]:
        with _connect(config) as conn:
            return {"deleted": SessionStore(conn).delete(scope.name)}

    # ---- 分享链接管理（仅本人）---------------------------------------------

    def _share_base(request: Request) -> str:
        return (config.public_url or str(request.base_url)).rstrip("/") + "/s/"

    @app.get("/api/sessions/{name}/shares")
    def list_shares(scope: Scope = Depends(owner_scope)) -> list[dict[str, Any]]:
        with _connect(config) as conn:
            _load(conn, scope.name)
            return [s.to_dict() for s in ShareStore(conn).list(scope.name)]

    @app.post("/api/sessions/{name}/shares")
    def create_share(body: ShareRequest, request: Request, scope: Scope = Depends(owner_scope)) -> dict[str, Any]:
        if body.ttl_hours not in SHARE_TTL_CHOICES:
            raise HTTPException(400, "有效期只能是 1 小时、1 天、7 天、30 天或永久")
        with _connect(config) as conn:
            _load(conn, scope.name)
            share, token = ShareStore(conn).create(scope.name, body.ttl_hours)
        return {**share.to_dict(), "token": token, "url": _share_base(request) + token}

    @app.delete("/api/sessions/{name}/shares/{share_id}")
    def revoke_share(share_id: str, scope: Scope = Depends(owner_scope)) -> dict[str, Any]:
        with _connect(config) as conn:
            if not ShareStore(conn).revoke(scope.name, share_id):
                raise HTTPException(404, "分享链接不存在")
        return {"revoked": share_id}

    # ---- 只读接口：本人与分享链接共用 ---------------------------------------

    owner_router = APIRouter(prefix="/api/sessions/{name}")
    share_router = APIRouter(prefix="/api/share/{token}")
    for router, dep in ((owner_router, owner_scope), (share_router, share_scope)):
        _register_read_routes(router, dep, config)
    app.include_router(owner_router)
    app.include_router(share_router)

    # ---- 续问（仅本人）-----------------------------------------------------

    @app.post("/api/sessions/{name}/chat")
    async def chat(request: Request, scope: Scope = Depends(owner_scope)):
        if config.agent_factory is None or not config.can_chat:
            raise HTTPException(503, "当前服务不能续问：缺少 OPENAI_API_KEY，或启动时用了 --read-only")
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(400, "请求体不是 JSON") from exc
        question = _question_from(body)
        if not question:
            raise HTTPException(400, "问题不能为空")
        with _connect(config) as conn:
            _, info = _load(conn, scope.name)
        missing = [p for p in info.logs if not Path(p).is_file()]
        if missing:
            raise HTTPException(409, f"会话使用的日志已不存在：{missing[0]}")
        return _start_turn(config, info, question, body)

    # ---- 前端页面 ----------------------------------------------------------

    @app.get("/s/{token}")
    def share_page(token: str) -> Response:
        return _index()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if path.startswith("api/"):
            raise HTTPException(404, "接口不存在")
        token = request.query_params.get("token")
        if token is not None and config.token is not None:
            # 一次性把 URL 里的令牌换成 Cookie，再重定向掉，避免令牌留在地址栏和浏览历史里
            target = "/" + path
            if not secrets.compare_digest(token, config.token):
                return HTMLResponse(_message_page("访问令牌不正确", "请使用 log-agent serve 启动时打印的完整链接。"), 401)
            response = RedirectResponse(target or "/", status_code=303)
            response.set_cookie(COOKIE, token, httponly=True, samesite="strict", path="/")
            return response
        asset = (STATIC_DIR / path).resolve()
        if path and asset.is_file() and asset.is_relative_to(STATIC_DIR.resolve()):
            return FileResponse(asset)
        return _index()

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)

    return app


def _register_read_routes(router: APIRouter, dep, config: WebConfig) -> None:
    from ..export import to_markdown
    from ..report import ReportView, report_body
    from .sources import SourceError, read_context
    from .timeline import build_timeline

    @router.get("")
    def session_detail(scope: Scope = Depends(dep)) -> dict[str, Any]:
        with _connect(config) as conn:
            store, info = _load(conn, scope.name)
            turns = [_turn_brief(n, p) for n, p in store.history(scope.name)]
        result = {**(_shared_session_dict(info) if scope.read_only else _session_dict(info)),
                  "read_only": scope.read_only, "turn_list": turns}
        if scope.read_only:
            result = _shared_copy(result, info)
        return _redacted_copy(result, scope.redact)

    @router.get("/turns/{number}")
    def turn(number: int, scope: Scope = Depends(dep)) -> dict[str, Any]:
        with _connect(config) as conn:
            store, info = _load(conn, scope.name)
            payload = store.turn(scope.name, number)
        if payload is None:
            raise HTTPException(404, f"第 {number} 轮不存在或未保存报告")
        payload = {**payload, "settings": {k: v for k, v in payload.get("settings", {}).items() if k != "base_url"}}
        if scope.read_only:
            payload = _shared_copy(payload, info)
        payload = _redacted_copy(payload, scope.redact, ViewSourceResolver(info.logs, info.code))
        if not payload.get("schema_version"):
            payload = {**payload, "analysis": None, "structured_status": "missing"}
        return {"turn": number, **payload}

    @router.get("/timeline")
    def timeline(buckets: int = 120, scope: Scope = Depends(dep)) -> dict[str, Any]:
        with _connect(config) as conn:
            _, info = _load(conn, scope.name)
        result = build_timeline(
            info.logs, _tz(info), target_buckets=buckets,
            encoding=info.settings.get("encoding") or None, redact=scope.redact,
        )
        return _shared_copy(result, info) if scope.read_only else result

    @router.get("/source")
    def source(source: str, start: int, end: int | None = None, before: int = 20, after: int = 20,
               scope: Scope = Depends(dep)) -> dict[str, Any]:
        with _connect(config) as conn:
            _, info = _load(conn, scope.name)
        try:
            result = read_context(
                info.logs, info.code, source, start, end, before=before, after=after,
                redact=scope.redact, encoding=info.settings.get("encoding") or None,
            )
            if scope.read_only:
                result.pop("path", None)
                if source in info.logs:
                    result["source"] = f"log/{info.logs.index(source)}"
            return _shared_copy(result, info) if scope.read_only else result
        except SourceError as exc:
            message = _shared_copy(str(exc), info) if scope.read_only else str(exc)
            raise HTTPException(exc.status, redact_log(message, enabled=scope.redact)) from exc

    @router.get("/export")
    def export(turn: int, format: str = "markdown", view: str = "detailed", scope: Scope = Depends(dep)) -> Response:
        try:
            ReportView(view)
        except ValueError as exc:
            raise HTTPException(400, "view 只能是 brief / detailed / ticket") from exc
        with _connect(config) as conn:
            store, info = _load(conn, scope.name)
            payload = store.turn(scope.name, turn)
        if payload is None:
            raise HTTPException(404, f"第 {turn} 轮不存在或未保存报告")
        payload = {**payload, "settings": {k: v for k, v in payload.get("settings", {}).items() if k != "base_url"}}
        if scope.read_only:
            payload = _shared_copy(payload, info)
        payload = _redacted_copy(payload, scope.redact, ViewSourceResolver(info.logs, info.code))
        if not payload.get("schema_version"):
            payload = {**payload, "schema_version": 2, "analysis": None, "structured_status": "missing", "finding": None}
        stem = f"{info.name}-turn{turn}"
        if format == "json":
            content = json.dumps({**payload, "view": view, "rendered_report": report_body(payload, view)},
                                 ensure_ascii=False, indent=2)
            filename, media = f"{stem}.json", "application/json"
        elif format == "markdown":
            content, filename, media = to_markdown(payload, view), f"{stem}.md", "text/markdown"
        else:
            raise HTTPException(400, "format 只能是 markdown / json")
        disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
        return Response(content, media_type=f"{media}; charset=utf-8", headers={"Content-Disposition": disposition})


def _question_from(body: Any) -> str:
    """兼容 AG-UI RunAgentInput（TanStack AI 的 fetchServerSentEvents）和简单的 {"question": ...}。"""
    if not isinstance(body, dict):
        return ""
    if isinstance(body.get("question"), str):
        return body["question"].strip()
    for message in reversed(body.get("messages") or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        parts = content if isinstance(content, list) else message.get("parts") or []
        texts = [p.get("content") or p.get("text") or "" for p in parts if isinstance(p, dict) and p.get("type") == "text"]
        return "\n".join(t for t in texts if t).strip()
    return ""


def _start_turn(config: WebConfig, info: SessionInfo, question: str, body: dict[str, Any]) -> StreamingResponse:
    from .runner import TURN_LOCK, custom, run_turn

    if not TURN_LOCK.acquire(blocking=False):
        raise HTTPException(409, "已有一轮分析正在进行，请等它结束后再追问")
    thread_id = str(body.get("threadId") or info.name)
    run_id = str(body.get("runId") or f"run-{uuid.uuid4().hex[:12]}")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    cancelled = threading.Event()

    def emit(event: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def work() -> None:
        try:
            run_turn(
                db_path=config.db_path, info=info, question=question, emit=emit, cancelled=cancelled,
                agent_factory=config.agent_factory, base_url=config.base_url, thread_id=thread_id, run_id=run_id,
                redact_owner=config.redact_owner,
            )
        except Exception as exc:  # noqa: BLE001 — 任何失败都要以 RUN_ERROR 告知浏览器，而不是让流悄悄断开
            from ..redact import redact_log

            emit(custom("log_agent.error", {"message": redact_log(f"{type(exc).__name__}: {exc}", enabled=config.redact_owner)}))
            emit({"type": "RUN_ERROR", "threadId": thread_id, "runId": run_id,
                  "message": redact_log(f"{type(exc).__name__}: {exc}", enabled=config.redact_owner)})
        finally:
            TURN_LOCK.release()
            emit(None)

    threading.Thread(target=work, name=f"log-agent-turn-{info.name}", daemon=True).start()

    async def stream():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": keep-alive\n\n"  # 长时间跑工具时防止代理 / 浏览器判定连接空闲
                    continue
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        finally:
            # 浏览器断开（关页面、点停止）：通知分析线程按中断收尾，已输出部分照常存档
            cancelled.set()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _index() -> Response:
    index = STATIC_DIR / "index.html"
    if not index.is_file():
        return HTMLResponse(_message_page(
            "前端尚未构建",
            "当前安装缺少 Web 前端资源。源码运行时请先执行：cd web && npm ci && npm run build",
        ), 503)
    return FileResponse(index, headers={"Cache-Control": "no-cache"})


def _message_page(title: str, body: str) -> str:
    from html import escape

    return (f"<!doctype html><meta charset='utf-8'><title>{escape(title)}</title>"
            f"<body style='font-family:system-ui;padding:3rem;color:#333'><h2>{escape(title)}</h2>"
            f"<p>{escape(body)}</p></body>")
