"""网页 skill / 记忆管理接口。"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from log_agent.memory import MemorySession, MemoryStore  # noqa: E402
from log_agent.web.app import WebConfig, create_app  # noqa: E402

TOKEN = "manage-token"
OWNER = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**OWNER, "X-Log-Agent-Request": "1"}


def skill_md(name: str, description: str = "排查支付超时") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n1. 先看网关日志\n"


@pytest.fixture
def env(tmp_path: Path):
    home = tmp_path / "home-skills"
    extra = tmp_path / "extra-skills"
    (extra / "payment-timeout").mkdir(parents=True)
    (extra / "payment-timeout" / "SKILL.md").write_text(skill_md("payment-timeout", "自定义版本"), encoding="utf-8")
    (extra / "broken").mkdir()
    (extra / "broken" / "SKILL.md").write_text("没有 frontmatter", encoding="utf-8")
    config = WebConfig(
        db_path=tmp_path / "sessions.db", token=TOKEN, skill_dirs=(str(extra),), skills_home=home,
        skills_cwd=tmp_path, memory_path=tmp_path / "memory.db",
    )
    return TestClient(create_app(config)), home, extra, tmp_path / "memory.db"


def test_manage_routes_require_owner_and_csrf(env) -> None:
    client, *_ = env
    assert client.get("/api/skills").status_code == 401
    assert client.get("/api/memory").status_code == 401
    body = {"source": "user", "name": "x", "content": skill_md("x")}
    assert client.post("/api/skills", json=body, headers=OWNER).status_code == 403


def test_skill_lifecycle_and_override(env) -> None:
    client, home, _, _ = env
    created = client.post(
        "/api/skills", json={"source": "user", "name": "payment-timeout", "content": skill_md("payment-timeout")}, headers=WRITE,
    )
    assert created.status_code == 200, created.text
    assert (home / "payment-timeout" / "SKILL.md").is_file()

    sources = {s["key"]: s for s in client.get("/api/skills", headers=OWNER).json()["sources"]}
    user_skill = sources["user"]["skills"][0]
    # 自定义目录优先级更高，用户级同名 skill 被覆盖
    assert user_skill["shadowed_by"] == sources["extra-1"]["label"]
    broken = next(s for s in sources["extra-1"]["skills"] if s["name"] == "broken")
    assert broken["problems"]

    updated = client.put(
        "/api/skills/user/payment-timeout", json={"content": skill_md("payment-timeout", "新描述")}, headers=WRITE,
    )
    assert updated.status_code == 200
    assert "新描述" in client.get("/api/skills/user/payment-timeout", headers=OWNER).json()["content"]

    assert client.delete("/api/skills/user/payment-timeout", headers=WRITE).status_code == 200
    assert not (home / "payment-timeout").exists()


@pytest.mark.parametrize("name", ["Bad_Name", "-x", "a--b", "../evil"])
def test_skill_create_rejects_bad_names(env, name: str) -> None:
    client, *_ = env
    response = client.post("/api/skills", json={"source": "user", "name": name, "content": skill_md(name)}, headers=WRITE)
    assert response.status_code in (400, 422)


def test_skill_save_rejects_mismatched_frontmatter(env) -> None:
    client, *_ = env
    response = client.post(
        "/api/skills", json={"source": "user", "name": "alpha", "content": skill_md("beta")}, headers=WRITE,
    )
    assert response.status_code == 422
    assert "目录名" in response.json()["detail"]


def test_skill_paths_cannot_escape_source(env) -> None:
    client, *_ = env
    for name in ("%2E%2E", ".hidden", "..%5Cevil"):
        assert client.get(f"/api/skills/user/{name}", headers=OWNER).status_code == 400, name
    assert client.delete("/api/skills/nope/x", headers=WRITE).status_code == 404


def test_memory_crud_and_similar_prompt(env) -> None:
    client, _, _, _ = env
    saved = client.post("/api/memory", json={"text": "报告先写结论", "kind": "preference"}, headers=WRITE).json()
    assert saved["saved"] and saved["memory"]["scope_label"] == "全局"
    memory_id = saved["memory"]["id"]

    similar = client.post("/api/memory", json={"text": "报告请先写结论", "kind": "preference"}, headers=WRITE).json()
    assert similar["saved"] is False and similar["similar"][0]["id"] == memory_id
    replaced = client.post(
        "/api/memory", json={"text": "报告请先写结论", "kind": "preference", "replace_id": memory_id}, headers=WRITE,
    ).json()
    assert replaced["updated"] and replaced["memory"]["id"] == memory_id

    assert client.patch(f"/api/memory/{memory_id}", json={"text": "结论放最前"}, headers=WRITE).json()["text"] == "结论放最前"
    assert client.delete(f"/api/memory/{memory_id}", headers=WRITE).status_code == 200
    assert client.get("/api/memory", headers=OWNER).json()["memories"] == []
    assert client.delete(f"/api/memory/{memory_id}", headers=WRITE).status_code == 404


def test_memory_candidates_accept_and_reject(env) -> None:
    client, _, _, memory_db = env
    store = MemoryStore(memory_db)
    session = MemorySession(store=store, mode="suggest", project=None, session="s1")
    session.suggest("网关就是 account-gateway", "term", "explicit")
    session.suggest("测试环境叫 pre", "fact", "correction")
    session.finish_turn("记住这些")
    store.close()

    pending = client.get("/api/memory", headers=OWNER).json()["pending"]
    assert len(pending) == 2
    first, second = pending
    accepted = client.post(f"/api/memory/candidates/{first['id']}/accept", json={}, headers=WRITE).json()
    assert accepted["saved"]
    assert client.post(f"/api/memory/candidates/{second['id']}/reject", json={"permanent": True}, headers=WRITE).status_code == 200

    data = client.get("/api/memory", headers=OWNER).json()
    assert data["pending"] == []
    assert [m["text"] for m in data["memories"]] == [first["text"]]
    assert client.post(f"/api/memory/candidates/{second['id']}/accept", json={}, headers=WRITE).status_code == 404
