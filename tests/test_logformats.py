from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_agent import cli, logformat, timefilter, tools
from log_agent.logformat import detect_format, level_and_body, parse_custom_formats
from log_agent.timefilter import find_timestamp

runner = CliRunner()
CST = timezone(timedelta(hours=8))


@pytest.fixture(autouse=True)
def _fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timefilter, "_now", lambda: datetime(2026, 9, 30, 12, 0, tzinfo=UTC))


def _docker(line: str) -> str:
    return json.dumps({"log": line + "\n", "stream": "stderr", "time": "2026-09-30T06:00:00.1Z"})


CASES = [
    # (行, 期望时间, 期望 (级别, 正文) 或 None, 期望格式)
    ('127.0.0.1 - - [30/Sep/2026:14:00:00 +0800] "GET /api/order/123?x=1 HTTP/1.1" 502 157 "-" "curl/8"',
     datetime(2026, 9, 30, 14, 0, tzinfo=CST), ("ERROR", "status=502 GET /api/order/123"), "nginx / Apache 访问日志"),
    ('10.0.0.1 - bob [30/Sep/2026:14:00:01 +0800] "POST /login HTTP/1.1" 401 12',
     datetime(2026, 9, 30, 14, 0, 1, tzinfo=CST), ("WARN", "status=401 POST /login"), "nginx / Apache 访问日志"),
    ("2026/09/30 14:00:00 [error] 123#0: *45 connect() failed (111: Connection refused)",
     datetime(2026, 9, 30, 14, 0), ("ERROR", "123#0: *45 connect() failed (111: Connection refused)"), "文本"),
    ("2026/09/30 14:00:00 [crit] 1#0: disk full", datetime(2026, 9, 30, 14, 0), ("FATAL", "1#0: disk full"), "文本"),
    ("<11>Sep 30 14:00:00 host app[123]: db connection lost", datetime(2026, 9, 30, 14, 0), "ERROR", "syslog"),
    ("30-Sep-2026 14:00:00.123 SEVERE [main] Catalina.start failed", datetime(2026, 9, 30, 14, 0, 0, 123000),
     ("FATAL", "[main] Catalina.start failed"), "文本"),
    ("E0930 14:00:00.123456    1234 controller.go:42] failed to sync pod", datetime(2026, 9, 30, 14, 0, 0, 123456),
     ("ERROR", "failed to sync pod"), "glog / klog"),
    ('time=2026-09-30T14:00:00+08:00 level=error msg="upstream timeout" status=504 path=/pay',
     datetime(2026, 9, 30, 14, 0, tzinfo=CST), ("ERROR", "status=504 upstream timeout"), "logfmt"),
    ('ts=1790740800.5 lvl=eror msg="db down"', datetime(2026, 9, 30, 4, 0, 0, 500000, tzinfo=UTC),
     ("ERROR", "db down"), "logfmt"),
    ('{"level":50,"time":1790740800123,"msg":"payment failed"}', datetime(2026, 9, 30, 4, 0, 0, 123000, tzinfo=UTC),
     ("ERROR", "payment failed"), "JSON（pino / bunyan）"),
    ('{"@t":"2026-09-30T06:00:00Z","@mt":"Order {Id} placed","Id":7}', datetime(2026, 9, 30, 6, 0, tzinfo=UTC),
     ("INFO", "Order {Id} placed"), "JSON（Serilog CLEF）"),
    ('{"@t":"2026-09-30T06:00:00Z","@l":"Error","@m":"Order failed"}', datetime(2026, 9, 30, 6, 0, tzinfo=UTC),
     ("ERROR", "Order failed"), "JSON（Serilog CLEF）"),
    ('{"@timestamp":"2026-09-30T06:00:00.000Z","log":{"level":"error"},"message":"ecs failure"}',
     datetime(2026, 9, 30, 6, 0, tzinfo=UTC), ("ERROR", "ecs failure"), "JSON"),
    (_docker("2026-09-30 14:00:00 ERROR inner app failure"), datetime(2026, 9, 30, 14, 0),
     ("ERROR", "inner app failure"), "Docker json-file"),
    (_docker("no timestamp WARN here"), datetime(2026, 9, 30, 6, 0, 0, 100000, tzinfo=UTC), ("WARN", "here"),
     "Docker json-file"),
    ('2026-09-30T06:00:00.123456789Z stderr F {"level":"error","msg":"cri json app"}',
     datetime(2026, 9, 30, 6, 0, 0, 123456, tzinfo=UTC), ("ERROR", "cri json app"), "CRI / containerd → JSON"),
    ("1790740800.123    150 10.0.0.1 TCP_MISS/503 0 GET http://x/ - DIRECT/- -",
     datetime(2026, 9, 30, 4, 0, 0, 123000, tzinfo=UTC), None, "文本"),
    ("14:00:00 WARN only time", time(14, 0), ("WARN", "only time"), "文本"),
]


@pytest.mark.parametrize(("line", "stamp", "level", "fmt"), CASES)
def test_builtin_formats(line: str, stamp, level, fmt: str) -> None:
    found = find_timestamp(line)
    assert (found[1] if found else None) == stamp
    parsed = level_and_body(line)
    assert (parsed[0] if isinstance(level, str) and parsed else parsed) == level
    assert detect_format(line) == fmt


def test_year_is_inferred_across_new_year(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timefilter, "_now", lambda: datetime(2027, 1, 2, 8, 0, tzinfo=UTC))
    assert find_timestamp("Dec 31 23:59:59 host app: x")[1] == datetime(2026, 12, 31, 23, 59, 59)
    assert find_timestamp("Jan  2 00:00:01 host app: x")[1] == datetime(2027, 1, 2, 0, 0, 1)


@pytest.mark.parametrize("value", [1790740800, 1790740800123, 1790740800123456, 1790740800123456789, "1790740800"])
def test_epoch_units(value) -> None:
    found = find_timestamp(json.dumps({"ts": value, "level": "info"}))
    assert found[1].replace(microsecond=0) == datetime(2026, 9, 30, 4, 0, tzinfo=UTC)


def test_access_log_overview_clusters_by_route_and_status(tmp_path: Path) -> None:
    rows = [f'10.0.0.{i} - - [30/Sep/2026:14:0{i}:00 +0800] "GET /api/order/{100 + i} HTTP/1.1" {code} 10'
            for i, code in enumerate([200, 502, 502, 404, 502])]
    log = tmp_path / "access.log"
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    timefilter.set_default_timezone("+08:00")  # 窗口边界按日志所在时区写
    out = tools.log_overview(str(log), since="2026-09-30 14:01", until="2026-09-30 14:04")
    assert out.meta["errors"] == 3 and out.meta["warnings"] == 1
    assert out.meta["top_errors"][0]["signature"] == "status=502 GET /api/order/#"


# ---------------------------------------------------------------------------
# 自定义格式
# ---------------------------------------------------------------------------

GATEWAY = {
    "name": "gateway",
    "pattern": r"^(?P<time>\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) \|(?P<level>\w)\| (?P<message>.*)$",
    "time_format": "%d.%m.%Y %H:%M:%S",
    "levels": {"E": "ERROR", "W": "WARN", "I": "INFO"},
    "sample": "30.09.2026 14:00:00 |E| upstream reset",
}


def test_custom_format_parses_time_level_and_message() -> None:
    logformat.set_custom_formats([GATEWAY])
    line = "30.09.2026 14:00:05 |E| upstream reset by peer"
    assert find_timestamp(line)[1] == datetime(2026, 9, 30, 14, 0, 5)
    assert level_and_body(line) == ("ERROR", "upstream reset by peer")
    assert detect_format(line) == "自定义：gateway"
    # 不匹配自定义格式的行照常走内置规则
    assert level_and_body("2026-09-30 14:00:00 WARN x") == ("WARN", "x")


def test_custom_format_without_time_format_uses_builtin_rules() -> None:
    logformat.set_custom_formats([{"pattern": r"^\[(?P<ts>[^\]]+)\] <(?P<level>\w+)> (?P<msg>.*)$"}])
    line = "[30/Sep/2026:14:00:00 +0800] <warning> slow"
    assert find_timestamp(line)[1] == datetime(2026, 9, 30, 14, 0, tzinfo=CST)
    assert level_and_body(line) == ("WARN", "slow")


@pytest.mark.parametrize(("spec", "message"), [
    ({"pattern": "(?P<time>"}, "不是合法的正则"),
    ({"pattern": r"^(?P<msg>.*)$"}, "至少要有"),
    ({"pattern": r"^(?P<level>\w)", "levels": {"E": "BAD"}}, "不是可识别的级别"),
    ({**GATEWAY, "sample": "nope"}, "sample 与 pattern 不匹配"),
    ({**GATEWAY, "sample": "99.99.2026 14:00:00 |E| x"}, "无法按 time_format"),
    ({**GATEWAY, "levels": {}, "sample": "30.09.2026 14:00:00 |Z| x"}, "级别"),
    ({**GATEWAY, "colour": "red"}, "不认识的字段"),
])
def test_custom_format_validation(spec: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_custom_formats([spec])


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    path = tmp_path / "cfg.toml"
    path.write_text(text, encoding="utf-8")
    monkeypatch.setenv("LOG_AGENT_CONFIG", str(path))


def test_inspect_uses_custom_format_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, monkeypatch, """
app_packages = ["com.acme"]

[[log_formats]]
name = "gateway"
pattern = '^(?P<time>\\d{2}\\.\\d{2}\\.\\d{4} \\d{2}:\\d{2}:\\d{2}) \\|(?P<level>\\w)\\| (?P<message>.*)$'
time_format = "%d.%m.%Y %H:%M:%S"
levels = { E = "ERROR", I = "INFO" }
sample = "30.09.2026 14:00:00 |E| upstream reset"
""")
    log = tmp_path / "gw.log"
    log.write_text("30.09.2026 14:00:00 |I| start\n30.09.2026 14:00:05 |E| upstream reset\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["inspect", "-l", str(log), "--since", "2026-09-30 14:00:03"])
    assert out.exit_code == 0, out.output
    assert "自定义：gateway 100%" in out.output
    assert "时间戳识别：2/2 行" in out.output and "ERROR 1" in out.output and "覆盖 1 行" in out.output


def test_single_log_formats_table_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _config(tmp_path, monkeypatch, "[log_formats]\npattern = '^(?P<level>[A-Z]+): (?P<message>.*)$'\n")
    log = tmp_path / "x.log"
    log.write_text("ERROR: boom\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["inspect", "-l", str(log)])
    assert out.exit_code == 0, out.output
    assert "自定义：custom-1" in out.output


@pytest.mark.parametrize("text", [
    "[[log_formats]]\npattern = '(?P<time>'\n",
    "app_packages = \"com.acme\"\n",
])
def test_bad_log_settings_fail_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    _config(tmp_path, monkeypatch, text)
    log = tmp_path / "x.log"
    log.write_text("x\n", encoding="utf-8")
    out = runner.invoke(cli.app, ["inspect", "-l", str(log)])
    assert out.exit_code == 2
    assert "自定义日志格式有误" in out.output or "app_packages" in out.output


# ---------------------------------------------------------------------------
# review findings (#43 / #44)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("line", "expected"), [
    ("30-Sep-2026 14:00:00.123 SEVERE [main] Catalina.start failed", "FATAL"),
    ("SEVERE: deployment failed", "FATAL"),
    ("FINE: pool warmed", "DEBUG"),
    ("2026-09-30 14:00:00 FINER entering", "TRACE"),
    ("2026-09-30 14:00:00 FINEST detail", "TRACE"),
    # 普通文本里的 fine / alert / panic 不是级别
    ("everything is fine", None),
    ("[alert-dispatcher] INFO sent", "INFO"),
    ("2026-09-30 14:00:00 [alert.sender] DEBUG queued", "DEBUG"),
    ("recovered from panic in handler", None),
    ("2026-09-30 14:00:00 notice board updated", None),
    # 独立的大写 token、方括号里的小写、Go 的行首 panic: 仍然识别
    ("2026-09-30 14:00:00 ALERT disk almost full", "FATAL"),
    ("2026-09-30 14:00:00 NOTICE config reloaded", "INFO"),
    ("[Wed Sep 30 14:00:00 2026] [core:notice] AH00094: started", "INFO"),
    ("2026/09/30 14:00:00 [emerg] 1#0: bind() failed", "FATAL"),
    ("panic: runtime error: index out of range", "FATAL"),
])
def test_text_levels(line: str, expected: str | None) -> None:
    parsed = level_and_body(line)
    assert (parsed[0] if parsed else None) == expected


@pytest.mark.parametrize("value", ["1e999", "-1e999", "1e309", "30.5", '"\u00b2"', '"99999999999999999999999"'])
def test_bad_numeric_levels_do_not_abort_scans(tmp_path: Path, value: str) -> None:
    line = f'{{"level": {value}, "msg": "x"}}'
    assert level_and_body(line) is None
    log = tmp_path / "x.log"
    log.write_text(line + '\n{"level": 50, "msg": "boom"}\n', encoding="utf-8")
    out = tools.log_overview(str(log))
    assert out.status == "ok" and "ERROR" in out


def test_app_line_with_quoted_request_is_not_an_access_log() -> None:
    line = '2026-06-09 14:02:03 WARN [t] "GET /x HTTP/1.1" 200 12ms'
    assert level_and_body(line) == ("WARN", '[t] "GET /x HTTP/1.1" 200 12ms')
    assert detect_format(line) != "nginx / Apache 访问日志"


def test_structured_record_with_log_field_is_not_docker() -> None:
    line = '{"@t":"2026-09-30T06:00:00Z","@m":"Order failed","log":"business value"}'
    assert level_and_body(line) == ("INFO", "Order failed")
    assert find_timestamp(line)[1] == datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
    assert detect_format(line) == "JSON（Serilog CLEF）"
    # 真正的 Docker 行仍然拆包
    assert level_and_body(_docker("2026-09-30 14:00:00 ERROR inner"))[0] == "ERROR"
    assert level_and_body(json.dumps({"log": "WARN only log"}))[0] == "WARN"


def test_time_only_custom_format_keeps_builtin_levels() -> None:
    logformat.set_custom_formats([{"pattern": r"^(?P<time>\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) (?P<message>.*)$",
                                   "time_format": "%d.%m.%Y %H:%M:%S"}])
    line = "30.09.2026 14:00:05 ERROR upstream reset"
    assert level_and_body(line) == ("ERROR", "upstream reset")
    assert find_timestamp(line)[1] == datetime(2026, 9, 30, 14, 0, 5)


def test_level_only_custom_format_keeps_builtin_timestamps(tmp_path: Path) -> None:
    logformat.set_custom_formats([{"pattern": r"^\S+ \S+ \|(?P<level>\w)\| (?P<message>.*)$",
                                   "levels": {"E": "ERROR", "I": "INFO"}}])
    line = "2026-09-30 14:00:05 |E| upstream reset"
    assert level_and_body(line) == ("ERROR", "upstream reset")
    assert find_timestamp(line)[1] == datetime(2026, 9, 30, 14, 0, 5)
    log = tmp_path / "gw.log"
    log.write_text("2026-09-30 13:00:00 |E| old failure\n" + line + "\n", encoding="utf-8")
    out = tools.log_overview(str(log), since="2026-09-30 14:00", until="2026-09-30 14:10")
    assert "upstream reset" in out and "old failure" not in out


def test_custom_time_format_without_date_is_a_time_of_day() -> None:
    logformat.set_custom_formats([{"pattern": r"^(?P<time>\d{2}:\d{2}:\d{2}) (?P<level>\w+) (?P<message>.*)$",
                                   "time_format": "%H:%M:%S", "sample": "14:00:05 ERROR x"}])
    assert find_timestamp("14:00:05 ERROR x")[1] == time(14, 0, 5)


def test_custom_yearless_date_accepts_feb_29() -> None:
    logformat.set_custom_formats([{"pattern": r"^(?P<time>\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (?P<level>\w+) (?P<message>.*)$",
                                   "time_format": "%m-%d %H:%M:%S"}])
    found = find_timestamp("02-29 10:00:00 ERROR leap")
    assert found is not None and (found[1].month, found[1].day) == (2, 29) and found[1].year % 4 == 0
    assert find_timestamp("09-30 10:00:00 ERROR x")[1] == datetime(2026, 9, 30, 10, 0)


def test_project_log_format_table_overrides_user_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent.config import load_config

    user = Path.home() / ".log-agent" / "config.toml"
    user.parent.mkdir(parents=True, exist_ok=True)
    user.write_text("[log_formats]\nname = 'user'\npattern = '^(?P<level>[A-Z]+): (?P<message>.*)$'\n",
                    encoding="utf-8")
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".log-agent.toml").write_text(
        "[log_formats]\nname = 'project'\npattern = '^(?P<level>[A-Z]+): (?P<msg>.*)$'\n", encoding="utf-8")
    monkeypatch.delenv("LOG_AGENT_CONFIG", raising=False)
    formats = load_config(project).shared["log_formats"]
    assert [f["name"] for f in formats] == ["project"]


def test_format_breakdown_counts_unrecognized_records(tmp_path: Path) -> None:
    from log_agent.diagnostics import _format_breakdown
    from log_agent.logfile import open_log

    log = tmp_path / "mixed.log"
    lines = ["2026-09-30 14:00:00 INFO start"] + [f"random record {i} without format" for i in range(9)]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    breakdown = _format_breakdown(open_log(str(log)))
    assert breakdown[0].startswith("未识别 90%"), breakdown
    # 缩进的续行仍然算上一条的一部分
    log.write_text("2026-09-30 14:00:00 INFO start\n  SELECT *\n  FROM t\n", encoding="utf-8")
    assert _format_breakdown(open_log(str(log))) == ["文本 100%"]
