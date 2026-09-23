from __future__ import annotations

import json
import re
from io import StringIO
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from rich.console import Console
from typer.testing import CliRunner

from log_agent import cli
from log_agent.citations import CitationLinker, LinkedMarkdown
from log_agent.render import parse_summary_line

from .conftest import ScriptedChatModel

runner = CliRunner()


# ---------------------------------------------------------------------------
# 一句话结论
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("一句话结论：Router.select 未判空（可信度：高）", ("Router.select 未判空", "高")),
        ("**一句话结论**：配额耗尽导致降级 (可信度: 中)", ("配额耗尽导致降级", "中")),
        ("> 一句话结论：证据不足，疑似超时（可信度：低）", ("证据不足，疑似超时", "低")),
        ("一句话结论：缺少 order_id", ("缺少 order_id", "")),
    ],
)
def test_parse_summary_line(line: str, expected: tuple[str, str]) -> None:
    assert parse_summary_line(line) == expected


def test_parse_summary_line_ignores_other_text() -> None:
    assert parse_summary_line("### 结论") is None
    assert parse_summary_line("结论是配额耗尽（可信度：高）") is None
    assert parse_summary_line("一句话结论：") is None


# ---------------------------------------------------------------------------
# 可点击引用
# ---------------------------------------------------------------------------


def test_linker_resolves_logs_and_code(sample_log: Path, code_repo: Path) -> None:
    linker = CitationLinker([str(sample_log)], [str(code_repo)], mode="vscode")
    text = linker.apply(
        f"见 `{sample_log.name}:7` 与 `app/order.py:2-3`，以及 `{code_repo.name}/app/order.py:3`；`missing.py:1` 不存在。"
    )
    order = (code_repo / "app" / "order.py").resolve().as_posix()
    assert f"](vscode://file/{sample_log.as_posix()}:7)" in text
    assert f"[`app/order.py:2-3`](vscode://file/{order}:2)" in text
    assert f"](vscode://file/{order}:3)" in text
    assert "`missing.py:1` 不存在" in text and "[`missing.py:1`]" not in text


def test_linker_leaves_fences_and_existing_links(sample_log: Path) -> None:
    linker = CitationLinker([str(sample_log)], [], mode="file")
    ref = f"`{sample_log.name}:1`"
    text = f"```\n{ref}\n```\n[{ref}](http://x)"
    assert linker.apply(text) == text


def test_linker_off_and_duplicate_names(tmp_path: Path) -> None:
    a, b = tmp_path / "a" / "app.log", tmp_path / "b" / "app.log"
    for path in (a, b):
        path.parent.mkdir()
        path.write_text("x\n", encoding="utf-8")
    assert CitationLinker([str(a)], [], mode="off").apply("`app.log:1`") == "`app.log:1`"
    # 两份同名日志无法判断指哪一份，不生成链接
    assert CitationLinker([str(a), str(b)], [], mode="file").apply("`app.log:1`") == "`app.log:1`"


@pytest.mark.parametrize("mode", ["file", "vscode"])
def test_linked_markdown_renders_osc8(sample_log: Path, mode: str) -> None:
    linker = CitationLinker([str(sample_log)], [], mode=mode)
    buffer = StringIO()
    # Windows CI 没有真实控制台，Rich 会自动判成 legacy_windows 并按设计不输出 OSC 8；这里模拟的是现代终端
    console = Console(file=buffer, force_terminal=True, legacy_windows=False, width=100)
    console.print(LinkedMarkdown(linker.apply(f"见 `{sample_log.name}:3`，[恶意](javascript:alert(1))")))
    targets = {t for t in re.findall(r"\x1b\]8;[^;]*;([^\x1b\a]*)", buffer.getvalue()) if t}
    expected = sample_log.as_uri() if mode == "file" else f"vscode://file/{sample_log.as_posix()}:3"
    # 只放行 file: / vscode:，模型输出里的 javascript: 链接仍按 markdown-it 默认规则拒绝
    assert targets == {expected}


# ---------------------------------------------------------------------------
# 端到端：结论横幅、JSON 字段、续会话、/copy
# ---------------------------------------------------------------------------


def _patch_agent(monkeypatch: pytest.MonkeyPatch, script: list) -> None:
    from log_agent import agent as agent_module

    real_build = agent_module.build_agent
    monkeypatch.setattr(
        agent_module, "build_agent",
        lambda **kw: real_build(model=ScriptedChatModel(script=script), checkpointer=kw.get("checkpointer")),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test")


def test_analyze_shows_summary_and_exports_it(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report = "一句话结论：支付请求缺少 order_id（可信度：高）\n\n### 结论\n\n详见证据。"
    _patch_agent(monkeypatch, [AIMessage(content=report)])
    out = tmp_path / "r.json"
    result = runner.invoke(cli.app, ["analyze", "-l", str(sample_log), "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert "支付请求缺少 order_id" in result.output and "可信度 高" in result.output
    assert "一句话结论" not in result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"] == "支付请求缺少 order_id" and data["confidence"] == "高"
    assert data["report"].startswith("一句话结论")


def test_analyze_without_summary_keeps_fields_empty(
    sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_agent(monkeypatch, [AIMessage(content="只是一段回答。")])
    out = tmp_path / "r.json"
    result = runner.invoke(cli.app, ["analyze", "-l", str(sample_log), "-o", str(out)])
    assert result.exit_code == 0, result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["summary"] is None and data["confidence"] is None


def test_chat_resume_reuses_sources(
    sample_log: Path, code_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_agent(monkeypatch, [AIMessage(content="第一轮"), AIMessage(content="第二轮"), AIMessage(content="第三轮")])
    db = tmp_path / "sessions.db"
    first = runner.invoke(
        cli.app, ["chat", "-l", str(sample_log), "-c", str(code_repo), "-s", "demo", "--db", str(db)],
        input="问题一\nexit\n",
    )
    assert first.exit_code == 0, first.output

    by_name = runner.invoke(cli.app, ["chat", "-s", "demo", "--db", str(db)], input="问题二\n/sources\nexit\n")
    assert by_name.exit_code == 0, by_name.output
    assert "沿用上次的日志与源码" in by_name.output
    assert sample_log.name in by_name.output and code_repo.name in by_name.output

    latest = runner.invoke(cli.app, ["chat", "--resume", "--db", str(db)], input="问题三\nexit\n")
    assert latest.exit_code == 0, latest.output
    assert "已加载会话 'demo' 的历史" in latest.output


def test_chat_resume_errors(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    db = tmp_path / "sessions.db"
    no_log = runner.invoke(cli.app, ["chat", "--db", str(db)])
    assert no_log.exit_code == 2 and "--resume" in no_log.output
    nothing = runner.invoke(cli.app, ["chat", "--resume", "--db", str(db)])
    assert nothing.exit_code == 2 and "还没有可以续上的会话" in nothing.output
    unknown = runner.invoke(cli.app, ["chat", "-s", "nope", "--db", str(db)])
    assert unknown.exit_code == 2 and "-l" in unknown.output

    import sqlite3

    from log_agent.sessions import SessionStore

    gone = tmp_path / "gone.log"
    conn = sqlite3.connect(str(db))
    SessionStore(conn).touch("old", [str(gone)], [], "m")
    conn.close()
    missing = runner.invoke(cli.app, ["chat", "-s", "old", "--db", str(db)])
    assert missing.exit_code == 2 and "已不存在" in missing.output


def test_chat_copy_command(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import clipboard

    copied: list[str] = []
    monkeypatch.setattr(clipboard, "copy_text", lambda text: copied.append(text) or "pbcopy")
    _patch_agent(monkeypatch, [AIMessage(content="根因是 KeyError。")])
    result = runner.invoke(
        cli.app, ["chat", "-l", str(sample_log), "--db", str(tmp_path / "s.db")],
        input="/copy\n问题\n/copy\nexit\n",
    )
    assert result.exit_code == 0, result.output
    assert "还没有可以复制的回答" in result.output
    assert "已复制上一条回答" in result.output
    assert copied == ["根因是 KeyError。\n"]
