from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_agent import cli, tools
from log_agent.compare import diff_signatures
from log_agent.timefilter import (
    WindowTracker,
    default_timezone,
    find_timestamp,
    parse_bound,
    parse_window,
    set_default_timezone,
)
from log_agent.watch import build_matcher


def test_comparison_uses_rates_in_both_directions() -> None:
    assert diff_signatures({"timeout": 100}, {"timeout": 50}, 10000, 1000) == (
        [], [("timeout", 100, 50)], [],
    )
    assert diff_signatures({"timeout": 50}, {"timeout": 100}, 1000, 10000) == (
        [], [], [("timeout", 50, 100)],
    )
    assert diff_signatures({"timeout": 100}, {"timeout": 50}, 10000, 5000) == ([], [], [])
    assert diff_signatures({"timeout": 1}, {"timeout": 2}, 1000, 1000) == ([], [], [])
    assert diff_signatures({"timeout": 5}, {}, 1000, 1000) == ([], [], [("timeout", 5, 0)])


@pytest.mark.parametrize("level", ["error", "Error", "critical", "severe", "FATAL", "err"])
def test_watch_understands_json_and_text_levels(level: str) -> None:
    matcher = build_matcher(None)
    assert matcher(f"2026-09-28 08:00:00 {level} failed")
    assert matcher(json.dumps({"message": "x" * 300, "level": level}))
    assert not matcher(json.dumps({"message": "ERROR happened before", "level": "info"}))
    assert not matcher(json.dumps({"message": "ERROR happened before"}))


def test_json_overview_clusters_messages_but_preserves_codes(tmp_path: Path) -> None:
    log = tmp_path / "structured.log"
    records = [
        {"requestId": str(i), "level": "error", "message": f"upstream failed order={i}", "status": code}
        for i, code in enumerate([401, 401, 500])
    ]
    records.append({"message": "ERROR previously", "severity": "info"})
    log.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    result = tools.log_overview(str(log))
    assert result.meta["errors"] == 3
    errors = result.meta["top_errors"]
    assert len(errors) == 2
    assert errors[0]["count"] == 2 and "status=401" in errors[0]["signature"]
    assert errors[1]["count"] == 1 and "status=500" in errors[1]["signature"]


def test_text_signatures_preserve_business_codes_and_http_status(tmp_path: Path) -> None:
    log = tmp_path / "text.log"
    log.write_text(
        "ERROR upstream status=401 request=123\nERROR upstream status=500 request=456\n"
        "ERROR vendor code=30008 request=123\nERROR vendor code=30009 request=456\n"
        "ERROR HTTP/1.1 401\nERROR HTTP/1.1 500\n",
        encoding="utf-8",
    )
    stats = tools._scan_overview(tools.open_log(log), parse_window(None, None))
    assert len(stats.signatures) == 6


def test_structured_timestamp_is_independent_of_field_order(tmp_path: Path) -> None:
    log = tmp_path / "structured.log"
    records = [
        {"message": "x" * 5000, "level": "error", "timestamp": f"2026-09-28T16:0{i}:00+08:00"}
        for i in range(2)
    ]
    log.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    result = tools.log_overview(str(log), since="2026-09-28T08:01:00Z", until="2026-09-28T08:01:00Z")
    assert result.meta["errors"] == 1 and result.meta["window_lines"] == 1


def test_offsets_and_bound_precision() -> None:
    first = find_timestamp("2026-09-28T08:00:00Z INFO a")[1]
    second = find_timestamp("2026-09-28T16:00:00+0800 INFO b")[1]
    assert first == second
    assert second.utcoffset() == timedelta(hours=8)
    upper = parse_bound("2026-09-28T16:00+08:00", upper=True).value
    assert upper.astimezone(UTC) == datetime(2026, 9, 28, 8, 0, 59, 999999, tzinfo=UTC)
    # 按绝对时刻而非墙上时刻判定起止顺序。
    parse_window("2026-09-28T16:00+08:00", "2026-09-28T09:00Z")
    with pytest.raises(ValueError, match="晚于"):
        parse_window("2026-09-28T09:00Z", "2026-09-28T16:00+08:00")


def test_mixed_zone_trace_order_and_naive_default(tmp_path: Path) -> None:
    set_default_timezone("+08:00")
    paths = []
    for name, stamp in [("late", "2026-09-28T08:00:02Z"), ("early", "2026-09-28T16:00:00+08:00"),
                        ("middle", "2026-09-28 16:00:01")]:
        path = tmp_path / f"{name}.log"
        path.write_text(f"{stamp} INFO trace=T42\n", encoding="utf-8")
        paths.append(str(path))
    result = tools.trace_request(paths, "T42")
    body = str(result).split("--- 按时间排序 ---\n")[1]
    assert body.index("early.log:1") < body.index("middle.log:1") < body.index("late.log:1")


def test_time_only_bounds_and_inherited_stack_use_configured_zone() -> None:
    set_default_timezone("+08:00")
    tracker = WindowTracker(parse_window("16:00", "16:00"))
    assert tracker.accept("2026-09-28T08:00:00Z ERROR failed")
    assert tracker.accept("  stack line")
    assert not tracker.accept("2026-09-28T08:01:00Z INFO done")
    tracker = WindowTracker(parse_window(None, "2026-09-28 16:00"))
    tracker.accept("2026-09-28T08:07:00Z INFO end")
    assert tracker.done


def test_overview_cache_respects_timezone(tmp_path: Path) -> None:
    log = tmp_path / "cache.log"
    log.write_text("2026-09-28T08:00:00Z ERROR failed\n", encoding="utf-8")
    set_default_timezone("+08:00")
    assert tools.log_overview(str(log), since="16:00", until="16:00").status == "ok"
    set_default_timezone("UTC")
    assert tools.log_overview(str(log), since="16:00", until="16:00").meta["kind"] == "empty_window"


@pytest.mark.parametrize("value", ["+25:00", "+08:70", "bad-timezone"])
def test_cli_rejects_invalid_zone_before_analysis(value: str, sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    result = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "--timezone", value])
    assert result.exit_code == 2 and "无法识别的时区" in result.output


def test_timezone_config_env_cli_precedence(sample_log: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_config import _run_analyze

    (tmp_path / ".log-agent.toml").write_text('timezone = "+08:00"\n', encoding="utf-8")
    result, _ = _run_analyze(monkeypatch, tmp_path, sample_log)
    assert result.exit_code == 0, result.output
    assert default_timezone().utcoffset(None) == timedelta(hours=8)
    monkeypatch.setenv("LOG_AGENT_TIMEZONE", "+09:00")
    result, _ = _run_analyze(monkeypatch, tmp_path, sample_log)
    assert result.exit_code == 0, result.output
    assert default_timezone().utcoffset(None) == timedelta(hours=9)
    result, _ = _run_analyze(monkeypatch, tmp_path, sample_log, "--timezone", "UTC")
    assert result.exit_code == 0, result.output
    assert default_timezone() == UTC
