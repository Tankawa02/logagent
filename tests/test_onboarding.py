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

from log_agent import cli, onboarding, probe
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


# ---------------------------------------------------------------------------
# PR #46 review / Windows CI
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("windows", [False, True])
def test_default_log_answer_round_trips_on_every_platform(windows: bool) -> None:
    paths = ["logs/app.log", "logs/my app.log", "C:/srv/x y/a.log"]
    answer = " ".join(onboarding.quote_arg(p, windows) for p in paths)
    assert onboarding.split_args(answer, windows) == paths
    if windows:
        assert "'" not in answer  # cmd / PowerShell 里单引号不是引号
        assert onboarding.split_args(r'logs\app.log "D:\x y\b.log"', True) == [r"logs\app.log", r"D:\x y\b.log"]


def test_display_path_uses_forward_slashes(tmp_path: Path) -> None:
    (tmp_path / "logs").mkdir()
    assert onboarding.display_path(tmp_path / "logs" / "a.log", tmp_path) == "logs/a.log"
    assert onboarding.display_path(tmp_path, tmp_path) == "."
    assert onboarding.display_path(tmp_path, tmp_path / "logs") == tmp_path.resolve().as_posix()


def test_init_interactive_with_windows_style_quoting(project: Path, gateway: str,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    # 回归：Windows 上默认日志答案被单引号包住、路径解析失败，后面的问题全部错位
    monkeypatch.setattr(onboarding, "IS_WINDOWS", True)
    (project / "logs" / "app.log").rename(project / "logs" / "my app.log")
    out = runner.invoke(cli.app, ["init"], input="\n2\ny\n\n+09:00\ny\ny\n", env=WIDE)
    assert out.exit_code == 0, out.output
    assert '["logs/my app.log"]' in out.output or '"logs/my app.log"' in out.output
    data = tomllib.loads((project / ".log-agent.toml").read_text(encoding="utf-8"))
    assert data["timezone"] == "+09:00" and data["code"] == ["."] and data["model"] == "openai:qwen-max"
    assert 'log-agent analyze -l "logs/my app.log"' in out.output


@pytest.mark.parametrize("windows", [False, True])
def test_key_hints_are_copyable_commands(monkeypatch: pytest.MonkeyPatch, windows: bool) -> None:
    import re

    monkeypatch.setattr(onboarding, "IS_WINDOWS", windows)
    allowed = re.compile(r'^(export OPENAI_API_KEY="sk-\.\.\."|\$env:OPENAI_API_KEY="sk-\.\.\."'
                         r'|set OPENAI_API_KEY=sk-\.\.\.|setx OPENAI_API_KEY "sk-\.\.\.")$')
    for _, command in onboarding.key_hints():
        assert allowed.match(command), command
    assert onboarding.key_hint_note()


def test_missing_key_hint_in_analyze_is_copyable(monkeypatch: pytest.MonkeyPatch, sample_log: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(onboarding, "IS_WINDOWS", False)
    out = runner.invoke(cli.app, ["analyze", "-l", str(sample_log)], env=WIDE)
    assert out.exit_code == 1
    line = next(row for row in out.output.splitlines() if "export OPENAI_API_KEY" in row)
    assert line.strip() == 'Shell: export OPENAI_API_KEY="sk-..."'


def test_fragment_urls_never_reach_config(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    out = runner.invoke(cli.app, ["init", "--yes", "--base-url", "https://gw.example.com/v1#token=frag-secret"],
                        env=WIDE)
    assert out.exit_code == 0, out.output
    assert "frag-secret" not in (project / ".log-agent.toml").read_text(encoding="utf-8")
    assert "frag-secret" not in out.output


def test_unsafe_env_gateway_is_neither_shown_nor_written(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://u:env-secret@gw.example.com/v1")
    out = runner.invoke(cli.app, ["init", "-m", "openai:m", "--no-ping"], input="\n\n\nn\ny\n", env=WIDE)
    assert out.exit_code == 0, out.output
    assert "env-secret" not in out.output
    assert "env-secret" not in (project / ".log-agent.toml").read_text(encoding="utf-8")
    assert "运行时实际连 gw.example.com" in out.output


def test_init_writes_the_file_selected_by_log_agent_config(project: Path, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    selected = tmp_path / "shared" / "team.toml"
    monkeypatch.setenv("LOG_AGENT_CONFIG", str(selected))
    out = runner.invoke(cli.app, ["init", "--yes", "--timezone", "+08:00"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert selected.is_file() and not (project / ".log-agent.toml").exists()
    assert "LOG_AGENT_CONFIG" in out.output
    # 相对路径以配置文件所在目录为准：code 仍然指向刚才的项目目录
    analyze = load_config(project).for_command("analyze")
    assert analyze["code"] == [str(project.resolve())] and analyze["timezone"] == "+08:00"


def test_init_pings_the_model_that_will_actually_run(project: Path, gateway: str,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_AGENT_MODEL", "openai:qwen-max")
    out = runner.invoke(cli.app, ["init", "--yes", "--ping", "-m", "openai:gpt-4.1"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert [r["model"] for r in _Gateway.requests] == ["qwen-max"]
    assert "LOG_AGENT_MODEL=openai:qwen-max 会覆盖" in out.output
    assert tomllib.loads((project / ".log-agent.toml").read_text(encoding="utf-8"))["model"] == "openai:gpt-4.1"


def test_init_pings_the_gateway_that_will_actually_run(project: Path, gateway: str) -> None:
    out = runner.invoke(cli.app, ["init", "--yes", "--ping", "--base-url", "http://127.0.0.1:9/v1"], env=WIDE)
    assert out.exit_code == 0, out.output  # 环境变量里的网关覆盖配置，验证的也是它
    assert len(_Gateway.requests) == 1 and "会覆盖配置里的 base_url" in out.output


def test_rejected_explicit_gateway_skips_ping_and_listing(project: Path, gateway: str) -> None:
    out = runner.invoke(cli.app, ["init", "--yes", "--ping", "--base-url", "https://u:p@other.example.com/v1"],
                        env=WIDE)
    assert out.exit_code == 1, out.output
    assert _Gateway.requests == [] and "跳过" in out.output
    out = runner.invoke(cli.app, ["init", "--force"], input="https://u:p@other.example.com/v1\n\ny\n\n\ny\ny\n",
                        env=WIDE)
    assert out.exit_code == 0, out.output
    assert _Gateway.requests == [] and "接口返回的模型" not in out.output


def test_ping_refuses_providers_that_ignore_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain.chat_models

    def picky(model, **kwargs):
        if kwargs:
            raise TypeError("unexpected keyword argument 'max_retries'")
        raise AssertionError("must not fall back to a client without limits")

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", picky)
    result = probe.ping("someprovider:model", None, timeout=1)
    assert not result.ok and "不支持设置超时和重试" in result.message


def test_ping_is_bounded_even_if_the_client_hangs() -> None:
    import time

    class Hangs:
        def invoke(self, prompt, **kwargs):
            time.sleep(10)

    started = time.monotonic()
    result = probe.ping("openai:x", None, timeout=0.5, chat_model=Hangs())
    assert not result.ok and "没有响应" in result.message
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("name", ["logs/$(whoami).log", "logs/`whoami`.log", "logs/%PATH%.log", "logs/!PATH!.log"])
def test_shell_command_omits_unsafe_windows_paths(name: str) -> None:
    assert onboarding.shell_arg(name, windows=True) is None
    assert onboarding.split_args(onboarding.quote_arg(name, True), True) == [name]


def test_init_does_not_print_substitution_command(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (project / "logs" / "app.log").rename(project / "logs" / "$(whoami).log")
    monkeypatch.setattr(onboarding, "IS_WINDOWS", True)
    out = runner.invoke(cli.app, ["init", "--yes", "--no-ping"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert 'log-agent analyze -l "logs/$(whoami).log"' not in out.output
    assert "请先重命名文件" in out.output


def test_init_new_explicit_config_inherits_user_defaults(project: Path, tmp_path: Path,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent.config import user_config_path

    user = user_config_path()
    user.parent.mkdir(parents=True)
    user.write_text('model = "openai:custom"\nbase_url = "https://gateway.example/v1"\n', encoding="utf-8")
    target = tmp_path / "new.toml"
    monkeypatch.setenv("LOG_AGENT_CONFIG", str(target))
    out = runner.invoke(cli.app, ["init", "--yes", "--no-ping"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert "现有配置读取失败" not in out.output
    data = tomllib.loads(target.read_text(encoding="utf-8"))
    assert data["model"] == "openai:custom"
    assert data["base_url"] == "https://gateway.example/v1"


@pytest.mark.parametrize("model,url", [(" openai:custom ", " https://gateway.example/v1 "), ("   ", "   ")])
def test_init_probe_preserves_runtime_environment_values(project: Path, monkeypatch: pytest.MonkeyPatch,
                                                        model: str, url: str) -> None:
    monkeypatch.setenv("LOG_AGENT_MODEL", model)
    monkeypatch.setenv("OPENAI_BASE_URL", url)
    monkeypatch.setenv("OPENAI_API_KEY", GOOD_KEY)
    calls = []
    monkeypatch.setattr(onboarding, "_ping", lambda m, u: calls.append((m, u)) or True)
    out = runner.invoke(cli.app, ["init", "--yes", "--ping", "-m", "openai:saved",
                                  "--base-url", "https://saved.example/v1"], env=WIDE)
    assert out.exit_code == 0, out.output
    assert calls == [(model, url)]
    assert cli._resolve_model(None) == model


@pytest.mark.parametrize("elapsed", [0.75, 1.25])
def test_ping_fallback_shares_deadline(monkeypatch: pytest.MonkeyPatch, elapsed: float) -> None:
    now = [100.0]
    monkeypatch.setattr(probe.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(probe, "_GRACE_SECONDS", 0)
    waits = []

    def bounded(call, deadline):
        waits.append(deadline)
        if len(waits) == 1:
            now[0] += elapsed
            raise ValueError("max_tokens unsupported")
        assert deadline == pytest.approx(1 - elapsed)
        raise probe.FutureTimeout

    monkeypatch.setattr(probe, "_bounded", bounded)
    result = probe.ping("openai:x", None, timeout=1, chat_model=object())
    assert not result.ok and "没有响应" in result.message
    assert waits == [1, 1 - elapsed]


def test_bounded_does_not_start_after_deadline() -> None:
    with pytest.raises(probe.FutureTimeout):
        probe._bounded(lambda: pytest.fail("expired invocation started"), 0)


@pytest.mark.parametrize("error", [ValueError("unknown provider"), ValueError("malformed model"),
                                   TypeError("invalid model name")])
def test_ping_reports_model_creation_errors(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    import langchain.chat_models

    def invalid(*args, **kwargs):
        raise error

    monkeypatch.setattr(langchain.chat_models, "init_chat_model", invalid)
    result = probe.ping("invalid:model", None)
    assert not result.ok and "无法创建模型客户端" in result.message
    assert "不支持设置超时" not in result.message


def test_doctor_process_exits_when_invocation_never_returns(project: Path) -> None:
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent('''
        import os
        import threading
        from log_agent import agent, cli, probe
        class Hangs:
            def invoke(self, *args, **kwargs):
                threading.Event().wait()
        agent._resolve_chat_model = lambda *args, **kwargs: Hangs()
        probe._GRACE_SECONDS = 0
        os.environ["OPENAI_API_KEY"] = "test"
        cli.app(args=["doctor", "--ping", "--ping-timeout", "1"])
    ''')
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=8)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "没有响应" in result.stdout
