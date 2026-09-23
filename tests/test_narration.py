from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import agent as agent_module
from log_agent import cli
from log_agent.render import collect_ai_texts, condense_note, looks_like_report

from .conftest import ScriptedChatModel

runner = CliRunner()

REPORT = "一句话结论：订单服务 NPE（可信度：高）\n\n### 根因分析\n\nRouter.select 未判空。"


def _narrated(text: str, name: str, call_id: str, **args) -> AIMessage:
    return AIMessage(content=text, tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sample_log: Path, script: list[AIMessage], *extra: str):
    real_build = agent_module.build_agent
    monkeypatch.setattr(
        agent_module, "build_agent",
        lambda **kw: real_build(model=ScriptedChatModel(script=script), checkpointer=kw.get("checkpointer")),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    out = tmp_path / "result.json"
    result = runner.invoke(
        cli.app, ["analyze", "-l", str(sample_log), "-q", "为什么失败", "-o", str(out), *extra],
        env={"COLUMNS": "120"},
    )
    return result, out


def test_looks_like_report() -> None:
    assert not looks_like_report("先看一下整体的错误分布")
    assert looks_like_report("一句话结论：没问题（可信度：高）")
    assert looks_like_report("### 时间线\n\n14:00 开始报错")
    assert looks_like_report("很长" * 200)


def test_condense_note_strips_markdown() -> None:
    assert condense_note("\n**先看** `app.log` 的整体分布\n然后再搜索") == "先看 app.log 的整体分布"
    assert condense_note("- 确认重试逻辑") == "确认重试逻辑"
    assert condense_note("   ") == ""


def test_narration_is_shown_as_note_not_report(
    sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = [
        _narrated("先看 14 点前后的错误分布", "log_overview", "c1", path=str(sample_log)),
        AIMessage(content=REPORT),
    ]
    result, out = _run(monkeypatch, tmp_path, sample_log, script, "-v")
    assert result.exit_code == 0, result.output
    # verbose 下旁白作为进度行留在工具行上方，且出现在"分析结果"分隔线之前
    assert "先看 14 点前后的错误分布" in result.output
    assert result.output.index("先看 14 点前后的错误分布") < result.output.index("分析结果")

    data = json.loads(out.read_text(encoding="utf-8"))
    assert "先看 14 点" not in data["report"]
    assert data["report"].startswith("一句话结论")
    assert data["tool_calls"][0]["note"] == "先看 14 点前后的错误分布"


def test_long_text_before_tools_stays_in_report(
    sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    early = "### 初步判断\n\n日志里有大量超时，先确认一下是哪个下游。"
    script = [
        _narrated(early, "log_overview", "c1", path=str(sample_log)),
        AIMessage(content=REPORT),
    ]
    result, out = _run(monkeypatch, tmp_path, sample_log, script)
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "初步判断" in data["report"]
    assert data["tool_calls"][0]["note"] == ""


def test_collect_ai_texts_skips_narration() -> None:
    from langchain_core.messages import HumanMessage

    messages = [
        HumanMessage(content="q"),
        _narrated("先看整体分布", "log_overview", "c1"),
        AIMessage(content=REPORT),
    ]
    assert collect_ai_texts(messages) == REPORT
    # 只有旁白时退而求其次，至少不丢内容
    assert collect_ai_texts(messages[:2]) == "先看整体分布"
