from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from log_agent import cli
from log_agent.export import build_payload, to_markdown, write_report
from log_agent.render import TurnResult
from log_agent.report import extract_analysis, visible_report

from .conftest import ScriptedChatModel


def analysis_data(assessment="finding", confidence="high"):
    return {
        "assessment": assessment, "confidence": confidence, "conclusion": "支付请求失败",
        "impact": "已观察到一条请求失败，整体影响待确认", "next_steps": ["确认入参"],
        "issues": [] if assessment == "clear" else [{
            "title": "缺少 order_id", "symptoms": "支付返回 500", "impact": "请求 abc",
            "evidence": [{"source": "app.log", "line_start": 4, "line_end": 7,
                          "excerpt": "ERROR [order] payment failed order=1001\nKeyError: 'order_id'"}],
            "root_cause_hypotheses": [{"explanation": "入参缺少字段", "confidence": "medium",
                                       "reasoning": "日志显示 KeyError，还需核对请求参数"}],
            "open_questions": ["上游是否遗漏"], "recommendations": ["增加入参校验"],
            "reproduction_conditions": ["待确认：缺少 order_id 的请求"],
            "verification_steps": ["验证缺失字段返回明确错误"],
        }], "open_questions": ["确认整体请求量"],
    }


def report_text(data=None, body="完整推导：请求 → 校验 → 异常"):
    return body + "\n\n```log-agent-report\n" + json.dumps(data or analysis_data(), ensure_ascii=False) + "\n```"


@pytest.mark.parametrize("assessment, expected", [("finding", True), ("clear", False), ("unknown", None)])
def test_explicit_assessment_overrides_wording(assessment, expected):
    result = TurnResult(report=report_text(analysis_data(assessment), "一句话结论：未发现异常（可信度：低）"))
    assert result.finding is expected
    assert result.confidence == "高"
    assert result.structured_status == "valid"
    assert "log-agent-report" not in result.report


@pytest.mark.parametrize("change", [
    lambda d: d.update(assessment="invalid"),
    lambda d: d.update(issues=[]),
    lambda d: d["issues"][0]["evidence"][0].update(line_start=0),
    lambda d: d["issues"][0]["evidence"][0].update(line_start="7"),
    lambda d: d["issues"][0]["evidence"][0].update(line_end=1),
    lambda d: d.update(assessment="clear"),
    lambda d: d.pop("impact"),
])
def test_invalid_data_never_drives_automation(change):
    data = analysis_data()
    change(data)
    result = TurnResult(report=report_text(data), summary="异常", confidence="高")
    assert result.analysis is None and result.finding is None
    assert result.structured_status == "invalid"
    assert "log-agent-report" in result.report  # Preserve failed output for inspection.


def test_missing_truncated_duplicate_and_interrupted_reports():
    assert TurnResult(summary="未发现异常").finding is None
    text = report_text()
    for bad in (text[:-4], text + "\n" + text, text + "\ntrailing"):
        assert extract_analysis(bad)[2] == "invalid"
    assert TurnResult(report=text, interrupted=True).finding is None
    assert TurnResult(report=text, error="failed").finding is None
    assert visible_report(text[:-4]) == "完整推导：请求 → 校验 → 异常"
    ordinary = '```json\n{"status": "ok"}\n```'
    assert extract_analysis(ordinary) == (ordinary, None, "missing")


def test_views_keep_context_and_structured_json(tmp_path):
    settings = {"since": "10:00", "until": "11:00", "timezone": "+08:00", "baseline": "09:00~09:30"}
    payload = build_payload(TurnResult(report=report_text()), question="为什么？", logs=["/logs/app.log"],
                            code=["/repo"], model="test", settings=settings)
    settings["since"] = "12:00"
    for view in ("brief", "detailed", "ticket"):
        md = to_markdown(payload, view)
        assert "10:00" in md and "+08:00" in md and "/logs/app.log" in md and "/repo" in md
        assert "确认入参" in md
        assert ("完整推导" in md) == (view == "detailed")
        assert ("验证缺失字段" in md) == (view != "brief")
        path = write_report(tmp_path / f"{view}.json", payload, "json", view)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["analysis"] == payload["analysis"]
        assert saved["generated_at"] == payload["generated_at"]
        assert saved["schema_version"] == 2 and saved["view"] == view
    assert "结构化数据不可用" in to_markdown({**payload, "analysis": None}, "ticket")


def test_analyze_structured_export_and_fail_on(tmp_path, sample_log, monkeypatch):
    from log_agent import agent as agent_module

    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(
        model=ScriptedChatModel(script=[AIMessage(content=report_text())])))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    path = tmp_path / "ticket.json"
    out = CliRunner().invoke(cli.app, ["analyze", "-l", str(sample_log), "-o", str(path),
                                      "--view", "ticket", "--fail-on", "medium", "--timezone", "+08:00"])
    assert out.exit_code == 3, out.output
    assert '"assessment"' not in out.output
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["analysis"]["issues"][0]["evidence"][0]["line_start"] == 4
    assert saved["settings"]["timezone"] == "+08:00"
    assert "验证方法" in saved["rendered_report"]


def test_chat_restores_structured_snapshot_and_exports_views(tmp_path, sample_log, monkeypatch):
    from log_agent import agent as agent_module

    real_build = agent_module.build_agent
    monkeypatch.setattr(agent_module, "build_agent", lambda **kw: real_build(
        model=ScriptedChatModel(script=[AIMessage(content=report_text())]), checkpointer=kw.get("checkpointer")))
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    args = ["chat", "--db", str(tmp_path / "s.db"), "-s", "handoff"]
    runner = CliRunner()
    first = runner.invoke(cli.app, [*args, "-l", str(sample_log), "--since", "10:00",
                                    "--base-url", "https://private.example/v1"], input="分析\nexit\n")
    assert first.exit_code == 0, first.output
    ticket, brief = tmp_path / "ticket.json", tmp_path / "brief.md"
    resumed = runner.invoke(cli.app, args, input=f"/window off\n/save-ticket {ticket}\n/save-brief {brief}\nexit\n")
    assert resumed.exit_code == 0, resumed.output
    saved = json.loads(ticket.read_text(encoding="utf-8"))
    assert saved["analysis"]["assessment"] == "finding"
    assert saved["settings"]["since"] == "10:00"
    assert "base_url" not in saved["settings"]
    assert "验证方法" in saved["rendered_report"]
    assert "完整推导" not in brief.read_text(encoding="utf-8")


def test_old_snapshot_does_not_export_wording_based_finding(tmp_path):
    payload = build_payload(TurnResult(report="未发现异常"), question="q", logs=[], code=[], model="test")
    payload.pop("schema_version")
    payload.pop("analysis")
    payload.pop("structured_status")
    payload["finding"] = False
    path = write_report(tmp_path / "old.json", payload, "json", "ticket")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["finding"] is None and saved["structured_status"] == "missing"
    assert payload["finding"] is False
    assert saved["generated_at"] == payload["generated_at"]


def test_report_filters_connection_settings_without_mutating_metadata(tmp_path):
    settings = {"base_url": "https://user:secret@private.example/v1", "timezone": "UTC"}
    payload = build_payload(TurnResult(report="done"), question="q", logs=[], code=[], model="test", settings=settings)
    assert payload["settings"] == {"timezone": "UTC"}
    assert "base_url" in settings
    # Existing snapshots must also be safe when exported by chat /save.
    payload["settings"] = settings
    path = write_report(tmp_path / "legacy.json", payload, "json")
    assert json.loads(path.read_text(encoding="utf-8"))["settings"] == {"timezone": "UTC"}
    assert payload["settings"] == settings and "base_url" in payload["settings"]
