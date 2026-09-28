from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_agent import cli, diagnostics

runner = CliRunner()


def forbid_agent(**kwargs):
    raise AssertionError("local diagnostics must never construct an agent")


def test_inspect_runs_without_credentials_and_redacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr("log_agent.agent.build_agent", forbid_agent)
    log = tmp_path / "structured.log"
    log.write_text(
        '{"level":"error","message":"password=hunter-secret", "timestamp":"2026-09-28T08:00:00Z"}\n'
        '  stack continuation\n'
        '{"level":"info","message":"ok", "timestamp":"2026-09-28T08:01:00Z"}\n',
        encoding="utf-8",
    )
    out = runner.invoke(cli.app, ["inspect", "-l", str(log), "--timezone", "+08:00", "--since", "16:00", "--until", "16:00"])
    assert out.exit_code == 0, out.output
    assert "2/3" in out.output and "覆盖 2 行" in out.output
    assert "ERROR 1" in out.output and "hunter-secret" not in out.output
    assert "不调用模型" in out.output and "L1" in out.output
    out = runner.invoke(cli.app, ["inspect", "-l", str(log), "--since", "17:00"])
    assert out.exit_code == 0 and "当前时间窗口没有日志" in out.output


def test_inspect_handles_empty_and_unrecognized_logs(tmp_path: Path) -> None:
    empty, plain = tmp_path / "empty.log", tmp_path / "plain.log"
    empty.write_text("", encoding="utf-8")
    plain.write_text("hello\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["inspect", "-l", str(empty), "-l", str(plain), "--since", "14:00"])
    assert out.exit_code == 0, out.output
    assert "文件为空" in out.output and "无法验证时间窗口" in out.output
    assert "0/0" in out.output and "0/1" in out.output


def test_inspect_uses_config_and_does_not_require_a_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".log-agent.toml").write_text('timezone="+08:00"\n[inspect]\nsince="16:00"\nuntil="16:00"\n', encoding="utf-8")
    log = tmp_path / "a.log"
    log.write_text("2026-09-28T08:00:00Z INFO ok\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["inspect", "-l", str(log)])
    assert out.exit_code == 0, out.output
    assert "覆盖 1 行" in out.output and "+08:00" in out.output


def test_doctor_shows_effective_values_without_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("log_agent.agent.build_agent", forbid_agent)
    (tmp_path / ".log-agent.toml").write_text('model="openai:config-model"\ntimezone="+08:00"\n', encoding="utf-8")
    monkeypatch.setenv("LOG_AGENT_MODEL", "openai:env-model")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-key-never-print")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://user:secret-password@example.com/v1?token=secret-query")
    out = runner.invoke(cli.app, ["doctor"], env={"COLUMNS": "160"})
    assert out.exit_code == 0, out.output
    assert "openai:env-model" in out.output and "LOG_AGENT_MODEL" in out.output
    assert "+08:00" in out.output and "配置文件" in out.output
    assert "不连接模型" in out.output
    for secret in ("secret-key-never-print", "secret-password", "secret-query"):
        assert secret not in out.output


def test_doctor_reports_missing_key_and_old_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    real_version = diagnostics.metadata.version
    monkeypatch.setattr(diagnostics.metadata, "version", lambda name: "0.6.8" if name == "deepagents" else real_version(name))
    out = runner.invoke(cli.app, ["doctor"])
    assert out.exit_code == 1, out.output
    assert "OPENAI_API_KEY 未设置" in out.output and "deepagents 0.6.8" in out.output
    assert "uv sync" in out.output


def test_doctor_inspect_does_not_require_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = runner.invoke(cli.app, ["doctor", "--command", "inspect"])
    assert out.exit_code == 0, out.output
    assert "OPENAI_API_KEY 未设置" not in out.output


@pytest.mark.parametrize("config", ['timezone="bad"', 'timeout="NaN"', 'budget="oops"', 'max_retries=-1'])
def test_doctor_rejects_invalid_effective_settings(config: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    (tmp_path / ".log-agent.toml").write_text(config + "\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["doctor"])
    assert out.exit_code == 1, out.output


def test_doctor_validates_ranges_in_configured_zone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    (tmp_path / ".log-agent.toml").write_text(
        'timezone="+08:00"\nsince="2026-09-28 16:00"\nuntil="2026-09-28T09:00Z"\n', encoding="utf-8",
    )
    out = runner.invoke(cli.app, ["doctor"])
    assert out.exit_code == 0, out.output
