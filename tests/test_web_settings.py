"""网页设置页：改写用户级配置、保存 API Key、立即作用于运行中的服务。"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from log_agent import credentials  # noqa: E402
from log_agent.config import ConfigError, user_config_path  # noqa: E402
from log_agent.web.app import WebConfig, create_app  # noqa: E402
from log_agent.web.settings import SettingsContext, apply, update_toml_text  # noqa: E402

TOKEN = "settings-token"
OWNER = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**OWNER, "X-Log-Agent-Request": "1"}
KEY = "sk-test-0123456789abcdef"
_ENV = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "LOG_AGENT_MODEL", "LOG_AGENT_TIMEOUT", "LOG_AGENT_MAX_RETRIES",
        "LOG_AGENT_TIMEZONE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # 先 set 再 del：让 monkeypatch 记住原状态，用例里 apply() 改过的环境变量结束后能还原
    for name in _ENV:
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)


def make_client(tmp_path: Path, **ctx) -> tuple[TestClient, WebConfig]:
    settings = SettingsContext(default_model="openai:gpt-4.1", cwd=tmp_path, **ctx)
    config = WebConfig(db_path=tmp_path / "sessions.db", token=TOKEN, agent_factory=lambda **_: None,
                       settings=settings)
    apply(settings, config)
    return TestClient(create_app(config)), config


def test_update_toml_keeps_comments_and_sections() -> None:
    text = '# 我的配置\nmodel = "openai:old"   # 旧模型\nmax-retries = 2\n\n[analyze]\nverbose = true\n'
    result = update_toml_text(text, {"model": "openai:new", "base_url": "https://gw.example.com/v1",
                                     "max_retries": None})
    assert result.startswith("# 我的配置\nmodel = \"openai:new\"\n")
    assert 'base_url = "https://gw.example.com/v1"' in result
    assert "max-retries" not in result
    assert result.index("base_url") < result.index("[analyze]")
    assert "[analyze]\nverbose = true" in result


def test_update_toml_rejects_broken_file() -> None:
    with pytest.raises(ConfigError):
        update_toml_text("model = \n", {"model": "x"})


def test_settings_require_owner_and_csrf(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    assert client.get("/api/settings").status_code == 401
    assert client.put("/api/settings", json={"values": {}}, headers=OWNER).status_code == 403
    assert client.post("/api/settings/test", json={}, headers=OWNER).status_code == 403


def test_save_key_and_config_applies_immediately(tmp_path: Path) -> None:
    client, config = make_client(tmp_path)
    assert client.get("/api/meta", headers=OWNER).json()["can_chat"] is False

    response = client.put("/api/settings", headers=WRITE, json={
        "values": {"base_url": "https://gw.example.com/v1", "model": "openai:qwen-max", "timeout": 90, "memory": "off"},
        "api_key": f"  {KEY}\n",
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert KEY not in response.text  # 完整 Key 永远不回传
    assert body["api_key"] == {"set": True, "masked": "sk-…cdef", "source": "file", "locked": False, "saved": True}
    assert body["fields"]["base_url"]["source"] == "user"

    assert credentials.read_api_key() == KEY
    if sys.platform != "win32":
        assert stat.S_IMODE(credentials.credentials_path().stat().st_mode) == 0o600
    saved = user_config_path().read_text(encoding="utf-8")
    assert 'base_url = "https://gw.example.com/v1"' in saved and KEY not in saved

    assert config.base_url == "https://gw.example.com/v1"
    assert config.default_model == "openai:qwen-max"
    assert config.memory_mode == "off"
    assert os.environ["LOG_AGENT_TIMEOUT"] == "90"
    assert client.get("/api/meta", headers=OWNER).json()["can_chat"] is True

    cleared = client.put("/api/settings", headers=WRITE, json={"clear_api_key": True, "values": {"base_url": None}})
    assert cleared.json()["api_key"]["set"] is False
    assert not credentials.credentials_path().exists()
    assert config.base_url is None and config.can_chat is False


def test_env_values_are_locked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://user:secret@env.example.com/v1")
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    client, config = make_client(tmp_path, env_locked=frozenset({"OPENAI_BASE_URL"}), key_from_env=True)
    client.put("/api/settings", headers=WRITE, json={"values": {"base_url": "https://file.example.com/v1"}})

    body = client.get("/api/settings", headers=OWNER).json()
    field = body["fields"]["base_url"]
    assert field["locked"] is True and field["source"] == "env"
    assert "secret" not in field["effective"]  # 带凭据的地址只显示主机名
    assert body["api_key"]["locked"] is True
    assert config.base_url == "https://user:secret@env.example.com/v1"


@pytest.mark.parametrize(("values", "message"), [
    ({"base_url": "https://gw.example.com/v1?token=abc"}, "接口地址"),
    ({"timeout": 0}, "请求超时"),
    ({"max_retries": 2.5}, "重试次数"),
    ({"timezone": "Mars/Base"}, "默认时区"),
    ({"unknown": 1}, "不支持"),
])
def test_invalid_values_are_rejected(tmp_path: Path, values: dict, message: str) -> None:
    client, _ = make_client(tmp_path)
    response = client.put("/api/settings", headers=WRITE, json={"values": values})
    assert response.status_code == 400 and message in response.json()["detail"]
    assert not user_config_path().exists()


def test_connection_test_without_key(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    result = client.post("/api/settings/test", headers=WRITE, json={}).json()
    assert result["ok"] is False and "API Key" in result["message"]
