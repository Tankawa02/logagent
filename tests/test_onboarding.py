"""log-agent init 引导与 doctor --ping。

--ping 用本机起的一个假 OpenAI 兼容网关测，走的是和 analyze 完全相同的 ChatOpenAI 路径，不访问外网。
"""

from __future__ import annotations

import json
import threading
import tomllib
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_agent import cli, probe
from log_agent.config import load_config
from log_agent.onboarding import InitChoices, render_config, unsafe_url_reason, validate_config_text

runner = CliRunner()
GOOD_KEY = "sk-test-good-key-0000-1234"
WIDE = {"COLUMNS": "220"}


class _Gateway(BaseHTTPRequestHandler):
    models = ("gpt-4.1", "qwen-max", "text-embedding-v3")
    requests: list[dict] = []

    def log_message(self, *args) -> None:  # 安静
        pass

    def _send(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {GOOD_KEY}":
            return True
        self._send(401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}})
        return False

    def do_GET(self) -> None:
        if self._authorized():
            self._send(200, {"object": "list", "data": [{"id": m, "object": "model"} for m in self.models]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if not self._authorized():
            return
        if body["model"] not in self.models:
            self._send(404, {"error": {"message": f"model {body['model']} does not exist"}})
            return
        type(self).requests.append(body)
        self._send(200, {
            "id": "c1", "object": "chat.completion", "created": 0, "model": body["model"] + "-2026-09-01",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10},
        })


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    _Gateway.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", url)
    monkeypatch.setenv("OPENAI_API_KEY", GOOD_KEY)
    yield url
    server.shutdown()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "proj"
    (root / "logs").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (root / "logs" / "app.log").write_text(
        "2026-09-30 14:00:00 INFO start\n2026-09-30 14:00:05 ERROR boom order=1\n", encoding="utf-8")
    monkeypatch.chdir(root)
    return root


# ---------------------------------------------------------------------------
# doctor --ping
# ---------------------------------------------------------------------------


def test_doctor_ping_succeeds_with_minimal_request(gateway: str) -> None:
    out = runner.invoke(cli.app, ["doctor", "--ping"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert "模型服务可用（openai:gpt-4.1 @ 127.0.0.1）" in out.output and "实际模型 gpt-4.1-2026-09-01" in out.output
    [request] = _Gateway.requests
    assert request["model"] == "gpt-4.1" and len(request["messages"]) == 1
    assert request.get("max_completion_tokens", request.get("max_tokens")) == 16
    assert GOOD_KEY not in out.output


@pytest.mark.parametrize(("env", "message"), [
    ({"OPENAI_API_KEY": "sk-wrong-key-xxxxxxxx"}, "认证失败（401）"),
    ({"LOG_AGENT_MODEL": "openai:does-not-exist"}, "返回 404"),
])
def test_doctor_ping_reports_bad_key_and_model(gateway: str, env: dict, message: str) -> None:
    out = runner.invoke(cli.app, ["doctor", "--ping"], env={**WIDE, **env})
    assert out.exit_code == 1, out.output
    assert "模型服务不可用" in out.output and message in out.output
    assert "已自动重试" not in out.output  # --ping 不重试，不能说重试过
    assert "sk-wrong-key-xxxxxxxx" not in out.output


def test_doctor_ping_reports_unreachable_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", GOOD_KEY)
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:1/v1")
    out = runner.invoke(cli.app, ["doctor", "--ping", "--ping-timeout", "3"], env=WIDE)
    assert out.exit_code == 1, out.output
    assert "无法连接模型接口" in out.output


def test_doctor_ping_needs_key_and_skips_commands_without_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = runner.invoke(cli.app, ["doctor", "--ping"], env=WIDE)
    assert out.exit_code == 1 and "无法连接模型服务" in out.output
    out = runner.invoke(cli.app, ["doctor", "--command", "inspect", "--ping"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert "跳过 --ping" in out.output


def test_doctor_without_ping_points_to_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    out = runner.invoke(cli.app, ["doctor"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert "不连接模型服务" in out.output and "--ping" in out.output


class _Picky:
    """不接受 max_tokens 的模型：第一次带上限会被拒，去掉上限后成功。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def invoke(self, prompt, **kwargs):
        from langchain_core.messages import AIMessage

        self.calls.append(kwargs)
        if "max_tokens" in kwargs:
            raise ValueError("Unsupported parameter: 'max_tokens'")
        return AIMessage(content=[{"type": "text", "text": "ok"}], response_metadata={"model_name": "o-test"})


def test_ping_retries_without_token_limit_when_rejected() -> None:
    model = _Picky()
    result = probe.ping("openai:o-test", None, chat_model=model)
    assert result.ok and result.served_model == "o-test" and "ok" in result.message
    assert model.calls == [{"max_tokens": 16}, {}]


def test_mask_key() -> None:
    assert probe.mask_key("sk-abcdefghijklmnop1234") == "sk-…1234"
    assert probe.mask_key("short") == "*****"


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


def test_init_yes_generates_loadable_config_without_secrets(project: Path, gateway: str) -> None:
    (project / "logs" / "gw.log").write_text("GW|30.09.2026|start\nGW|30.09.2026|password=hunter2 failed\n",
                                             encoding="utf-8")
    out = runner.invoke(cli.app, ["init", "--yes", "--ping", "--timezone", "+08:00"], env=WIDE)
    assert out.exit_code == 0, out.output
    text = (project / ".log-agent.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    assert data["model"] == "openai:gpt-4.1" and data["base_url"] == gateway
    assert data["code"] == ["."] and data["timezone"] == "+08:00"
    assert GOOD_KEY not in text and "hunter2" not in text  # Key 不落盘，抽样行先脱敏
    assert "# [[log_formats]]" in text and "GW|30.09.2026|start" in text  # 未识别的日志附上模板
    assert "模型接口可用" in out.output and "log-agent analyze -l logs/app.log" in out.output
    config = load_config(project).for_command("analyze")
    assert config["code"] == [str(project.resolve())] and config["timezone"] == "+08:00"


def test_init_refuses_to_overwrite_without_force(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    (project / ".log-agent.toml").write_text("model = 'openai:keep'\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["init", "--yes"], env=WIDE)
    assert out.exit_code == 2 and "--force" in out.output
    assert "openai:keep" in (project / ".log-agent.toml").read_text(encoding="utf-8")
    out = runner.invoke(cli.app, ["init", "--yes", "--force", "-m", "qwen-max"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert tomllib.loads((project / ".log-agent.toml").read_text(encoding="utf-8"))["model"] == "openai:qwen-max"


def test_init_without_key_explains_and_still_writes(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = runner.invoke(cli.app, ["init", "--yes"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert "没有设置 OPENAI_API_KEY" in out.output and "OPENAI_API_KEY=" in out.output
    assert "先设置 OPENAI_API_KEY" in out.output and "log-agent doctor --ping" in out.output
    assert (project / ".log-agent.toml").exists()
    # 显式要求 --ping 却没有 Key：配置照样生成，但退出码提示失败
    out = runner.invoke(cli.app, ["init", "--yes", "--force", "--ping"], env=WIDE)
    assert out.exit_code == 1, out.output


def test_init_interactive_lists_gateway_models(project: Path, gateway: str) -> None:
    # 网关地址回车 → 选第 2 个模型 → 验证 → 默认日志 → 时区 → 当作源码目录 → 写入
    answers = "\n2\ny\n\n+09:00\ny\ny\n"
    out = runner.invoke(cli.app, ["init"], input=answers, env=WIDE)
    assert out.exit_code == 0, out.output
    assert "接口返回的模型" in out.output and "text-embedding" not in out.output  # 嵌入模型不能对话
    data = tomllib.loads((project / ".log-agent.toml").read_text(encoding="utf-8"))
    assert data["model"] == "openai:qwen-max" and data["timezone"] == "+09:00" and data["code"] == ["."]
    assert [r["model"] for r in _Gateway.requests] == ["qwen-max"]


def test_init_interactive_cancel_writes_nothing(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    out = runner.invoke(cli.app, ["init", "-m", "openai:m", "--no-ping"], input="\n\n\nn\nn\n", env=WIDE)
    assert out.exit_code == 2, out.output
    assert not (project / ".log-agent.toml").exists()


def test_init_skips_timezone_for_offset_logs_and_code_outside_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.log").write_text('{"time":"2026-09-30T06:00:00Z","level":"info","msg":"ok"}\n', encoding="utf-8")
    monkeypatch.chdir(plain)
    out = runner.invoke(cli.app, ["init", "--yes"], env=WIDE)
    assert out.exit_code == 0, out.output
    data = tomllib.loads((plain / ".log-agent.toml").read_text(encoding="utf-8"))
    assert "timezone" not in data and "code" not in data
    assert "自带时区" in out.output and "不像代码仓库" in out.output


def test_init_never_writes_urls_with_credentials(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    out = runner.invoke(cli.app, ["init", "--yes", "--base-url", "https://u:secret-pass@gw.example.com/v1"], env=WIDE)
    assert out.exit_code == 0, out.output
    text = (project / ".log-agent.toml").read_text(encoding="utf-8")
    assert "secret-pass" not in text and "base_url" not in tomllib.loads(text)
    assert "OPENAI_BASE_URL" in out.output


@pytest.mark.parametrize(("url", "unsafe"), [
    ("https://gw.example.com/v1", False),
    ("https://u:p@gw.example.com/v1", True),
    ("https://gw.example.com/v1?token=abc", True),
    ("ftp://gw.example.com", True),
])
def test_unsafe_url_reason(url: str, unsafe: bool) -> None:
    assert (unsafe_url_reason(url) is not None) is unsafe


def test_rendered_config_round_trips_tricky_values() -> None:
    choices = InitChoices('openai:we"ird\\model', "https://gw.example.com/v1", ["src", "C:\\code"], "Asia/Shanghai")
    text = render_config(choices, today="2026-10-01")
    assert validate_config_text(text) == []
    data = tomllib.loads(text)
    assert data["model"] == 'openai:we"ird\\model' and data["code"] == ["src", "C:\\code"]
