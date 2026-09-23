from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.agent import SYSTEM_PROMPT, build_agent
from log_agent.config import load_config
from log_agent.skills import resolve_skill_sources

from .conftest import ScriptedChatModel, tool_call


def _skill(root: Path, name: str, description: str, body: str = "# 手册\n") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}", encoding="utf-8")
    return folder


class _Recorder(ScriptedChatModel):
    systems: list[str] = []
    last_messages: list[BaseMessage] = []

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs: Any):
        system = next((m for m in messages if isinstance(m, SystemMessage)), None)
        self.systems.append(system.text if system else "")
        self.last_messages = list(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


def _tool_results(model: _Recorder) -> dict[str, str]:
    return {m.tool_call_id: m.text for m in model.last_messages if isinstance(m, ToolMessage)}


def test_sources_order_dedupe_and_project_lookup(tmp_path: Path) -> None:
    user = Path.home() / ".log-agent" / "skills"
    user.mkdir(parents=True)
    project = tmp_path / "repo" / ".log-agent" / "skills"
    project.mkdir(parents=True)
    nested = tmp_path / "repo" / "a" / "b"
    nested.mkdir(parents=True)
    extra = tmp_path / "team-skills"
    extra.mkdir()

    sources = resolve_skill_sources([extra, user, tmp_path / "missing"], cwd=nested)
    # 同一目录只保留优先级最高的一次；不存在的目录直接忽略
    assert [s.directory for s in sources] == [project.resolve(), extra.resolve(), user.resolve()]
    assert [s.route for s in sources] == ["/skills/project/", "/skills/extra-1/", "/skills/extra-2/"]
    assert [s.route for s in resolve_skill_sources([], cwd=nested)] == ["/skills/user/", "/skills/project/"]


def test_no_skills_keeps_prompt_unchanged(tmp_path: Path) -> None:
    model = _Recorder(script=[AIMessage(content="ok")], systems=[])
    build_agent(model=model, skill_dirs=[]).invoke({"messages": [{"role": "user", "content": "hi"}]})
    assert model.systems[0] == SYSTEM_PROMPT


def test_skill_listed_and_readable_but_disk_is_not(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    _skill(skills, "sms-routing", "短信供应商路由与降级问题的排查手册", "# 短信路由\n先搜 vendor= 字段\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    model = _Recorder(
        script=[
            tool_call("read_file", "r1", file_path="/skills/extra-1/sms-routing/SKILL.md", limit=1000),
            tool_call("read_file", "r2", file_path=str(secret)),
            tool_call("read_file", "r3", file_path="/skills/extra-1/../../secret.txt"),
            AIMessage(content="ok"),
        ],
        systems=[],
    )
    build_agent(model=model, skill_dirs=[skills]).invoke(
        {"messages": [{"role": "user", "content": "hi"}]}, config={"recursion_limit": 30}
    )

    system = model.systems[0]
    assert system.startswith(SYSTEM_PROMPT)
    assert "## Skills（排查手册）" in system
    assert "**sms-routing**: 短信供应商路由与降级问题的排查手册" in system
    assert "/skills/extra-1/sms-routing/SKILL.md" in system

    results = _tool_results(model)
    assert "先搜 vendor= 字段" in results["r1"]
    assert "top secret" not in results["r2"] and "top secret" not in results["r3"]


def test_oversized_tool_result_is_readable_via_read_file(tmp_path: Path) -> None:
    """deepagents 把超长工具结果转存到 /large_tool_results/，只有 read_file 能读回来。"""
    log = tmp_path / "big.log"
    log.write_text(
        "".join(f"2026-06-09 14:00:{i % 60:02d} INFO line-{i} {'x' * 900}\n" for i in range(1200)), encoding="utf-8"
    )
    model = _Recorder(
        script=[
            tool_call("read_log_chunk", "big", path=str(log), start_line=1, num_lines=1000),
            tool_call("read_file", "back", file_path="/large_tool_results/big", offset=500, limit=2),
            AIMessage(content="ok"),
        ],
        systems=[],
    )
    build_agent(model=model).invoke({"messages": [{"role": "user", "content": "hi"}]}, config={"recursion_limit": 30})

    results = _tool_results(model)
    assert "/large_tool_results/big" in results["big"]
    assert "line-499" in results["back"]


def test_skills_from_config_and_cli_panel(sample_log: Path, tmp_path: Path, monkeypatch) -> None:
    from log_agent import agent as agent_module

    project = tmp_path / "work"
    _skill(project / "ops" / "skills", "payments", "支付失败排查")
    (project / ".log-agent.toml").write_text('skills = ["ops/skills"]\n', encoding="utf-8")
    assert load_config(project).for_command("chat")["skills"] == [str((project / "ops" / "skills").resolve())]

    seen: dict = {}
    real_build = agent_module.build_agent

    def fake_build(**kwargs: Any):
        seen.update(kwargs)
        return real_build(model=ScriptedChatModel(script=[AIMessage(content="### 结论\n\nok")]))

    monkeypatch.setattr(agent_module, "build_agent", fake_build)
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    monkeypatch.chdir(project)
    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log)])
    assert result.exit_code == 0, result.output
    assert [Path(p) for p in seen["skill_dirs"]] == [(project / "ops" / "skills").resolve()]
    assert "Skills" in result.output and "1 个" in result.output
