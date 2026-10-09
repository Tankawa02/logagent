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


def test_session_list_brief_matches_full_payload_brief(demo) -> None:
    from log_agent.web.app import _turn_brief

    db, *_ = demo
    conn = sqlite3.connect(db)
    store = SessionStore(conn)
    store.touch("broken", [], [], "m")
    store.record_turn("broken", "q", 0, {"question": "q"})
    conn.execute("UPDATE log_agent_turns SET payload = 'not json' WHERE name = 'broken'")
    conn.commit()
    info = store.get(SESSION)
    expected = _turn_brief(info.turns, store.last_turn(SESSION))
    briefs = store.last_turn_briefs()
    conn.close()
    assert {"turn": info.turns, **briefs[SESSION]} == expected
    assert "broken" not in briefs
    listed = {s["name"]: s["last"] for s in client_for(db).get("/api/sessions", headers=OWNER).json()}
    assert listed[SESSION] == expected and listed["broken"] is None


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
    # trace：每个工具记录开始偏移，每次模型调用记录耗时，且都落在本轮总耗时之内
    assert all(0 <= t["started"] <= turn["elapsed_seconds"] for t in turn["tool_calls"])
    assert turn["llm_calls"] and all(c["seconds"] >= 0 and c["started"] <= turn["elapsed_seconds"] for c in turn["llm_calls"])

    trace = client.get("/api/trace", headers=OWNER).json()
    latest = trace[0]
    assert (latest["session"], latest["turn"], latest["model"]) == (SESSION, 2, "openai:gpt-test")
    assert latest["tool_count"] == 2 and latest["tools"] == {"log_overview": 1, "search_logs": 1}
    assert latest["llm_calls"] == len(turn["llm_calls"]) and "report" not in latest
    assert [t["turn"] for t in client.get(f"/api/trace?session={SESSION}", headers=OWNER).json()] == [2, 1]
    assert client.get("/api/trace", headers={"Authorization": "Bearer wrong"}).status_code == 401


def _wait_until_done(client, name: str, timeout: float = 10.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        live = client.get(f"/api/sessions/{name}/chat/live", headers=OWNER).json()
        if not live["active"]:
            return live
        time.sleep(0.05)
    raise AssertionError("分析没有在预期时间内结束")


def test_chat_survives_disconnect_and_can_be_rejoined(demo, scripted_agent, monkeypatch) -> None:
    from log_agent.web import runner

    monkeypatch.setattr(runner, "_LIVE_RUNS", {})  # 别的用例刚跑完的轮次还在保留期里
    db, *_ = demo
    client = client_for(db, agent_factory=cli._web_agent_factory)
    assert client.get(f"/api/sessions/{SESSION}/chat/live", headers=OWNER).json() == {"active": False}
    assert client.get(f"/api/sessions/{SESSION}/chat/stream", headers=OWNER).status_code == 404

    # 只读到第一个事件就断开（相当于切到别的会话）：分析不能因此被中断
    with client.stream("POST", f"/api/sessions/{SESSION}/chat", json={"question": "切走再回来"}, headers=WRITE) as response:
        first = next(line for line in response.iter_lines() if line.startswith("data:"))
    assert json.loads(first[5:])["type"] == "RUN_STARTED"

    live = _wait_until_done(client, SESSION)
    assert live["question"] == "切走再回来" and live["finished_at"]
    assert live["turn"] == 2 and live["saved_turn"] == 2
    turn = client.get(f"/api/sessions/{SESSION}/turns/2", headers=OWNER).json()
    assert turn["question"] == "切走再回来" and turn["status"] == "ok"

    # 切回来：重放这一轮完整的事件，和直接看到的一样
    with client.stream("GET", f"/api/sessions/{SESSION}/chat/stream", headers=OWNER) as response:
        replay = sse_events(response)
    kinds = [e["type"] for e in replay]
    assert kinds[0] == "RUN_STARTED" and kinds[-1] == "RUN_FINISHED"
    assert "一句话结论" in "".join(e["delta"] for e in replay if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert any(e["type"] == "CUSTOM" and e["name"] == "log_agent.turn" for e in replay)

    # 只有本人能看、能停；停一个已经结束的轮次什么也不做
    assert client.get(f"/api/sessions/{SESSION}/chat/live").status_code == 401
    assert client.post(f"/api/sessions/{SESSION}/chat/stop", headers=OWNER).status_code == 403
    assert client.post(f"/api/sessions/{SESSION}/chat/stop", headers=WRITE).json()["stopped"] is False


def test_live_run_replay_is_compact_and_expires(monkeypatch) -> None:
    from log_agent.web import runner

    monkeypatch.setattr(runner, "_LIVE_RUNS", {})
    monkeypatch.setattr(runner, "LIVE_RUN_GRACE_SECONDS", 0.05)
    live = runner.LiveRun("s", "q", "run-1", turn=3)
    runner.register_live_run(live)
    for ch in "abc":
        live.emit({"type": "CUSTOM", "name": "log_agent.draft", "value": {"delta": ch}})
    live.emit({"type": "CUSTOM", "name": "log_agent.draft", "value": {"reset": True}})
    for ch in "一二三":
        live.emit({"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": ch})
    live.emit({"type": "CUSTOM", "name": "log_agent.turn", "value": {"turn": 3}})
    assert live.events == [
        {"type": "CUSTOM", "name": "log_agent.draft", "value": {"reset": True}},
        {"type": "TEXT_MESSAGE_CONTENT", "messageId": "m", "delta": "一二三"},
        {"type": "CUSTOM", "name": "log_agent.turn", "value": {"turn": 3}},
    ]
    assert live.describe()["saved_turn"] == 3

    live.finish()
    assert live.wait(1)
    import time

    deadline = time.monotonic() + 2
    while runner.get_live_run("s") is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert runner.get_live_run("s") is None  # 保留期一过自动释放，不需要有人再来查


def test_stop_before_run_registers_is_applied(monkeypatch) -> None:
    from log_agent.web import runner

    monkeypatch.setattr(runner, "_LIVE_RUNS", {})
    monkeypatch.setattr(runner, "_EARLY_STOPS", {})
    # 停止请求先到：记下，不影响别的 run
    assert runner.request_stop("s", "run-early") == {"stopped": True, "pending": True}
    other = runner.LiveRun("s", "q", "run-other")
    runner.register_live_run(other)
    assert not other.cancelled.is_set()
    early = runner.LiveRun("s", "q", "run-early")
    runner.register_live_run(early)
    assert early.cancelled.is_set()


def test_delete_stops_running_turn_first(demo, monkeypatch) -> None:
    import threading

    from log_agent.web import runner

    monkeypatch.setattr(runner, "_LIVE_RUNS", {})
    db, *_ = demo
    client = client_for(db)
    live = runner.LiveRun(SESSION, "q", "run-del")
    runner.register_live_run(live)

    def worker() -> None:
        live.cancelled.wait(5)
        live.finish()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert client.delete(f"/api/sessions/{SESSION}", headers=WRITE).json() == {"deleted": True}
    assert live.cancelled.is_set() and live.done
    assert runner.get_live_run(SESSION) is None


def test_chat_accepts_plain_question_and_rejects_empty(demo, scripted_agent) -> None:
    db, *_ = demo
    client = client_for(db, agent_factory=cli._web_agent_factory)
    assert client.post(f"/api/sessions/{SESSION}/chat", json={"messages": []}, headers=WRITE).status_code == 400
    with client.stream("POST", f"/api/sessions/{SESSION}/chat", json={"question": "再看看"}, headers=WRITE) as response:
        assert sse_events(response)[-1]["type"] == "RUN_FINISHED"


def test_fs_browse_is_owner_only_and_lists_dirs_first(demo) -> None:
    db, log, code = demo
    client = client_for(db)
    assert client.get("/api/fs/list", params={"path": str(log.parent)}).status_code == 401
    assert client.get("/api/fs/places").status_code == 401
    listing = client.get("/api/fs/list", params={"path": str(log.parent)}, headers=OWNER).json()
    assert listing["path"] == str(log.parent.resolve())
    kinds = [e["kind"] for e in listing["entries"]]
    assert kinds == sorted(kinds, key=lambda k: k != "dir")
    assert any(e["name"] == log.name and e["kind"] == "file" and e["size"] > 0 for e in listing["entries"])
    # 传文件路径时列出它所在的目录
    assert client.get("/api/fs/list", params={"path": str(log)}, headers=OWNER).json()["path"] == listing["path"]
    assert client.get("/api/fs/list", params={"path": str(log.parent / "nope")}, headers=OWNER).status_code == 404
    places = client.get("/api/fs/places", headers=OWNER).json()
    assert any(p["kind"] == "recent-code" and p["path"] == str(code) for p in places)


def test_fs_filter_applies_before_truncation(tmp_path, monkeypatch, demo) -> None:
    from log_agent.web import workspace

    db, _, _ = demo
    monkeypatch.setattr(workspace, "MAX_ENTRIES", 5)
    big = tmp_path / "big"
    big.mkdir()
    for i in range(20):
        (big / f"a{i:02d}.log").write_text("x")
    (big / "zz-target.log").write_text("x")
    (big / "zz-sub").mkdir()
    client = client_for(db)
    plain = client.get("/api/fs/list", params={"path": str(big)}, headers=OWNER).json()
    assert plain["truncated"] and plain["total"] == 22
    assert "zz-target.log" not in [e["name"] for e in plain["entries"]]
    found = client.get("/api/fs/list", params={"path": str(big), "q": "TARGET"}, headers=OWNER).json()
    assert [e["name"] for e in found["entries"]] == ["zz-target.log"] and not found["truncated"]
    dirs = client.get("/api/fs/list", params={"path": str(big), "dirs": "true"}, headers=OWNER).json()
    assert [e["name"] for e in dirs["entries"]] == ["zz-sub"]


def test_fs_glob_expands_patterns(tmp_path, demo) -> None:
    db, _, _ = demo
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("app-1.log", "app-2.log", "other.txt"):
        (logs / name).write_text("x")
    client = client_for(db)
    assert client.get("/api/fs/glob", params={"pattern": str(logs / "*.log")}).status_code == 401
    result = client.get("/api/fs/glob", params={"pattern": str(logs / "*.log")}, headers=OWNER).json()
    assert [Path(p).name for p in result["files"]] == ["app-1.log", "app-2.log"]
    assert result["dir"] == str(logs.resolve())
    missing = client.get("/api/fs/glob", params={"pattern": str(logs / "*.gz")}, headers=OWNER)
    assert missing.status_code in (400, 404)


def test_fs_glob_is_bounded_and_filesystem_only(tmp_path, monkeypatch, demo) -> None:
    from log_agent.web import workspace

    db, _, _ = demo
    client = client_for(db)

    def glob(pattern: str):
        return client.get("/api/fs/glob", params={"pattern": pattern}, headers=OWNER)

    # `-` 是命令行的标准输入标记，网页里不能触发读 stdin
    assert glob("-").status_code == 400
    assert glob(str(tmp_path / "**" / "*.log")).status_code == 400

    bracket = tmp_path / "app[old]"
    bracket.mkdir()
    (bracket / "x.log").write_text("x")
    as_dir = glob(str(bracket)).json()
    assert as_dir == {"files": [], "dir": str(bracket.resolve()), "truncated": False}
    as_file = glob(str(bracket / "x.log")).json()
    assert as_file["files"] == [str((bracket / "x.log").resolve())]

    many = tmp_path / "many"
    many.mkdir()
    for i in range(8):
        (many / f"f{i}.log").write_text("x")
    monkeypatch.setattr(workspace, "MAX_ENTRIES", 5)
    assert glob(str(many / "*.log")).status_code == 400
    monkeypatch.setattr(workspace, "MAX_ENTRIES", 2000)
    monkeypatch.setattr(workspace, "GLOB_VISIT_BUDGET", 3)
    assert glob(str(many / "*.log")).status_code == 400


def test_create_session_from_web_then_first_turn(demo, scripted_agent) -> None:
    db, log, code = demo
    client = client_for(db, agent_factory=cli._web_agent_factory, default_model="openai:gpt-test")
    assert client.post("/api/sessions", json={"logs": [str(log)]}, headers=OWNER).status_code == 403  # 缺 CSRF 头
    assert client.post("/api/sessions", json={"logs": []}, headers=WRITE).status_code == 400
    assert client.post("/api/sessions", json={"logs": ["-"]}, headers=WRITE).status_code == 400
    bad_code = client.post("/api/sessions", json={"logs": [str(log)], "code": [str(log.parent / "missing")]}, headers=WRITE)
    assert bad_code.status_code == 400
    assert client.post("/api/sessions", json={"logs": [str(log)], "timezone": "Mars/Base"}, headers=WRITE).status_code == 400

    created = client.post("/api/sessions", json={
        "logs": [str(log.parent / "*.log")], "code": [str(code)], "timezone": "+08:00",
    }, headers=WRITE).json()
    name = created["name"]
    assert created["origin"] == "chat" and created["turns"] == 0 and created["model"] == "openai:gpt-test"
    assert [entry["path"] for entry in created["logs"]] == [str(log)]  # 通配符已展开
    with client.stream("POST", f"/api/sessions/{name}/chat", json={"question": "为什么报错？"}, headers=WRITE) as response:
        events = sse_events(response)
    assert events[-1]["type"] == "RUN_FINISHED"
    turn = client.get(f"/api/sessions/{name}/turns/1", headers=OWNER).json()
    assert turn["question"] == "为什么报错？" and turn["code"] == [str(code)]
    assert turn["settings"]["timezone"] == "+08:00"
    detail = client.get(f"/api/sessions/{name}", headers=OWNER).json()
    assert detail["title"] == "为什么报错？" and detail["turns"] == 1


def test_create_session_unavailable_without_model(demo) -> None:
    db, log, _ = demo
    response = client_for(db).post("/api/sessions", json={"logs": [str(log)]}, headers=WRITE)
    assert response.status_code == 503


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


def test_share_redacts_persisted_no_redact_payload(demo, monkeypatch) -> None:
    from log_agent import redact

    db, *_ = demo
    secret = 'sk-abcdefghijklmnopqrstuvwx'
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        payload = store.turn(SESSION, 1)
        payload['settings']['no_redact'] = True
        payload['question'] = payload['summary'] = payload['report'] = secret
        payload['analysis']['conclusion'] = secret
        payload['analysis']['issues'][0]['evidence'][0]['excerpt'] = secret
        payload['evidence_check']['items'][0]['excerpt'] = secret
        payload['tool_calls'] = [{'name': 'search_logs', 'args': {'pattern': secret}, 'output': [secret]}]
        payload['extra'] = {'nested': [secret, 42, False, None]}
        store.record_turn(SESSION, secret, 0, payload)
    monkeypatch.setattr(redact, '_enabled', False)
    client = client_for(db, redact_owner=False)
    token = client.post(f'/api/sessions/{SESSION}/shares', json={'ttl_hours': 1}, headers=WRITE).json()['token']
    for suffix in ['', '/turns/2', '/export?turn=2&format=json', '/export?turn=2&format=markdown']:
        response = client.get(f'/api/share/{token}{suffix}')
        assert response.status_code == 200
        assert secret not in response.text
        assert '已脱敏' in response.text
        owner = client.get(f'/api/sessions/{SESSION}{suffix}', headers=OWNER)
        assert owner.status_code == 200 and secret in owner.text
    shared = client.get(f'/api/share/{token}/turns/2').json()
    assert shared['extra']['nested'] == ['sk-[已脱敏]', 42, False, None]
    assert shared['analysis']['issues'][0]['evidence'][0]['excerpt'] == 'sk-[已脱敏]'
    with sqlite3.connect(str(db)) as conn:
        assert SessionStore(conn).turn(SESSION, 2) == payload


def test_shared_source_masks_interior_private_key_lines(demo, monkeypatch) -> None:
    from log_agent import redact

    db, log, _ = demo
    log.write_text('INFO start\n-----BEGIN PRIVATE KEY-----\nabcdefgh12345678\nijklmnop12345678\n'
                   '-----END PRIVATE KEY-----\nINFO end\n', encoding='utf-8')
    monkeypatch.setattr(redact, '_enabled', False)
    client = client_for(db, redact_owner=False)
    token = client.post(f'/api/sessions/{SESSION}/shares', json={'ttl_hours': 1}, headers=WRITE).json()['token']
    params = {'source': log.name, 'start': 3, 'end': 4, 'before': 0, 'after': 0}
    shared = client.get(f'/api/share/{token}/source', params=params)
    assert shared.status_code == 200
    assert shared.json()['lines'] == [{'n': 3, 'text': '[私钥已脱敏]'}, {'n': 4, 'text': '[私钥已脱敏]'}]
    assert shared.json()['has_more'] is True
    owner = client.get(f'/api/sessions/{SESSION}/source', params=params, headers=OWNER).json()
    assert owner['lines'][0]['text'] == 'abcdefgh12345678'


def test_timeline_signature_links_stay_in_their_bucket(tmp_path) -> None:
    log = tmp_path / 'recurring.log'
    log.write_text('10:00:00 ERROR connection failed\n10:00:01 ERROR connection failed\n'
                   '10:10:00 ERROR connection failed\n', encoding='utf-8')
    for target in [10, 60, 600]:
        data = build_timeline([log], target_buckets=target)
        buckets = [b for b in data['buckets'] if b['error']]
        assert buckets[0]['top'][0]['line'] == 1
        assert buckets[-1]['top'][0]['line'] == 3


@pytest.mark.parametrize('continuation', [
    '    at com.example.error.Handler.run(23:59:59.java:42)',
    'Caused by: timeout after 23:59:59',
    '  File "23:59:59.py", line 42, in run',
])
def test_timeline_ignores_clocks_in_stack_continuations(tmp_path, continuation) -> None:
    log = tmp_path / 'stack.log'
    log.write_text(f'10:00:00 ERROR failed\n{continuation}\n10:00:01 INFO done\n', encoding='utf-8')
    data = build_timeline([log])
    assert data['totals'] == {'events': 2, 'warn': 0, 'error': 1}
    assert data['files'][0]['timestamped'] == 2
    assert len(data['buckets']) == 2


@pytest.mark.parametrize('view', ['brief', 'detailed', 'ticket'])
def test_shared_payload_masks_pem_split_across_list_items(demo, monkeypatch, view) -> None:
    from log_agent import redact

    db, *_ = demo
    body = 'QUJDREVGR0hJSktMTU5P'
    parts = ['-----BEGIN PRIVATE KEY-----', body + '\nZw==', '', '-----END PRIVATE KEY-----']
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        payload = store.turn(SESSION, 1)
        payload['settings']['no_redact'] = True
        payload['analysis']['next_steps'] = parts
        payload['tool_calls'] = [{'output': [0, *parts, {'nested': parts}, None]}]
        store.record_turn(SESSION, 'split PEM', 0, payload)
    monkeypatch.setattr(redact, '_enabled', False)
    client = client_for(db, redact_owner=False)
    token = client.post(f'/api/sessions/{SESSION}/shares', json={'ttl_hours': 1}, headers=WRITE).json()['token']
    for suffix in ['/turns/2', f'/export?turn=2&format=json&view={view}',
                   f'/export?turn=2&format=markdown&view={view}']:
        response = client.get(f'/api/share/{token}{suffix}')
        assert response.status_code == 200
        assert body not in response.text and 'Zw==' not in response.text
    turn = client.get(f'/api/share/{token}/turns/2').json()
    assert turn['analysis']['next_steps'] == [redact.KEY_MASK, f'{redact.KEY_MASK}\n{redact.KEY_MASK}',
                                              redact.KEY_MASK, redact.KEY_MASK]
    assert turn['tool_calls'][0]['output'][0] == 0
    assert turn['tool_calls'][0]['output'][-1] is None
    assert client.get(f'/api/sessions/{SESSION}/turns/2', headers=OWNER).json() == {'turn': 2, **payload}
    with sqlite3.connect(str(db)) as conn:
        assert SessionStore(conn).turn(SESSION, 2) == payload


@pytest.mark.parametrize('filename', ['192.168.1.3.log', 'server-192.168.1.3-log.txt'])
def test_shared_evidence_retains_resolvable_source(tmp_path, code_repo, filename) -> None:
    log = spike_log(tmp_path / filename)
    db = tmp_path / 'sessions.db'
    seed_session(db, log, code_repo, no_redact=True)
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        payload = store.turn(SESSION, 1)
        payload['analysis']['issues'][0]['evidence'][0]['excerpt'] = 'peer 192.168.1.3 password=hunter2'
        payload['extra'] = {'source': 'password=hunter2'}  # unresolvable free text is still redacted
        store.record_turn(SESSION, 'source link', 0, payload)
    client = client_for(db)
    token = client.post(f'/api/sessions/{SESSION}/shares', json={'ttl_hours': 1}, headers=WRITE).json()['token']
    for suffix in ['/turns/2', '/export?turn=2&format=json']:
        response = client.get(f'/api/share/{token}{suffix}')
        assert response.status_code == 200
        assert 'hunter2' not in response.text
        evidence = response.json()['analysis']['issues'][0]['evidence'][0]
        assert evidence['source'] == filename
        assert '192.168.1.3' not in evidence['excerpt']
        source = client.get(f'/api/share/{token}/source', params={
            'source': evidence['source'], 'start': evidence['line_start'], 'before': 0, 'after': 0,
        })
        assert source.status_code == 200 and source.json()['lines']


def test_late_source_window_uses_compact_cached_pem_ranges(tmp_path, monkeypatch) -> None:
    import tracemalloc

    from log_agent.logfile import LogFile, open_log
    from log_agent.web.sources import read_context

    log = tmp_path / 'large.log'
    with log.open('w') as output:
        for _ in range(30_000):
            output.write('INFO ' + 'x' * 256 + '\n')
        output.write('-----BEGIN PRIVATE KEY-----\nQUJDREVGR0hJSktMTU5P\nZw==\n-----END PRIVATE KEY-----\n')
    calls = []
    real_iter = LogFile.iter_lines

    def tracked(self, start_line=1):
        calls.append(start_line)
        yield from real_iter(self, start_line)

    monkeypatch.setattr(LogFile, 'iter_lines', tracked)
    tracemalloc.start()
    try:
        for _ in range(2):
            data = read_context([str(log)], [], log.name, 30_002, before=0, after=0)
            assert data['lines'] == [{'n': 30_002, 'text': '[私钥已脱敏]'}]
            assert data['total_lines'] == 30_004 and data['has_more']
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 4 * 1024 * 1024  # no full-log string/dict; input is >7 MiB
    assert calls == [1, 30_002, 30_002]  # a single scan, then indexed window reads
    assert open_log(log).scan_cache['web-private-key-ranges'] == [(30_001, 30_004)]


def test_source_pem_cache_invalidates_after_appending_end_marker(tmp_path) -> None:
    from log_agent.web.sources import read_context

    log = tmp_path / 'growing.log'
    log.write_text('INFO start\nQUJDREVGR0hJSktMTU5P\n', encoding='utf-8')
    first = read_context([str(log)], [], log.name, 2, before=0, after=0)
    assert first['lines'][0]['text'] == 'QUJDREVGR0hJSktMTU5P'
    with log.open('a') as output:
        output.write('-----END PRIVATE KEY-----\n')
    second = read_context([str(log)], [], log.name, 2, before=0, after=0)
    assert second['lines'] == [{'n': 2, 'text': '[私钥已脱敏]'}]


def test_payload_redaction_keeps_list_boundaries_when_rules_remove_newlines() -> None:
    from log_agent.web.app import _redacted_copy

    original = ['Bearer\nabcdefghijklmnop', 'normal next item', 'last item']
    assert _redacted_copy(original, True) == ['Bearer [已脱敏]', 'normal next item', 'last item']


def test_shared_metadata_hides_paths_and_settings_with_resolvable_handles(demo, tmp_path) -> None:
    db, log, code = demo
    duplicate = tmp_path / 'other' / log.name
    duplicate.parent.mkdir()
    duplicate.write_text('10:00:00 ERROR second log\n', encoding='utf-8')
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        info = store.get(SESSION)
        store.touch(SESSION, [str(log), str(duplicate)], [str(code)], info.model,
                    {**info.settings, 'base_url': 'https://internal.example/v1',
                     'private_setting': 'do-not-disclose', 'no_redact': True})
    client = client_for(db)
    token = client.post(f'/api/sessions/{SESSION}/shares', json={}, headers=WRITE).json()['token']
    detail = client.get(f'/api/share/{token}')
    assert detail.status_code == 200
    assert str(tmp_path) not in detail.text
    assert 'internal.example' not in detail.text and 'do-not-disclose' not in detail.text
    assert set(detail.json()['settings']) <= {'since', 'until', 'timezone', 'baseline'}
    handles = [item['path'] for item in detail.json()['logs']]
    assert len(set(handles)) == 2
    for handle in handles:
        response = client.get(f'/api/share/{token}/source', params={'source': handle, 'start': 1})
        assert response.status_code == 200 and response.json()['lines']
        assert str(tmp_path) not in response.text
    timeline = client.get(f'/api/share/{token}/timeline')
    assert str(tmp_path) not in timeline.text
    assert timeline.json()['files'][1]['source'] == handles[1]


@pytest.mark.parametrize('dated_first', [True, False])
def test_timeline_mixed_dated_and_clock_only_entries(tmp_path, dated_first) -> None:
    log = tmp_path / 'mixed.log'
    lines = ['2026-10-01 10:00:00 ERROR dated', '10:00:01 ERROR clock-only']
    log.write_text('\n'.join(lines if dated_first else reversed(lines)), encoding='utf-8')
    data = build_timeline([log])
    assert data['time_only'] is False
    assert data['totals']['error'] == 2
    assert data['bucket_seconds'] == 1
    assert len(data['buckets']) == 2
    assert data['start'].startswith('2026-10-01')


def test_web_auto_encoding_ignores_cli_global(tmp_path, monkeypatch) -> None:
    from log_agent import logfile
    from log_agent.web.sources import read_context

    log = tmp_path / 'utf8.log'
    log.write_text('10:00:00 ERROR 支付失败\n', encoding='utf-8')
    monkeypatch.setattr(logfile, '_forced_encoding', 'utf-16')
    assert logfile.open_log(log).encoding == 'utf-16'
    assert build_timeline([log])['totals']['error'] == 1
    source = read_context([str(log)], [], log.name, 1)
    assert source['lines'][0]['text'] == '10:00:00 ERROR 支付失败'


@pytest.mark.parametrize('redact_owner', [True, False])
def test_live_tool_events_apply_owner_policy_recursively(monkeypatch, redact_owner) -> None:
    import threading
    from types import SimpleNamespace

    from log_agent import redact

    monkeypatch.setattr(redact, '_enabled', False)
    events = []
    renderer = runner.WebStreamRenderer(events.append, threading.Event(), redact_owner=redact_owner)
    secret = 'sk-' + 'a' * 32
    renderer.pending_note = secret
    handle = SimpleNamespace(tool_name='search_logs', tool_call_id='tool-1',
                             input={'nested': {'values': [secret], secret: 'value'}}, error=secret)
    renderer._on_tool(handle)
    renderer._record(renderer.running[-1], subagent=secret)
    assert [e['name'] for e in events] == ['log_agent.tool_start', 'log_agent.tool_end']
    for event in events:
        assert (secret in json.dumps(event)) is not redact_owner
    assert renderer.records[-1].summary == secret  # View redaction does not mutate the saved record.


@pytest.mark.parametrize('failure', [False, True])
def test_web_turn_restores_global_settings(demo, monkeypatch, failure) -> None:
    import threading

    from log_agent import logfile, redact, timefilter
    from log_agent.render import TurnResult

    db, _, _ = demo
    with sqlite3.connect(str(db)) as conn:
        info = SessionStore(conn).get(SESSION)
    monkeypatch.setattr(logfile, '_forced_encoding', 'gb18030')
    monkeypatch.setattr(timefilter, '_default_timezone', timefilter.parse_timezone('+03:00'))
    window = timefilter.parse_window('01:00', '02:00')
    monkeypatch.setattr(timefilter, '_default_window', window)
    monkeypatch.setattr(redact, '_enabled', False)
    before = (logfile._forced_encoding, timefilter.default_timezone(), timefilter.default_window(), redact.is_enabled())

    def build(**kwargs):
        assert logfile._forced_encoding is None
        assert redact.is_enabled()
        if failure:
            raise RuntimeError('build failed')
        return object()

    monkeypatch.setattr(runner.WebStreamRenderer, 'run', lambda *a, **kw: TurnResult(report='done'))
    kwargs = dict(db_path=db, info=info, question='q', emit=lambda e: None, cancelled=threading.Event(),
                  agent_factory=build, base_url=None, thread_id=SESSION, run_id='restore')
    if failure:
        with pytest.raises(RuntimeError, match='build failed'):
            runner.run_turn(**kwargs)
    else:
        runner.run_turn(**kwargs)
    assert (logfile._forced_encoding, timefilter.default_timezone(),
            timefilter.default_window(), redact.is_enabled()) == before


def test_shared_absolute_code_source_remains_resolvable(demo) -> None:
    db, _, code = demo
    target = code / '192.168.1.3.py'
    target.write_text('print("hello")\n', encoding='utf-8')
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        payload = store.turn(SESSION, 1)
        payload['analysis']['issues'][0]['evidence'][0].update(source=str(target), line_start=1, line_end=1)
        store.record_turn(SESSION, 'code source', 0, payload)
    client = client_for(db)
    token = client.post(f'/api/sessions/{SESSION}/shares', json={}, headers=WRITE).json()['token']
    response = client.get(f'/api/share/{token}/turns/2')
    assert str(code) not in response.text
    source = response.json()['analysis']['issues'][0]['evidence'][0]['source']
    assert source == 'code/0/192.168.1.3.py'
    response = client.get(f'/api/share/{token}/source', params={'source': source, 'start': 1})
    assert response.status_code == 200 and response.json()['lines'][0]['text'] == 'print("hello")'
    assert str(code) not in response.text
    assert client.get(f'/api/share/{token}/source', params={
        'source': 'code/0/../../not-registered', 'start': 1,
    }).status_code == 404


def test_web_redaction_setting_prefers_current_metadata(demo) -> None:
    db, log, code = demo
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        store.touch(SESSION, [str(log)], [str(code)], 'test', {'no_redact': True})
        assert runner.session_no_redact(store, SESSION) is True


@pytest.mark.parametrize('stamps', [
    ['23:59:59', '2026-01-02 00:00:01', '00:00:02'],
    ['2026-01-01 23:59:59', '00:00:01', '00:00:02'],
    ['23:59:59', '00:00:01', '2026-01-02 00:00:02'],
])
@pytest.mark.parametrize('offset', [0, 8, -5])
def test_timeline_mixed_dates_across_midnight(tmp_path, stamps, offset) -> None:
    from datetime import datetime, timedelta, timezone

    from log_agent.web.timeline import scan_file

    log = tmp_path / 'midnight.log'
    log.write_text('\n'.join(f'{stamp} ERROR failure' for stamp in stamps), encoding='utf-8')
    tz = timezone(timedelta(hours=offset))
    scan = scan_file(log, tz)
    assert scan.events == 3 and not scan.time_only
    assert max(scan.seconds) - min(scan.seconds) == 3
    assert datetime.fromtimestamp(min(scan.seconds), tz).replace(tzinfo=None) == datetime(2026, 1, 1, 23, 59, 59)


@pytest.mark.parametrize('dated', [False, True])
def test_timeline_scans_file_only_once(dated) -> None:
    from datetime import UTC

    from log_agent.web.timeline import _scan

    class Log:
        calls = 0

        def iter_lines(self, start):
            self.calls += 1
            assert self.calls == 1
            yield 1, '23:59:59 ERROR leading'
            yield 2, ('2026-01-02 ' if dated else '') + '00:00:01 WARN next'

    scan = _scan(Log(), 'test', UTC)
    assert scan.events == 2
    assert max(scan.seconds) - min(scan.seconds) == 2
    assert scan.time_only is not dated


@pytest.mark.parametrize('saved_url', [None, 'https://saved.example/v1'])
def test_web_turn_prefers_saved_endpoint(demo, monkeypatch, saved_url) -> None:
    import threading

    from log_agent.render import TurnResult

    db, _, _ = demo
    with sqlite3.connect(str(db)) as conn:
        info = SessionStore(conn).get(SESSION)
    info.settings['base_url'] = saved_url
    seen = []
    monkeypatch.setattr(runner.WebStreamRenderer, 'run', lambda *a, **kw: TurnResult(report='done'))
    runner.run_turn(db_path=db, info=info, question='q', emit=lambda e: None, cancelled=threading.Event(),
                    agent_factory=lambda **kwargs: seen.append(kwargs), base_url='https://server.example/v1',
                    thread_id=SESSION, run_id='endpoint')
    assert seen[0]['base_url'] == (saved_url or 'https://server.example/v1')
    with sqlite3.connect(str(db)) as conn:
        assert 'base_url' not in SessionStore(conn).turn(SESSION, 2)['settings']


def test_shared_windows_source_uses_url_separators(demo) -> None:
    from log_agent.web.app import _shared_copy

    db, _, _ = demo
    with sqlite3.connect(str(db)) as conn:
        info = SessionStore(conn).get(SESSION)
    info.code = [r'C:\Users\owner\project']
    value = {'source': r'C:\Users\owner\project\nested\file.py', 'text': r'regex \d+'}
    assert _shared_copy(value, info) == {'source': 'code/0/nested/file.py', 'text': r'regex \d+'}


def test_owner_export_removes_legacy_connection_metadata(demo) -> None:
    db, _, _ = demo
    with sqlite3.connect(str(db)) as conn:
        store = SessionStore(conn)
        payload = store.turn(SESSION, 1)
        payload['settings']['base_url'] = 'https://user:secret@internal.example/v1'
        store.record_turn(SESSION, 'legacy', 0, payload)
    client = client_for(db)
    for path in ['turns/2', 'export?turn=2&format=json']:
        response = client.get(f'/api/sessions/{SESSION}/{path}', headers=WRITE)
        assert response.status_code == 200
        assert 'base_url' not in response.json()['settings']


@pytest.mark.parametrize("value", ['"OFFF"', "1"])
def test_serve_rejects_invalid_memory_mode(tmp_path, monkeypatch, value: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f"memory = {value}\n", encoding="utf-8")
    monkeypatch.setenv("LOG_AGENT_CONFIG", str(config))
    result = CliRunner().invoke(cli.app, ["serve", "--no-token"])
    assert result.exit_code == 2
    assert "memory" in result.output


@pytest.mark.parametrize(
    ("env", "platform", "expected"),
    [
        ({"DISPLAY": ":0"}, "linux", True),
        ({}, "linux", False),
        ({"WAYLAND_DISPLAY": "wayland-0"}, "linux", True),
        ({"DISPLAY": ":0", "SSH_CONNECTION": "1.2.3.4 22 5.6.7.8 22"}, "linux", False),
        ({}, "darwin", True),
        ({"SSH_TTY": "/dev/pts/1"}, "darwin", False),
        ({}, "win32", True),
    ],
)
def test_serve_skips_browser_when_headless(monkeypatch, env: dict[str, str], platform: str, expected: bool) -> None:
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(cli.sys, "platform", platform)
    assert cli._can_open_browser() is expected


def test_serve_opens_browser_by_default(monkeypatch) -> None:
    import uvicorn

    calls: list[tuple] = []
    monkeypatch.setattr(uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "_can_open_browser", lambda: True)
    monkeypatch.setattr(cli, "_open_browser_when_ready", lambda *args: calls.append(args))
    assert CliRunner().invoke(cli.app, ["serve", "--port", "8799"]).exit_code == 0
    assert len(calls) == 1 and calls[0][0].startswith("http://127.0.0.1:8799/?token=")
    assert CliRunner().invoke(cli.app, ["serve", "--no-open"]).exit_code == 0
    assert len(calls) == 1


class _PiecewiseStream:
    """模拟模型按小片段输出的一次消息流。"""

    def __init__(self, pieces: list[str], tool_calls: list | None = None) -> None:
        self.pieces = pieces
        self.output = AIMessage(content="".join(pieces), tool_calls=tool_calls or [])

    @property
    def text(self):
        yield from self.pieces


def test_report_text_streams_to_browser_piece_by_piece() -> None:
    import threading

    from log_agent.web.runner import WebStreamRenderer

    events: list[dict] = []
    renderer = WebStreamRenderer(events.append, threading.Event(), redact_owner=False)
    # 旁白：工具调用前的一句话，只进草稿，不进报告正文
    renderer._on_message(_PiecewiseStream(["先看", "一下分布。"], tool_calls=[
        {"name": "log_overview", "args": {}, "id": "o1", "type": "tool_call"}]))
    report = ["## 一句话", "结论\n\n", "支付服务在 10:05 ", "连接池耗尽", "，导致超时。"]
    renderer._on_message(_PiecewiseStream(report))
    renderer.close_message()

    deltas = [e["delta"] for e in events if e["type"] == "TEXT_MESSAGE_CONTENT"]
    # 判定为正文后逐片推送，而不是等空行凑满一整段才推
    assert len(deltas) >= 4
    assert "".join(deltas) == "".join(report)
    assert not any("先看" in d for d in deltas)
    assert renderer.answer_parts  # 存档用的分块记账照常进行
    assert [e["type"] for e in events if e["type"].startswith("TEXT_MESSAGE")][-1] == "TEXT_MESSAGE_END"


def test_finishing_phases_are_announced_after_report_text() -> None:
    import threading

    from log_agent.web.runner import WebStreamRenderer

    events: list[dict] = []
    renderer = WebStreamRenderer(events.append, threading.Event(), redact_owner=False)
    pieces = ["## 一句话", "结论\n\n", "连接池耗尽。\n\n", "```log-agent", "-report\n", '{"a": 1}\n', "```\n"]
    renderer._on_message(_PiecewiseStream(pieces))
    renderer._on_phase("verifying")
    renderer.phase("saving")
    renderer.phase("saving")

    phases = [e["value"]["phase"] for e in events if e.get("type") == "CUSTOM" and e.get("name") == "log_agent.phase"]
    # 机器附录一开始写就提示「整理结构化报告」，之后依次核对、保存，同一阶段不重复推送
    assert phases == ["structuring", "verifying", "saving"]
    first_phase = next(i for i, e in enumerate(events) if e.get("name") == "log_agent.phase")
    text_before = "".join(e["delta"] for e in events[:first_phase] if e["type"] == "TEXT_MESSAGE_CONTENT")
    assert "```log-agent" in text_before
