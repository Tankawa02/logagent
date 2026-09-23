from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.config import ConfigError, load_config

from .conftest import ScriptedChatModel


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_project_config_overrides_user_and_resolves_paths(tmp_path: Path) -> None:
    _write(Path.home() / ".log-agent" / "config.toml", 'model = "openai:user-model"\nmax_steps = 50\n')
    project = tmp_path / "proj"
    (project / "src").mkdir(parents=True)
    _write(project / ".log-agent.toml", 'model = "openai:proj-model"\ncode = "src"\n[analyze]\nverbose = true\n')
    nested = project / "deep" / "er"
    nested.mkdir(parents=True)

    config = load_config(nested)
    assert config.files == [Path.home() / ".log-agent" / "config.toml", project / ".log-agent.toml"]
    analyze = config.for_command("analyze")
    assert analyze["model"] == "openai:proj-model" and analyze["max_steps"] == 50
    assert analyze["code"] == [str((project / "src").resolve())]
    assert analyze["verbose"] is True and "verbose" not in config.for_command("chat")


def test_unknown_keys_warn_and_bad_toml_fails(tmp_path: Path) -> None:
    _write(tmp_path / ".log-agent.toml", 'modle = "x"\n[analyse]\nverbose = true\n')
    config = load_config(tmp_path)
    assert any("modle" in w for w in config.warnings) and any("[analyse]" in w for w in config.warnings)

    _write(tmp_path / ".log-agent.toml", "model = \n")
    with pytest.raises(ConfigError, match="格式错误"):
        load_config(tmp_path)


def _run_analyze(monkeypatch: pytest.MonkeyPatch, cwd: Path, log: Path, *extra: str) -> tuple[Any, dict]:
    from log_agent import agent as agent_module

    seen: dict = {}
    real_build = agent_module.build_agent

    def fake_build(**kwargs: Any):
        seen.update(kwargs)
        return real_build(model=ScriptedChatModel(script=[AIMessage(content="### 结论\n\nok")]))

    monkeypatch.setattr(agent_module, "build_agent", fake_build)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.chdir(cwd)
    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(log), *extra])
    return result, seen


def test_analyze_reads_config_defaults(sample_log: Path, code_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = tmp_path / "work"
    _write(
        project / ".log-agent.toml",
        f'model = "openai:cfg-model"\nbase_url = "https://gw.example/v1"\ncode = ["{code_repo.as_posix()}"]\ntimeout = 222\n',
    )
    context: dict = {}
    real_message = cli._build_context_message
    monkeypatch.setattr(
        cli, "_build_context_message", lambda logs, code, q: context.update(code=code) or real_message(logs, code, q)
    )
    result, seen = _run_analyze(monkeypatch, project, sample_log)
    assert result.exit_code == 0, result.output
    assert seen == {"model": "openai:cfg-model", "base_url": "https://gw.example/v1"}
    assert context["code"] == [str(code_repo)]
    assert "配置" in result.output
    assert os.environ["LOG_AGENT_TIMEOUT"] == "222"


def test_cli_and_env_beat_config(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path / ".log-agent.toml", 'model = "openai:cfg-model"\nbase_url = "https://cfg/v1"\n')
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env/v1")
    result, seen = _run_analyze(monkeypatch, tmp_path, sample_log, "-m", "openai:cli-model")
    assert result.exit_code == 0, result.output
    assert seen == {"model": "openai:cli-model", "base_url": "https://env/v1"}


def test_config_command_init_and_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(cli.app, ["config", "--init"]).exit_code == 0
    assert (tmp_path / ".log-agent.toml").is_file()
    assert runner.invoke(cli.app, ["config", "--init"]).exit_code == 2

    (tmp_path / ".log-agent.toml").write_text('model = "openai:x"\n', encoding="utf-8")
    shown = runner.invoke(cli.app, ["config"])
    assert shown.exit_code == 0 and "openai:x" in shown.output and "已加载" in shown.output
