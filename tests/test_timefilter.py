from __future__ import annotations

from datetime import datetime, time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_agent import cli, tools
from log_agent.render import summarize_tool_output
from log_agent.timefilter import (
    WindowTracker,
    find_timestamp,
    parse_bound,
    parse_window,
    set_default_window,
)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("2026-06-09 14:02:03,123 ERROR x", datetime(2026, 6, 9, 14, 2, 3, 123000)),
        ("2026-06-09T14:02:03.5Z INFO", datetime(2026, 6, 9, 14, 2, 3, 500000)),
        ("[2026/06/09 14:02:03] WARN", datetime(2026, 6, 9, 14, 2, 3)),
        ("Jun  9 14:02:03 host sshd[1]: ok", time(14, 2, 3)),
        ("  at com.foo.Bar.run(Bar.java:10)", None),
        ("2026-13-40 14:02:03 bad date", None),
    ],
)
def test_find_timestamp(line: str, expected: object) -> None:
    found = find_timestamp(line)
    assert (found[1] if found else None) == expected


def test_message_body_time_is_ignored() -> None:
    assert find_timestamp("INFO " + "x" * 90 + " retry at 12:00:00") is None


def test_upper_bound_includes_whole_precision() -> None:
    assert parse_bound("2026-06-09 14:05", upper=True).value == datetime(2026, 6, 9, 14, 5, 59, 999999)
    assert parse_bound("2026-06-09", upper=True).value == datetime(2026, 6, 9, 23, 59, 59, 999999)
    assert parse_bound("14:05", upper=True).value == time(14, 5, 59, 999999)
    assert parse_bound("23:59", upper=True).value == time(23, 59, 59, 999999)
    assert parse_bound("2026-06-09T14:05:30", upper=False).value == datetime(2026, 6, 9, 14, 5, 30)


@pytest.mark.parametrize("bad", ["yesterday", "14", "2026-06-09 25:00"])
def test_parse_bound_rejects_garbage(bad: str) -> None:
    with pytest.raises(ValueError, match="无法识别"):
        parse_bound(bad, upper=False)


def test_window_rejects_reversed_bounds() -> None:
    with pytest.raises(ValueError, match="晚于"):
        parse_window("2026-06-09 15:00", "2026-06-09 14:00")


def test_time_only_bound_matches_datetime_lines() -> None:
    window = parse_window("10:00:03", "10:00:04")
    assert window.contains(datetime(2026, 6, 9, 10, 0, 3))
    assert not window.contains(datetime(2026, 6, 9, 10, 0, 5))


def test_tracker_inherits_timestamp_for_stack_lines() -> None:
    tracker = WindowTracker(parse_window("2026-06-09 10:00:03", "2026-06-09 10:00:03"))
    assert not tracker.accept("2026-06-09 10:00:02 INFO a")
    assert tracker.accept("2026-06-09 10:00:03 ERROR boom")
    assert tracker.accept("Traceback (most recent call last):")
    assert not tracker.accept("2026-06-09 10:00:04 INFO b")
    assert not tracker.accept("  continuation of 10:00:04")


def test_tracker_stops_well_past_end() -> None:
    tracker = WindowTracker(parse_window(None, "2026-06-09 10:00"))
    tracker.accept("2026-06-09 10:04:00 INFO still within tolerance")
    assert not tracker.done
    tracker.accept("2026-06-09 10:07:00 INFO far past")
    assert tracker.done


# --- 工具集成 -----------------------------------------------------------------


def test_search_logs_with_window_keeps_traceback(sample_log: Path) -> None:
    out = tools.search_logs(str(sample_log), "KeyError|ERROR", since="10:00:03", until="10:00:03")
    assert out.status == "ok"
    assert "4: " in out and "7: " in out
    assert "9: " not in out
    assert out.meta["window"] == "10:00:03 → 10:00:03"


def test_search_logs_window_no_match(sample_log: Path) -> None:
    out = tools.search_logs(str(sample_log), "ERROR", since="2026-06-09 10:00:06")
    assert out.status == "hint" and out.meta["kind"] == "no_match"
    assert "时间窗口" in out


def test_invalid_window_is_tool_error(sample_log: Path) -> None:
    out = tools.search_logs(str(sample_log), "ERROR", since="later")
    assert out.status == "error" and out.startswith("[错误]")


def test_overview_window_stats(sample_log: Path) -> None:
    out = tools.log_overview(str(sample_log), since="2026-06-09 10:00:04")
    assert "共 10 行" in out
    assert "对应 L8-L10" in out
    assert out.meta["errors"] == 1
    assert out.meta["window_lines"] == 3
    summary, failed = summarize_tool_output("log_overview", out)
    assert not failed and "窗口内 3 行" in summary


def test_overview_empty_window(sample_log: Path) -> None:
    out = tools.log_overview(str(sample_log), since="2026-06-10")
    assert out.status == "hint" and out.meta["kind"] == "empty_window"
    assert "2026-06-09 10:00:00 → 2026-06-09 10:00:06" in out


def test_window_ignored_when_log_has_no_timestamps(tmp_path: Path) -> None:
    log = tmp_path / "plain.log"
    log.write_text("start\nERROR boom\nend\n", encoding="utf-8")
    out = tools.search_logs(str(log), "ERROR", since="10:00")
    assert out.status == "ok" and "已忽略时间窗口" in out


def test_default_window_applies_and_explicit_overrides(sample_log: Path) -> None:
    set_default_window("2026-06-09 10:00:05", None)
    assert tools.search_logs(str(sample_log), "ERROR").meta["hits"] == 1
    assert tools.search_logs(str(sample_log), "ERROR", since="10:00:00").meta["hits"] == 2


# --- 结构化结果 ---------------------------------------------------------------


def test_langchain_tools_carry_artifact(sample_log: Path) -> None:
    by_name = {t.name: t for t in tools.as_langchain_tools()}
    assert set(by_name) == {f.__name__ for f in tools.ALL_TOOLS}
    assert {"since", "until"} <= set(by_name["search_logs"].args)

    msg = by_name["search_logs"].invoke(
        {"type": "tool_call", "id": "c1", "name": "search_logs", "args": {"path": str(sample_log), "pattern": "ERROR"}}
    )
    assert msg.artifact == {"status": "ok", "hits": 2, "truncated": False, "window": None}
    assert msg.content.startswith("4: ")
    assert summarize_tool_output("search_logs", msg) == ("命中 2 行", False)

    missing = by_name["read_log_chunk"].invoke(
        {"type": "tool_call", "id": "c2", "name": "read_log_chunk", "args": {"path": "nope.log"}}
    )
    summary, failed = summarize_tool_output("read_log_chunk", missing)
    assert failed and summary == "日志文件不存在: nope.log"


def test_summary_is_independent_of_body_wording(sample_log: Path) -> None:
    out = tools.search_logs(str(sample_log), "ERROR")
    reworded = tools.ToolOutput("完全不同的正文", out.status, **out.meta)
    assert summarize_tool_output("search_logs", reworded) == ("命中 2 行", False)


# --- CLI ---------------------------------------------------------------------


def test_cli_rejects_bad_time(sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "--since", "noon"])
    assert result.exit_code == 2
    assert "无法识别的时间" in result.output
