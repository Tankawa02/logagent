"""Web 界面（log-agent serve）：鉴权、分享链接、时间线、证据原文、网页续问的 AG-UI 事件流。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from log_agent import agent as agent_module  # noqa: E402
from log_agent import cli  # noqa: E402
from log_agent.sessions import SessionStore  # noqa: E402
from log_agent.web import runner  # noqa: E402
from log_agent.web.app import WebConfig, create_app  # noqa: E402
from log_agent.web.shares import ShareStore  # noqa: E402
from log_agent.web.timeline import build_timeline, pick_width  # noqa: E402

from .conftest import ScriptedChatModel, tool_call  # noqa: E402
from .web_fixtures import QUESTION, SESSION, first_line, report_text, seed_session, spike_log  # noqa: E402

TOKEN = "owner-token"
OWNER = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**OWNER, "X-Log-Agent-Request": "1"}


@pytest.fixture
def demo(tmp_path: Path, code_repo: Path):
    log = spike_log(tmp_path / "app.log")
    db = tmp_path / "sessions.db"
    seed_session(db, log, code_repo)
    return db, log, code_repo


def client_for(db: Path, **kwargs) -> TestClient:
    return TestClient(create_app(WebConfig(db_path=db, token=TOKEN, **kwargs)))


def sse_events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.iter_lines() if line.startswith("data: ")]


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


def test_owner_api_requires_token(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    sessions = client.get("/api/sessions", headers=OWNER).json()
    assert [s["name"] for s in sessions] == [SESSION]
    assert sessions[0]["last"]["assessment"] == "finding"
    assert client.get("/api/meta").json()["authenticated"] is False


def test_token_link_becomes_httponly_cookie(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    response = client.get(f"/sessions/{SESSION}?token={TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == f"/sessions/{SESSION}"  # 令牌不留在地址栏
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    assert client.get("/api/sessions").status_code == 200  # TestClient 带上了 Cookie
    assert client_for(db).get("/?token=nope", follow_redirects=False).status_code == 401


def test_writes_need_csrf_header(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    assert client.post(f"/api/sessions/{SESSION}/shares", json={}, headers=OWNER).status_code == 403
    assert client.post(f"/api/sessions/{SESSION}/shares", json={}, headers=WRITE).status_code == 200


def test_security_headers_hide_referrer(demo) -> None:
    db, *_ = demo
    headers = client_for(db).get("/api/meta").headers
    assert headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]


def test_serve_refuses_no_token_on_public_host() -> None:
    result = CliRunner().invoke(cli.app, ["serve", "--host", "0.0.0.0", "--no-token"])
    assert result.exit_code == 2
    assert "--no-token" in result.output


# ---------------------------------------------------------------------------
# 会话、报告与导出
# ---------------------------------------------------------------------------


def test_session_detail_and_turn_payload(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    detail = client.get(f"/api/sessions/{SESSION}", headers=OWNER).json()
    assert detail["read_only"] is False
    assert detail["turn_list"][0]["question"] == QUESTION
    assert detail["turn_list"][0]["evidence_status"] == "mismatch"  # 第三条证据是编造的
    turn = client.get(f"/api/sessions/{SESSION}/turns/1", headers=OWNER).json()
    assert turn["schema_version"] == 2 and turn["structured_status"] == "valid"
    statuses = [item["status"] for item in turn["evidence_check"]["items"]]
    assert statuses == ["verified", "verified", "mismatch"]
    assert client.get(f"/api/sessions/{SESSION}/turns/9", headers=OWNER).status_code == 404
    assert client.get("/api/sessions/nope", headers=OWNER).status_code == 404


def test_export_markdown_and_json(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    md = client.get(f"/api/sessions/{SESSION}/export", params={"turn": 1, "view": "ticket"}, headers=OWNER)
    assert md.status_code == 200
    assert "attachment" in md.headers["content-disposition"]
    assert md.text.startswith("# 日志分析报告")
    data = client.get(f"/api/sessions/{SESSION}/export", params={"turn": 1, "format": "json"}, headers=OWNER).json()
    assert data["schema_version"] == 2 and data["view"] == "detailed"
    assert client.get(f"/api/sessions/{SESSION}/export", params={"turn": 1, "view": "x"}, headers=OWNER).status_code == 400


# ---------------------------------------------------------------------------
# 时间线
# ---------------------------------------------------------------------------


def test_timeline_marks_error_spike_and_links_lines(demo) -> None:
    db, log, _ = demo
    data = client_for(db).get(f"/api/sessions/{SESSION}/timeline", params={"buckets": 60}, headers=OWNER).json()
    assert data["timezone"] == "+08:00" and data["bucket_seconds"] == 10
    assert data["totals"] == {"events": 73, "warn": 1, "error": 12}
    spikes = [data["buckets"][i] for i in data["spikes"]]
    assert [b["start"] for b in spikes] == [
        "2026-06-09T10:05:00+08:00", "2026-06-09T10:05:10+08:00", "2026-06-09T10:05:20+08:00",
    ]
    first = spikes[0]["first_error"][0]
    assert first == {"source": str(log), "line": first_line(log, "payment failed")}
    top = spikes[0]["top"][0]
    assert top["count"] == 4
    assert "13812345678" not in top["signature"]  # 签名数字归一化 + 脱敏，不把手机号带到浏览器
    # 堆栈续行不单独计数：每条错误后面的 Traceback 三行不会把 total 撑大
    assert spikes[0]["total"] == 5


def test_timeline_respects_session_timezone_for_naive_stamps(tmp_path: Path) -> None:
    from datetime import UTC

    from log_agent.timefilter import parse_timezone

    log = spike_log(tmp_path / "app.log")
    utc = build_timeline([str(log)], UTC, target_buckets=60)
    shanghai = build_timeline([str(log)], parse_timezone("+08:00"), target_buckets=60)
    assert utc["buckets"][0]["start"] == "2026-06-09T10:00:00+00:00"
    assert shanghai["buckets"][0]["start"] == "2026-06-09T10:00:00+08:00"
    assert utc["buckets"][0]["start_ts"] - shanghai["buckets"][0]["start_ts"] == 8 * 3600


def test_timeline_without_timestamps_is_empty(tmp_path: Path) -> None:
    log = tmp_path / "plain.log"
    log.write_text("ERROR boom\nINFO fine\n", encoding="utf-8")
    data = build_timeline([str(log)])
    assert data["buckets"] == [] and data["totals"]["events"] == 0


def test_pick_width_uses_nice_steps() -> None:
    assert pick_width(600, 60) == 10
    assert pick_width(3600, 120) == 30
    assert pick_width(86400 * 3, 120) == 3600


# ---------------------------------------------------------------------------
# 证据原文
# ---------------------------------------------------------------------------


def test_source_context_for_log_and_code(demo) -> None:
    db, log, _ = demo
    client = client_for(db)
    line = first_line(log, "payment failed")
    data = client.get(f"/api/sessions/{SESSION}/source", params={"source": "app.log", "start": line, "end": line + 3,
                                                                  "before": 2, "after": 2}, headers=OWNER).json()
    assert data["kind"] == "log"
    assert [row["n"] for row in data["lines"]] == list(range(line - 2, line + 6))
    hit = next(row["text"] for row in data["lines"] if row["n"] == line)
    assert "138****5678" in hit and "13812345678" not in hit
    code = client.get(f"/api/sessions/{SESSION}/source", params={"source": "app/order.py", "start": 3},
                      headers=OWNER).json()
    assert code["kind"] == "code" and code["lines"][-1]["text"].strip() == "return order['order_id']"


@pytest.mark.parametrize("source", ["../../etc/passwd", "/etc/passwd", "repo/../../secret.txt", "other.log"])
def test_source_outside_session_is_rejected(demo, source: str) -> None:
    db, log, _ = demo
    (log.parent / "secret.txt").write_text("password=hunter2\n", encoding="utf-8")
    response = client_for(db).get(f"/api/sessions/{SESSION}/source", params={"source": source, "start": 1}, headers=OWNER)
    assert response.status_code == 404


def test_owner_no_redact_shows_raw_but_share_stays_redacted(demo) -> None:
    db, log, _ = demo
    line = first_line(log, "payment failed")
    client = client_for(db, redact_owner=False)
    raw = client.get(f"/api/sessions/{SESSION}/source", params={"source": "app.log", "start": line, "before": 0,
                                                                 "after": 0}, headers=OWNER).json()
    assert "13812345678" in raw["lines"][0]["text"]
    token = client.post(f"/api/sessions/{SESSION}/shares", json={"ttl_hours": 1}, headers=WRITE).json()["token"]
    shared = client.get(f"/api/share/{token}/source", params={"source": "app.log", "start": line, "before": 0,
                                                               "after": 0}).json()
    assert "13812345678" not in shared["lines"][0]["text"]


# ---------------------------------------------------------------------------
# 分享链接
# ---------------------------------------------------------------------------


def test_share_link_is_read_only_and_scoped(demo, tmp_path: Path, code_repo: Path) -> None:
    db, *_ = demo
    other_log = spike_log(tmp_path / "other.log")
    seed_session(db, other_log, code_repo, name="chat-other")
    client = client_for(db)
    share = client.post(f"/api/sessions/{SESSION}/shares", json={"ttl_hours": 24}, headers=WRITE).json()
    assert share["url"].endswith(f"/s/{share['token']}") and share["hint"] == share["token"][-4:]
    guest = TestClient(client.app)  # 没有 Cookie、没有令牌的同事
    detail = guest.get(f"/api/share/{share['token']}").json()
    assert detail["name"] == SESSION and detail["read_only"] is True
    assert guest.get(f"/api/share/{share['token']}/timeline").status_code == 200
    assert guest.get(f"/api/share/{share['token']}/turns/1").status_code == 200
    # 分享链接不能看别的会话、不能续问、不能管理链接
    assert guest.get("/api/sessions").status_code == 401
    assert guest.get("/api/sessions/chat-other").status_code == 401
    assert guest.get(f"/api/share/{share['token']}/source", params={"source": "other.log", "start": 1}).status_code == 404
    assert guest.post(f"/api/share/{share['token']}/chat", json={"question": "hi"}).status_code in (404, 405)
    assert guest.post(f"/api/sessions/{SESSION}/chat", json={"question": "hi"}).status_code == 401
    assert guest.get(f"/api/sessions/{SESSION}/shares").status_code == 401
    # 库里只存哈希
    with sqlite3.connect(str(db)) as conn:
        stored = conn.execute("SELECT token_hash FROM log_agent_shares").fetchone()[0]
    assert share["token"] not in stored


def test_share_revoke_expiry_and_session_delete(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    first = client.post(f"/api/sessions/{SESSION}/shares", json={"ttl_hours": 1}, headers=WRITE).json()
    assert [s["id"] for s in client.get(f"/api/sessions/{SESSION}/shares", headers=OWNER).json()] == [first["id"]]
    assert client.delete(f"/api/sessions/{SESSION}/shares/{first['id']}", headers=WRITE).status_code == 200
    assert client.get(f"/api/share/{first['token']}").status_code == 404

    second = client.post(f"/api/sessions/{SESSION}/shares", json={"ttl_hours": 1}, headers=WRITE).json()
    past = (datetime.now() - timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(str(db)) as conn:
        conn.execute("UPDATE log_agent_shares SET expires_at = ?", (past,))
    assert client.get(f"/api/share/{second['token']}").status_code == 404
    assert client.post(f"/api/sessions/{SESSION}/shares", json={"ttl_hours": 5}, headers=WRITE).status_code == 400

    third = client.post(f"/api/sessions/{SESSION}/shares", json={"ttl_hours": None}, headers=WRITE).json()
    assert third["expires_at"] is None
    with sqlite3.connect(str(db)) as conn:
        assert SessionStore(conn).delete(SESSION)
        assert ShareStore(conn).resolve(third["token"]) is None


def test_session_delete_without_share_table(tmp_path: Path) -> None:
    conn = sqlite3.connect(str(tmp_path / "s.db"))
    store = SessionStore(conn)
    store.touch("s", ["a.log"], [], "m")
    assert store.delete("s")


# ---------------------------------------------------------------------------
# 网页续问（AG-UI 事件流）
# ---------------------------------------------------------------------------


@pytest.fixture
def scripted_agent(monkeypatch: pytest.MonkeyPatch, demo):
    _, log, _ = demo
    real = agent_module.build_agent
    seen: dict = {}

    def build(**kwargs):
        seen.update(kwargs)
        script = [
            AIMessage(content="先看一下整体分布。", tool_calls=[
                {"name": "log_overview", "args": {"path": str(log)}, "id": "o1", "type": "tool_call"}]),
            tool_call("search_logs", "s1", path=str(log), pattern="KeyError", regex=False),
            AIMessage(content=report_text(log)),
        ]
        return real(model=ScriptedChatModel(script=script), checkpointer=kwargs.get("checkpointer"))

    monkeypatch.setattr(agent_module, "build_agent", build)
    return seen


def test_chat_streams_ag_ui_events_and_records_turn(demo, scripted_agent) -> None:
    db, log, code = demo
    client = client_for(db, agent_factory=cli._web_agent_factory)
    body = {"threadId": SESSION, "runId": "run-1", "state": {}, "tools": [], "context": [], "forwardedProps": {},
            "messages": [{"id": "u1", "role": "user", "content": "10:05 的尖峰是什么原因？"}]}
    with client.stream("POST", f"/api/sessions/{SESSION}/chat", json=body, headers=WRITE) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = sse_events(response)

    kinds = [e["type"] for e in events]
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
    assert events[0]["threadId"] == SESSION and events[0]["runId"] == "run-1"
    # 工具只走 CUSTOM 事件：TOOL_CALL_* 会让 TanStack AI 以为要客户端续接、自动再发一次请求
    assert not any(k.startswith("TOOL_CALL") for k in kinds)
    custom = [(e["name"], e["value"]) for e in events if e["type"] == "CUSTOM"]
    started = [v["name"] for n, v in custom if n == "log_agent.tool_start"]
    ended = [v for n, v in custom if n == "log_agent.tool_end"]
    assert started == ["log_overview", "search_logs"]
    assert [v["name"] for v in ended] == ["log_overview", "search_logs"] and not any(v["failed"] for v in ended)
    assert next(v for n, v in custom if n == "log_agent.tool_start")["note"] == "先看一下整体分布。"
    text = "".join(e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "一句话结论" in text and "先看一下整体分布" not in text  # 旁白不进入报告正文
    saved = next(v for n, v in custom if n == "log_agent.turn")
    assert saved["turn"] == 2 and saved["status"] == "ok" and saved["structured_status"] == "valid"

    turn = client.get(f"/api/sessions/{SESSION}/turns/2", headers=OWNER).json()
    assert turn["question"] == "10:05 的尖峰是什么原因？"
    assert turn["logs"] == [str(log)] and turn["code"] == [str(code)]
    assert [t["name"] for t in turn["tool_calls"]] == ["log_overview", "search_logs"]
    assert turn["evidence_check"]["verified"] == 2  # 续问的报告也经过同一套证据核对
    assert turn["settings"]["timezone"] == "+08:00"
    assert scripted_agent["model"] == "openai:gpt-test"


def test_chat_accepts_plain_question_and_rejects_empty(demo, scripted_agent) -> None:
    db, *_ = demo
    client = client_for(db, agent_factory=cli._web_agent_factory)
    assert client.post(f"/api/sessions/{SESSION}/chat", json={"messages": []}, headers=WRITE).status_code == 400
    with client.stream("POST", f"/api/sessions/{SESSION}/chat", json={"question": "再看看"}, headers=WRITE) as response:
        assert sse_events(response)[-1]["type"] == "RUN_FINISHED"


def test_chat_busy_and_unavailable(demo) -> None:
    db, *_ = demo
    assert client_for(db).post(f"/api/sessions/{SESSION}/chat", json={"question": "x"}, headers=WRITE).status_code == 503
    client = client_for(db, agent_factory=cli._web_agent_factory)
    assert runner.TURN_LOCK.acquire(blocking=False)
    try:
        response = client.post(f"/api/sessions/{SESSION}/chat", json={"question": "x"}, headers=WRITE)
        assert response.status_code == 409
    finally:
        runner.TURN_LOCK.release()


def test_chat_reports_missing_logs(demo) -> None:
    db, log, _ = demo
    log.unlink()
    client = client_for(db, agent_factory=cli._web_agent_factory)
    response = client.post(f"/api/sessions/{SESSION}/chat", json={"question": "x"}, headers=WRITE)
    assert response.status_code == 409 and "已不存在" in response.json()["detail"]


def test_chat_cancel_saves_interrupted_turn(demo, monkeypatch: pytest.MonkeyPatch) -> None:
    """浏览器断开时沿用 Ctrl+C 的中断路径：本轮标为 interrupted 并照常存档，锁被释放。"""
    import threading

    db, log, code = demo
    real = agent_module.build_agent
    script = [tool_call("log_overview", "o1", path=str(log)), AIMessage(content=report_text(log))]
    monkeypatch.setattr(agent_module, "build_agent",
                        lambda **kw: real(model=ScriptedChatModel(script=script), checkpointer=kw.get("checkpointer")))
    cancelled = threading.Event()
    cancelled.set()
    events: list[dict] = []
    with sqlite3.connect(str(db)) as conn:
        info = SessionStore(conn).get(SESSION)
    payload = runner.run_turn(db_path=db, info=info, question="停一下", emit=events.append, cancelled=cancelled,
                              agent_factory=cli._web_agent_factory, base_url=None, thread_id=SESSION, run_id="r")
    assert payload["status"] == "interrupted"
    with sqlite3.connect(str(db)) as conn:
        assert SessionStore(conn).turn(SESSION, 2)["status"] == "interrupted"


def test_spa_fallback_and_api_404(demo) -> None:
    db, *_ = demo
    client = client_for(db)
    assert client.get("/api/does-not-exist", headers=OWNER).status_code == 404
    page = client.get(f"/sessions/{SESSION}")
    assert page.status_code in (200, 503)  # 503：源码运行且未构建前端
    assert "text/html" in page.headers["content-type"]
