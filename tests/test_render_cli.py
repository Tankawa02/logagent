from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli, tools
from log_agent.render import split_complete_blocks, summarize_tool_output

from .conftest import ScriptedChatModel, tool_call

runner = CliRunner()


def test_summaries_follow_real_tool_output(sample_log: Path, code_repo: Path) -> None:
    log = str(sample_log)
    cases = {
        "log_overview": (tools.log_overview(log), "10 行"),
        "read_log_chunk": (tools.read_log_chunk(log, 1, 3), "3 行"),
        "search_logs": (tools.search_logs(log, "ERROR", context=2), "命中 2 行"),
        "list_code_files": (tools.list_code_files(str(code_repo)), "2 个文件"),
        "read_code_file": (tools.read_code_file(str(code_repo), "app/order.py", 2, 2), "L2-2 / 3 行"),
        "grep_code": (tools.grep_code(str(code_repo), "pay", path_glob="*.py"), "命中 1 处"),
    }
    for name, (output, expected) in cases.items():
        summary, failed = summarize_tool_output(name, output)
        assert not failed, name
        assert expected in summary, (name, summary)


def test_summary_marks_errors() -> None:
    summary, failed = summarize_tool_output("read_log_chunk", "[错误] 日志文件不存在: x")
    assert failed and "不存在" in summary


def test_split_complete_blocks_respects_fences() -> None:
    done, rest = split_complete_blocks("para one\n\n```\ncode\n\nmore\n")
    assert done == "para one"
    assert rest.startswith("```")


def test_context_message_lists_every_log() -> None:
    msg = cli._build_context_message(["/a.log", "/b.log"], [], "为什么？")
    assert "共提供了 2 份日志" in msg and "/b.log" in msg and "只分析日志" in msg


def test_cli_requires_api_key(sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = runner.invoke(cli.app, ["analyze", "-l", str(sample_log)])
    assert result.exit_code == 1
    assert "OPENAI_API_KEY" in result.output


def test_cli_rejects_bad_glob(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = runner.invoke(cli.app, ["analyze", "-l", str(tmp_path / "*.log")])
    assert result.exit_code == 2
    assert "通配符" in result.output


def test_chat_rejects_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = runner.invoke(cli.app, ["chat", "-l", "-"])
    assert result.exit_code == 2


def test_chat_end_to_end(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """两轮对话 + 斜杠命令 + /save，然后用同一会话名续上，验证元数据与历史都被保存。"""
    from log_agent import agent as agent_module

    script = [
        tool_call("log_overview", "c1", path=str(sample_log)),
        AIMessage(content="第一轮：有 2 条 ERROR。"),
        AIMessage(content="第二轮：根因是 KeyError。"),
    ]
    real_build = agent_module.build_agent
    monkeypatch.setattr(
        agent_module, "build_agent",
        lambda **kw: real_build(model=ScriptedChatModel(script=script), checkpointer=kw.get("checkpointer")),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    db = tmp_path / "sessions.db"
    report = tmp_path / "saved.md"

    user_input = f"有几条错误？\n/help\n/stats\n根因是什么？\n/save {report}\n/unknown\nexit\n"
    result = runner.invoke(cli.app, ["chat", "-l", str(sample_log), "-s", "demo", "--db", str(db)], input=user_input)
    assert result.exit_code == 0, result.output
    assert "未知命令" in result.output
    assert "本次共 2 轮" in result.output
    assert "第二轮：根因是 KeyError。" in report.read_text(encoding="utf-8")

    listed = runner.invoke(cli.app, ["sessions", "list", "--db", str(db)])
    assert "demo" in listed.output

    resumed = runner.invoke(cli.app, ["chat", "-l", str(sample_log), "-s", "demo", "--db", str(db)], input="exit\n")
    assert "已加载会话 'demo' 的历史" in resumed.output

    removed = runner.invoke(cli.app, ["sessions", "rm", "demo", "--db", str(db)])
    assert "已删除" in removed.output


@pytest.mark.parametrize("suffix", [".md", ".json"])
def test_analyze_end_to_end_with_export(
    sample_log: Path, code_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    """用剧本模型跑通 agent → 工具 → 渲染 → 导出 全链路，不依赖真实 API。"""
    from log_agent import agent as agent_module

    script = [
        tool_call("log_overview", "c1", path=str(sample_log)),
        tool_call("search_logs", "c2", path=str(sample_log), pattern="KeyError", context=2),
        tool_call("grep_code", "c3", code_dir=str(code_repo), pattern="order_id", regex=False),
        AIMessage(content="### 问题概述\n\n支付时缺少 order_id。\n\n### 关键证据\n\n- app.log:7\n- app/order.py:3"),
    ]
    real_build = agent_module.build_agent
    monkeypatch.setattr(
        agent_module, "build_agent", lambda **kw: real_build(model=ScriptedChatModel(script=script))
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    out_file = tmp_path / f"report{suffix}"

    result = runner.invoke(
        cli.app, ["analyze", "-l", str(sample_log), "-c", str(code_repo), "-o", str(out_file), "--max-steps", "20"]
    )
    assert result.exit_code == 0, result.output
    content = out_file.read_text(encoding="utf-8")
    if suffix == ".json":
        data = json.loads(content)
        assert data["status"] == "ok"
        assert [t["name"] for t in data["tool_calls"]] == ["log_overview", "search_logs", "grep_code"]
        assert "order_id" in data["report"]
        assert data["usage"]["total"] > 0
    else:
        assert content.startswith("# 日志分析报告")
        assert "app/order.py:3" in content
        assert "工具调用 3 次" in content
