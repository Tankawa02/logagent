from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli, redact
from log_agent.evidence import SourceResolver, check_analysis, check_one, summary_line
from log_agent.export import build_payload, to_markdown
from log_agent.render import TurnResult
from log_agent.report import report_body

from .conftest import ScriptedChatModel
from .test_report import analysis_data, report_text


def _ev(source: str, start: int, end: int, excerpt: str) -> dict:
    return {"source": source, "line_start": start, "line_end": end, "excerpt": excerpt}


def _check(sample_log: Path, code_dirs=(), **ev) -> dict:
    resolver = SourceResolver([str(sample_log)], list(code_dirs))
    item = check_one(resolver, _ev(**ev), 1, 1)
    return item.__dict__


# ---------------------------------------------------------------------------
# 单条证据
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("excerpt", [
    "2026-06-09 10:00:03 ERROR [order] payment failed order=1001",
    "ERROR [order] payment failed",                                  # 片段
    "4: 2026-06-09 10:00:03 ERROR [order] payment failed order=1001",  # read_log_chunk 前缀
    "app.log:4  2026-06-09 10:00:03 ERROR [order]  payment   failed",  # trace 前缀 + 空白差异
    "ERROR [order] … order=1001",                                    # 省略号
    "ERROR [order] payment failed order=1001\nKeyError: 'order_id'",  # 跨行
])
def test_matching_excerpts_are_verified(sample_log: Path, excerpt: str) -> None:
    item = _check(sample_log, source="app.log", start=4, end=7, excerpt=excerpt)
    assert item["status"] == "verified", item


def test_off_by_a_few_lines_is_shifted_with_actual_range(sample_log: Path) -> None:
    item = _check(sample_log, source="app.log", start=2, end=2, excerpt="payment failed order=1001")
    assert item["status"] == "shifted"
    assert (item["actual_start"], item["actual_end"]) == (4, 4)
    assert "第 4-4 行" in item["note"]


def test_fabricated_excerpt_is_mismatch(sample_log: Path) -> None:
    item = _check(sample_log, source="app.log", start=4, end=4, excerpt="NullPointerException at Router.select")
    assert item["status"] == "mismatch"
    assert "NullPointerException" in item["note"]


def test_line_beyond_eof_is_mismatch(sample_log: Path) -> None:
    item = _check(sample_log, source="app.log", start=500, end=501, excerpt="payment failed")
    assert item["status"] == "mismatch" and "超出文件范围" in item["note"]


def test_unknown_source_is_unresolved(sample_log: Path) -> None:
    item = _check(sample_log, source="other.log", start=1, end=1, excerpt="service started")
    assert item["status"] == "unresolved"


@pytest.mark.parametrize("source", ["app.log", "app.log:4", "APP_ABS"])
def test_log_source_variants_resolve(sample_log: Path, source: str) -> None:
    source = str(sample_log) if source == "APP_ABS" else source
    item = _check(sample_log, source=source, start=4, end=4, excerpt="payment failed order=1001")
    assert item["status"] == "verified"


def test_excerpt_must_match_redacted_text(tmp_path: Path) -> None:
    log = tmp_path / "sms.log"
    log.write_text("2026-06-09 10:00:00 ERROR send failed phone=13812345678 ip=10.2.3.4\n", encoding="utf-8")
    shown = redact.redact_log("send failed phone=13812345678 ip=10.2.3.4")
    resolver = SourceResolver([str(log)], [])
    assert check_one(resolver, _ev("sms.log", 1, 1, shown), 1, 1).status == "verified"
    # 模型不可能看到原文手机号；摘录里出现原文说明不是来自工具输出
    assert check_one(resolver, _ev("sms.log", 1, 1, "phone=13812345678"), 1, 1).status == "mismatch"
    redact.set_enabled(False)
    assert check_one(resolver, _ev("sms.log", 1, 1, "phone=13812345678"), 1, 1).status == "verified"


def test_truncated_long_line_excerpt_is_verified(tmp_path: Path) -> None:
    log = tmp_path / "big.log"
    log.write_text("2026-06-09 10:00:00 ERROR " + "x" * 3000 + " route=gateway-b\n", encoding="utf-8")
    excerpt = "1: 2026-06-09 10:00:00 ERROR xxxxxxxx …(本行共 3043 字，已截断)"
    assert check_one(SourceResolver([str(log)], []), _ev("big.log", 1, 1, excerpt), 1, 1).status == "verified"


def test_gzip_logs_are_checked(tmp_path: Path) -> None:
    path = tmp_path / "app.log.gz"
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write("2026-06-09 10:00:00 INFO start\n2026-06-09 10:00:01 ERROR boom happened\n")
    item = check_one(SourceResolver([str(path)], []), _ev("app.log.gz", 2, 2, "ERROR boom happened"), 1, 1)
    assert item.status == "verified"


@pytest.mark.parametrize("source", ["app/order.py", "repo/app/order.py", "app\\order.py"])
def test_code_sources_resolve_relative_to_code_dir(sample_log: Path, code_repo: Path, source: str) -> None:
    item = _check(sample_log, [code_repo], source=source, start=3, end=3, excerpt="3 | return order['order_id']")
    assert item["status"] == "verified", item


def test_code_source_cannot_escape_code_dir(sample_log: Path, code_repo: Path) -> None:
    (code_repo.parent / "secret.py").write_text("token = 1\n", encoding="utf-8")
    item = _check(sample_log, [code_repo], source="../secret.py", start=1, end=1, excerpt="token = 1")
    assert item["status"] == "unresolved"


# ---------------------------------------------------------------------------
# 整份报告
# ---------------------------------------------------------------------------


def _analysis_with(*evidence: dict) -> dict:
    data = analysis_data()
    data["issues"][0]["evidence"] = list(evidence)
    return data


def test_check_analysis_overall_status(sample_log: Path) -> None:
    good = _ev("app.log", 4, 4, "payment failed order=1001")
    bad = _ev("app.log", 4, 4, "made up line")
    unknown = _ev("nope.log", 1, 1, "whatever")
    logs = [str(sample_log)]
    assert check_analysis(_analysis_with(good), logs, [])["status"] == "verified"
    assert check_analysis(_analysis_with(good, bad), logs, [])["status"] == "mismatch"
    assert check_analysis(_analysis_with(bad), logs, [])["status"] == "failed"
    assert check_analysis(_analysis_with(unknown), logs, [])["status"] == "unverifiable"
    assert check_analysis(_analysis_with(good, unknown), logs, [])["status"] == "incomplete"
    assert check_analysis(analysis_data("clear"), logs, []) is None
    check = check_analysis(_analysis_with(good, bad, unknown), logs, [])
    assert summary_line(check) == "1/3 条与原文一致，1 条与原文不符，1 条无法核对"


def test_markdown_marks_each_evidence(sample_log: Path) -> None:
    data = _analysis_with(_ev("app.log", 4, 4, "payment failed order=1001"), _ev("app.log", 4, 4, "made up"))
    result = TurnResult(report=report_text(data))
    result.evidence_check = check_analysis(result.analysis, [str(sample_log)], [])
    payload = build_payload(result, question="q", logs=[str(sample_log)], code=[], model="m")
    assert payload["evidence_check"]["mismatch"] == 1
    body = report_body(payload, "ticket")
    assert "app.log:4-4（✓ 已核对原文）" in body
    assert "✗ 与原文不符" in body
    assert "- 证据核对：1/2 条与原文一致，1 条与原文不符" in to_markdown(payload)


def test_old_payloads_without_check_still_render() -> None:
    result = TurnResult(report=report_text())
    payload = build_payload(result, question="q", logs=[], code=[], model="m")
    payload.pop("evidence_check")
    body = report_body(payload, "ticket")
    assert "app.log:4-7\n" in body
    assert "已核对原文" not in body and "与原文不符" not in body


# ---------------------------------------------------------------------------
# 端到端：analyze 输出与 --fail-on
# ---------------------------------------------------------------------------


def _run(monkeypatch: pytest.MonkeyPatch, sample_log: Path, data: dict, *args: str):
    from log_agent import agent as agent_module

    model = ScriptedChatModel(script=[AIMessage(content=report_text(data))])
    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(model=model))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    return CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), *args])


def test_analyze_reports_verified_evidence(monkeypatch: pytest.MonkeyPatch, sample_log: Path, tmp_path: Path) -> None:
    out = tmp_path / "r.json"
    result = _run(monkeypatch, sample_log, analysis_data(), "-o", str(out), "--fail-on", "high")
    assert result.exit_code == 3, result.output
    assert "证据核对：1/1 条与原文一致" in result.output
    assert json.loads(out.read_text(encoding="utf-8"))["evidence_check"]["status"] == "verified"


def test_fail_on_undecided_when_all_evidence_fails(monkeypatch: pytest.MonkeyPatch, sample_log: Path) -> None:
    data = _analysis_with(_ev("app.log", 4, 4, "NullPointerException in Router"))
    result = _run(monkeypatch, sample_log, data, "--fail-on", "low")
    assert result.exit_code == 4, result.output
    assert "与原文不符" in result.output


def test_fail_on_downgrades_confidence_on_partial_mismatch(monkeypatch: pytest.MonkeyPatch, sample_log: Path) -> None:
    data = _analysis_with(_ev("app.log", 4, 4, "payment failed order=1001"), _ev("app.log", 9, 9, "invented"))
    assert _run(monkeypatch, sample_log, data, "--fail-on", "high").exit_code == 0   # 高 → 按中判断
    assert _run(monkeypatch, sample_log, data, "--fail-on", "medium").exit_code == 3
