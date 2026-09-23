from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.budget import TokenBudget, format_tokens, parse_budget
from log_agent.compare import diff_signatures, parse_range
from log_agent.tools import compare_windows
from log_agent.watch import FollowError, LogFollower, TriggerBatch, batch_question, build_matcher

from .conftest import ScriptedChatModel, tool_call

# ---------------------------------------------------------------------------
# 预算
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "value"), [("200k", 200_000), ("1.5m", 1_500_000), ("150000", 150_000), (" 20K ", 20_000)],
)
def test_parse_budget(text: str, value: int) -> None:
    assert parse_budget(text) == value


@pytest.mark.parametrize("text", ["", "abc", "5k", "200g"])
def test_parse_budget_rejects_bad_values(text: str) -> None:
    with pytest.raises(ValueError):
        parse_budget(text)


def test_format_tokens() -> None:
    assert format_tokens(200_000) == "200k"
    assert format_tokens(1_000_000) == "1m"
    assert format_tokens(1_500_000) == "1.5m"


def test_budget_wraps_up_after_threshold_and_resets() -> None:
    budget = TokenBudget(10_000)
    assert budget.threshold == 8_000
    assert not budget.should_wrap_up()
    budget._used = 8_000
    assert budget.should_wrap_up() and budget.wrapped
    budget.reset()
    assert budget.used == 0 and not budget.wrapped


class _ToolsRecorder(ScriptedChatModel):
    bound: list[list[str]] = []
    systems: list[str] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _ToolsRecorder:
        self.bound.append([t.get("name") if isinstance(t, dict) else getattr(t, "name", "") for t in tools])
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        self.systems.append(str(messages[0].content))
        yield from super()._stream(messages, stop, run_manager, **kwargs)


def test_budget_removes_tools_and_reports_wrap_up(
    sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from log_agent import agent as agent_module
    from log_agent import budget as budget_module

    # 每次模型调用 120 tokens（见 ScriptedChatModel）；把收尾比例调到 1%，第二次调用就该收尾
    monkeypatch.setattr(budget_module, "WRAP_UP_RATIO", 0.01)
    model = _ToolsRecorder(
        script=[
            tool_call("log_overview", "o1", path=str(sample_log)),
            AIMessage(content="一句话结论：支付缺 order_id（可信度：中）\n\n证据不足处待确认"),
        ],
        bound=[],
        systems=[],
    )
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model, budget=kw.get("budget")))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    out = tmp_path / "r.json"

    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "--budget", "10k", "-o", str(out)])

    assert result.exit_code == 0, result.output
    assert "log_overview" in model.bound[0]
    # 工具列表为空时 langchain 不会再 bind_tools：只绑定过第一次，说明收尾那次调用确实没有工具
    assert len(model.bound) == 1
    assert "预算提示" in model.systems[-1] and "预算提示" not in model.systems[0]
    assert "接近 tokens 预算 10k" in result.output
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["budget_hit"] is True and data["finding"] is True


def test_bad_budget_fails_fast(sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "--budget", "lots"])
    assert result.exit_code == 2
    assert "看不懂的预算写法" in result.output


# ---------------------------------------------------------------------------
# --fail-on 退出码
# ---------------------------------------------------------------------------


def _run_with_report(monkeypatch: pytest.MonkeyPatch, sample_log: Path, report: str, *args: str):
    from log_agent import agent as agent_module

    model = ScriptedChatModel(script=[AIMessage(content=report)])
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    return CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), *args])


@pytest.mark.parametrize(
    ("report", "level", "code"),
    [
        ("一句话结论：Router.select 未判空（可信度：高）", "high", 3),
        ("一句话结论：Router.select 未判空（可信度：中）", "high", 0),
        ("一句话结论：Router.select 未判空（可信度：中）", "medium", 3),
        ("一句话结论：未发现异常，只有 3 条 WARN（可信度：高）", "low", 0),
        ("### 结论\n\n没有一句话结论", "low", 4),
    ],
)
def test_fail_on_exit_codes(
    monkeypatch: pytest.MonkeyPatch, sample_log: Path, report: str, level: str, code: int
) -> None:
    result = _run_with_report(monkeypatch, sample_log, report, "--fail-on", level)
    assert result.exit_code == code, result.output


def test_without_fail_on_findings_still_exit_zero(monkeypatch: pytest.MonkeyPatch, sample_log: Path) -> None:
    result = _run_with_report(monkeypatch, sample_log, "一句话结论：出错了（可信度：高）")
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# 窗口对比
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "since", "until"),
    [
        ("13:00~13:30", "13:00", "13:30"),
        ("13:00-13:30", "13:00", "13:30"),
        ("2026-06-09 13:00 ~ 2026-06-09 13:30", "2026-06-09 13:00", "2026-06-09 13:30"),
    ],
)
def test_parse_range(text: str, since: str, until: str) -> None:
    window = parse_range(text)
    assert (window.since.raw, window.until.raw) == (since, until)


@pytest.mark.parametrize("text", ["13:00", "2026-06-09 13:00-2026-06-09 13:30", "~13:30", "13:30~13:00"])
def test_parse_range_rejects(text: str) -> None:
    with pytest.raises(ValueError):
        parse_range(text)


def test_diff_signatures_normalizes_by_line_rate() -> None:
    base = {"timeout": 10, "retry": 10}
    target = {"timeout": 12, "retry": 60, "npe": 5}
    new, surged, dropped = diff_signatures(base, target, 1000, 1000)
    assert new == [("npe", 5)]
    # timeout 只是略增，不算"明显增多"
    assert [s[0] for s in surged] == ["retry"]
    assert dropped == []

    # 目标时段流量是基线的 6 倍：retry 次数虽然涨了 6 倍，出现率没变
    _, surged, _ = diff_signatures(base, target, 1000, 6000)
    assert surged == []


@pytest.fixture
def two_phase_log(tmp_path: Path) -> Path:
    lines = []
    for minute in range(10):
        lines.append(f"2026-06-09 13:{minute:02d}:00 INFO  ok request id={minute}")
        if minute % 5 == 0:
            lines.append(f"2026-06-09 13:{minute:02d}:30 ERROR cache miss key={minute}")
    for minute in range(10):
        lines.append(f"2026-06-09 14:{minute:02d}:00 INFO  ok request id={minute}")
        lines.append(f"2026-06-09 14:{minute:02d}:10 ERROR NullPointerException in Router.select id={minute}")
        lines.append(f"2026-06-09 14:{minute:02d}:20 ERROR cache miss key={minute}")
    path = tmp_path / "gateway.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_compare_windows_reports_new_and_surged_errors(two_phase_log: Path) -> None:
    result = compare_windows(str(two_phase_log), "13:00", "13:59", since="14:00", until="14:59")
    text = str(result)
    assert result.status == "ok"
    assert "基线：13:00 → 13:59" in text and "目标：14:00 → 14:59" in text
    assert "新出现的错误" in text and "NullPointerException in Router.select" in text
    assert "明显增多" in text and "cache miss" in text
    assert "新出现的异常类型：NullPointerException" in text
    assert result.artifact["base_errors"] == 2 and result.artifact["target_errors"] == 20


def test_compare_windows_uses_default_window_as_target(two_phase_log: Path) -> None:
    from log_agent.timefilter import set_default_window

    set_default_window("14:00", "14:59")
    text = str(compare_windows(str(two_phase_log), "13:00", "13:59"))
    assert "目标：14:00 → 14:59" in text


def test_compare_windows_errors(two_phase_log: Path) -> None:
    assert compare_windows(str(two_phase_log), "13:00", "").status == "error"
    assert "基线时段有误" in str(compare_windows(str(two_phase_log), "25:00", "26:00"))
    assert compare_windows(str(two_phase_log), "09:00", "09:30").artifact["kind"] == "empty_window"


def test_baseline_option_reaches_first_message(
    two_phase_log: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from log_agent import agent as agent_module

    seen: list[str] = []

    class _Capture(ScriptedChatModel):
        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(str(messages[-1].content))
            yield from super()._stream(messages, stop, run_manager, **kwargs)

    model = _Capture(script=[AIMessage(content="一句话结论：ok（可信度：高）")])
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = CliRunner().invoke(
        cli.app, ["analyze", "-l", str(two_phase_log), "--since", "14:00", "--baseline", "13:00~13:59"],
    )
    assert result.exit_code == 0, result.output
    assert "基线" in result.output
    assert 'compare_windows（baseline_since="13:00"，baseline_until="13:59"' in seen[0]
    assert "目标时段为上面的时间窗口" in seen[0]


def test_bad_baseline_fails_fast(sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "--baseline", "13:00"])
    assert result.exit_code == 2 and "看不懂的时间段" in result.output


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------


def test_follower_reads_only_new_complete_lines(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text("old 1\nold 2\n", encoding="utf-8")
    follower = LogFollower(str(path))
    assert follower.poll() == []

    with path.open("a", encoding="utf-8") as f:
        f.write("new 3\nhalf")
    assert follower.poll() == [(3, "new 3")]
    with path.open("a", encoding="utf-8") as f:
        f.write(" line\r\n")
    assert follower.poll() == [(4, "half line")]


def test_follower_restarts_after_truncation(tmp_path: Path) -> None:
    path = tmp_path / "app.log"
    path.write_text("a\nb\nc\n", encoding="utf-8")
    follower = LogFollower(str(path))
    path.write_text("x\n", encoding="utf-8")
    assert follower.poll() == [(1, "x")]
    assert follower.rotations == 1


def test_follower_rejects_gzip(tmp_path: Path) -> None:
    import gzip

    path = tmp_path / "app.log.gz"
    with gzip.open(path, "wt") as f:
        f.write("x\n")
    with pytest.raises(FollowError):
        LogFollower(str(path))


def test_matcher_defaults_to_error_levels() -> None:
    match = build_matcher(None)
    assert match("2026-06-09 10:00:03 ERROR boom")
    assert match("[FATAL] disk full")
    assert not match("2026-06-09 10:00:03 WARN slow")
    assert build_matcher(r"timeout")("upstream timeout")
    with pytest.raises(FollowError):
        build_matcher("(")


def test_trigger_batch_debounce_and_max_wait() -> None:
    batch = TriggerBatch(debounce=10, max_wait=30)
    assert not batch.ready(0)
    batch.add("/l/app.log", 5, "ERROR a", now=0)
    batch.add("/l/app.log", 9, "ERROR a", now=5)
    assert not batch.ready(10)
    assert batch.ready(15)

    busy = TriggerBatch(debounce=10, max_wait=30)
    for t in range(0, 31, 5):
        busy.add("/l/app.log", t, f"ERROR {t}", now=t)
    assert busy.ready(30)

    hits = batch.drain()
    assert hits.count == 2 and batch.count == 0
    assert len(hits.samples) == 1
    question = batch_question(hits, "为什么？", matched_by_pattern=False)
    assert "app.log：L5-L9，共 2 条" in question and "app.log:5" in question and question.endswith("为什么？")


def test_watch_once_analyzes_new_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import agent as agent_module
    from log_agent import watch as watch_module

    path = tmp_path / "app.log"
    path.write_text("2026-06-09 10:00:00 ERROR old failure\n", encoding="utf-8")
    seen: list[str] = []

    class _Capture(ScriptedChatModel):
        def _stream(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(str(messages[-1].content))
            yield from super()._stream(messages, stop, run_manager, **kwargs)

    model = _Capture(script=[AIMessage(content="一句话结论：新错误（可信度：高）")])
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model))
    monkeypatch.setenv("OPENAI_API_KEY", "test")

    clock = {"now": 0.0}
    monkeypatch.setattr("time.monotonic", lambda: clock["now"])

    def fake_sleep(_seconds: float) -> None:
        if clock["now"] == 0.0:
            with path.open("a", encoding="utf-8") as f:
                f.write("2026-06-09 10:01:00 INFO fine\n2026-06-09 10:01:01 ERROR new failure id=7\n")
        clock["now"] += 5.0

    monkeypatch.setattr("time.sleep", fake_sleep)
    monkeypatch.setattr(watch_module, "POLL_INTERVAL", 0)

    result = CliRunner().invoke(
        cli.app, ["watch", "-l", str(path), "--once", "--debounce", "1", "--cooldown", "0"],
    )
    assert result.exit_code == 0, result.output
    assert "追踪模式" in result.output and "新增 1 条" in result.output
    assert "app.log：L3，共 1 条" in seen[0]
    assert "new failure" in seen[0] and "old failure" not in seen[0]
