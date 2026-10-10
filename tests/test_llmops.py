"""成本 / 缓存统计、结构化抽取、证据修正、历史案例、反馈与评测。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from log_agent import config, pricing
from log_agent.cases import find_similar, hint_text
from log_agent.evals import EvalCase, case_from_turn, load_case, root_cause_hit, score_case
from log_agent.postprocess import EvidenceFix, EvidenceFixes, PostProcessor, repair_evidence
from log_agent.render import StreamRenderer
from log_agent.report import Analysis
from log_agent.sessions import SessionStore
from log_agent.usage import cache_hit_rate, usage_from_metadata

JAVA_LOG = [
    "2026-10-10 13:59:58 INFO  [exec-1] c.s.OrderController - POST /api/orders 200 40ms",
    "2026-10-10 14:00:00 ERROR [exec-5] c.s.OrderController - POST /api/orders 500 12ms couponCode=SPRING2026",
    'java.lang.NullPointerException: Cannot invoke "com.shop.Coupon.getAmount()" because "coupon" is null',
    "\tat com.shop.OrderService.placeOrder(OrderService.java:17)",
    "\tat com.shop.OrderController.create(OrderController.java:17)",
    "2026-10-10 14:00:03 INFO  [exec-2] c.s.OrderController - POST /api/orders 200 55ms",
]


def _analysis(evidence: list[dict], assessment: str = "finding") -> dict:
    issue = {
        "title": "下单空指针", "symptoms": "500", "impact": "待确认", "evidence": evidence,
        "root_cause_hypotheses": [{"explanation": "优惠券过期返回 null", "confidence": "high",
                                   "reasoning": "OrderService.java:17 未判空"}],
        "open_questions": [], "recommendations": [], "reproduction_conditions": [], "verification_steps": [],
    }
    return Analysis.model_validate({
        "assessment": assessment, "confidence": "high", "conclusion": "NullPointerException：优惠券过期未判空",
        "impact": "待确认", "next_steps": [], "issues": [issue] if assessment == "finding" else [], "open_questions": [],
    }).model_dump()


class _Structured:
    def __init__(self, owner: _FakeModel, schema):
        self.owner, self.schema = owner, schema

    def invoke(self, messages):
        self.owner.prompts.append(messages)
        reply = self.owner.replies.pop(0)
        raw = AIMessage(content="", usage_metadata={
            "input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
            "input_token_details": {"cache_read": 20},
        }, response_metadata={"model_name": "fake-post"})
        return {"raw": raw, "parsed": reply if reply is None else self.schema.model_validate(reply), "parsing_error": None}


class _FakeModel:
    def __init__(self, replies: list[Any]):
        self.replies = list(replies)
        self.prompts: list = []

    def with_structured_output(self, schema, method="json_schema", include_raw=False):
        return _Structured(self, schema)


# ---- 用量与计价 ---------------------------------------------------------------


def test_usage_includes_cache_and_reasoning_tokens():
    usage = usage_from_metadata({
        "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
        "input_token_details": {"cache_read": 600, "cache_creation": 100},
        "output_token_details": {"reasoning": 50},
    })
    assert usage == {"input": 1000, "output": 200, "total": 1200, "cache_read": 600, "cache_write": 100, "reasoning": 50}
    assert cache_hit_rate(usage) == 0.6


def test_cost_charges_cached_input_at_cache_price():
    price = pricing.Price(input=2.0, output=6.0, cache_read=0.5)
    usage = {"input": 1_000_000, "output": 100_000, "cache_read": 400_000}
    # 60 万普通输入 + 40 万缓存命中 + 10 万输出
    assert pricing.cost_of(usage, price) == pytest.approx(0.6 * 2.0 + 0.4 * 0.5 + 0.1 * 6.0)


def test_configured_price_wins_and_unknown_model_is_none(monkeypatch):
    loaded = config.LoadedConfig()
    loaded.shared["pricing"] = {"my-model": {"input": 1.0, "output": 2.0}}
    config.set_loaded(loaded)
    assert pricing.price_for("openai:my-model").source == "config"
    cost = pricing.turn_cost({"my-model": {"input": 1_000_000, "output": 0, "total": 1_000_000},
                              "mystery": {"input": 10, "output": 0, "total": 10}}, "my-model")
    # 未知模型按会话模型计价
    assert cost["usd"] == pytest.approx(1.00001) and cost["complete"]
    assert pricing.turn_cost({"": {"input": 5, "total": 5}}, "nobody-knows")["usd"] is None


def test_pricing_section_in_config_file(tmp_path, monkeypatch):
    project = tmp_path / "proj"
    project.mkdir()
    (project / ".log-agent.toml").write_text(
        'structured = "extract"\n[pricing."x-ai/grok-4.7"]\ninput = 2.0\noutput = 6.0\ncache_read = 0.5\n',
        encoding="utf-8",
    )
    loaded = config.load_config(project)
    assert loaded.shared["pricing"]["x-ai/grok-4.7"]["cache_read"] == 0.5 and not loaded.warnings
    monkeypatch.delenv("LOG_AGENT_STRUCTURED")
    config.apply_to_environment(loaded.for_command("analyze"))
    import os

    assert os.environ["LOG_AGENT_STRUCTURED"] == "extract"


# ---- 结构化抽取 ---------------------------------------------------------------


class _Agent:
    def __init__(self, report: str, post: PostProcessor):
        self.report, self.log_agent_post = report, post

    def stream_events(self, payload, config=None, version="v3"):
        report = self.report

        class _Stream:
            output = {"messages": [AIMessage(content=report)]}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def interleave(self, *names):
                return iter(())

        return _Stream()


def test_missing_appendix_is_extracted_and_costed():
    report = "一句话结论：优惠券过期未判空（可信度：高）\n\n### 关键证据\n正文"
    model = _FakeModel([_analysis([], assessment="unknown")])
    result = StreamRenderer(verbose=False).run(_Agent(report, PostProcessor(model, "auto")), {"messages": []})
    assert result.structured_status == "valid" and result.structured_source == "extracted"
    assert result.analysis["assessment"] == "unknown"
    assert result.usage["total"] == 60 and result.usage["cache_read"] == 20
    assert result.usage_by_model["fake-post"]["input"] == 50
    assert result.llm_calls[-1]["purpose"] == "structure"


def test_inline_mode_never_extracts():
    model = _FakeModel([])
    result = StreamRenderer(verbose=False).run(_Agent("一句话结论：x（可信度：高）", PostProcessor(model, "inline")),
                                               {"messages": []})
    assert result.structured_status == "missing" and model.prompts == []


# ---- 证据修正 -----------------------------------------------------------------


@pytest.fixture
def java_log(tmp_path: Path) -> Path:
    path = tmp_path / "app.log"
    path.write_text("\n".join(["2026-10-10 13:00:00 INFO filler"] * 40 + JAVA_LOG) + "\n", encoding="utf-8")
    return path


def _check(analysis, log):
    from log_agent.evidence import check_analysis

    return check_analysis(analysis, [str(log)], [])


def test_repair_fixes_shifted_and_relocates_without_model(java_log):
    shifted = {"source": "app.log", "line_start": 41, "line_end": 41, "excerpt": "POST /api/orders 500 12ms couponCode=SPRING2026"}
    far = {"source": "app.log", "line_start": 5, "line_end": 5, "excerpt": "at com.shop.OrderService.placeOrder(OrderService.java:17)"}
    analysis = _analysis([shifted, far])
    check = _check(analysis, java_log)
    assert check["shifted"] == 1 and check["mismatch"] == 1
    fixed, new_check, record, calls = repair_evidence(analysis, check, [str(java_log)], [], model=None)
    assert record["adopted"] and record["shifted_fixed"] == 1 and record["relocated"] == 1 and calls == []
    assert new_check["verified"] == 2
    assert fixed["issues"][0]["evidence"][1]["line_start"] == 44


def test_repair_asks_model_for_unlocatable_excerpt_and_drops(java_log):
    invented = {"source": "app.log", "line_start": 42, "line_end": 42, "excerpt": "coupon SPRING2026 expired at 14:00"}
    wrong = {"source": "app.log", "line_start": 43, "line_end": 43, "excerpt": "NullPointerException in getAmount totally rewritten"}
    analysis = _analysis([invented, wrong])
    check = _check(analysis, java_log)
    assert check["mismatch"] == 2
    model = _FakeModel([EvidenceFixes(fixes=[
        EvidenceFix(issue=1, index=1, action="drop"),
        EvidenceFix(issue=1, index=2, action="fix", line_start=43, line_end=43,
                    excerpt='java.lang.NullPointerException: Cannot invoke "com.shop.Coupon.getAmount()" because "coupon" is null'),
    ]).model_dump()])
    fixed, new_check, record, calls = repair_evidence(analysis, check, [str(java_log)], [], model=model)
    assert record["adopted"] and record["dropped"] == 1 and record["model_fixed"] == 1
    assert new_check["verified"] == 1 and new_check["mismatch"] == 0
    assert len(fixed["issues"][0]["evidence"]) == 1
    # 回传给模型的是所引行附近的真实原文
    assert "OrderService.java:17" in model.prompts[0][1]["content"]
    assert calls[0]["purpose"] == "repair"


def test_repair_not_adopted_when_model_makes_it_worse(java_log):
    wrong = {"source": "app.log", "line_start": 43, "line_end": 43, "excerpt": "made up line that is nowhere"}
    analysis = _analysis([wrong])
    check = _check(analysis, java_log)
    model = _FakeModel([EvidenceFixes(fixes=[EvidenceFix(issue=1, index=1, action="fix", line_start=43, line_end=43,
                                                         excerpt="still made up")]).model_dump()])
    fixed, new_check, record, _ = repair_evidence(analysis, check, [str(java_log)], [], model=model)
    assert not record["adopted"] and fixed is analysis and new_check is check


# ---- 历史案例与反馈 -----------------------------------------------------------


def _payload(log: Path, assessment: str = "finding") -> dict:
    return {"status": "ok", "question": "为什么 500", "logs": [str(log)], "code": [], "model": "m",
            "summary": "空指针", "analysis": _analysis([
                {"source": "app.log", "line_start": 44, "line_end": 44,
                 "excerpt": "at com.shop.OrderService.placeOrder(OrderService.java:17)"}], assessment)}


def test_case_index_and_retrieval_respects_feedback(java_log, tmp_path):
    conn = sqlite3.connect(":memory:")
    store = SessionStore(conn)
    store.touch("old", [str(java_log)], [], "m")
    store.record_turn("old", "为什么 500", 10, _payload(java_log))
    rows = conn.execute("SELECT signatures FROM log_agent_cases").fetchall()
    assert rows and "OrderService.java:placeOrder" in rows[0][0]

    other = tmp_path / "new.log"
    other.write_text("\n".join(JAVA_LOG) + "\n", encoding="utf-8")
    cases = find_similar(conn, [str(other)], [])
    assert [c["session"] for c in cases] == ["old"]
    assert "NullPointerException @ OrderService.java:placeOrder" in cases[0]["matched"]
    assert "历史相似案例" in hint_text(cases) and "优惠券过期未判空" in hint_text(cases)
    # 当前会话自己不算历史案例
    assert find_similar(conn, [str(other)], [], exclude="old") == []

    store.set_feedback("old", 1, "wrong", "其实是库存服务超时")
    cases = find_similar(conn, [str(other)], [])
    assert cases[0]["correction"] == "其实是库存服务超时" and "用户纠正" in hint_text(cases)
    store.set_feedback("old", 1, "down")
    assert find_similar(conn, [str(other)], []) == []
    store.clear_feedback("old", 1)
    assert store.feedback("old") == {}


def test_clear_turns_are_not_indexed(java_log):
    conn = sqlite3.connect(":memory:")
    store = SessionStore(conn)
    store.touch("s", [str(java_log)], [], "m")
    store.record_turn("s", "q", 1, _payload(java_log, assessment="clear"))
    assert conn.execute("SELECT COUNT(*) FROM log_agent_cases").fetchone()[0] == 0


def test_feedback_validation_and_session_costs(java_log):
    store = SessionStore(sqlite3.connect(":memory:"))
    store.touch("s", [str(java_log)], [], "m")
    store.record_turn("s", "q", 1, {**_payload(java_log), "cost": {"usd": 0.12}})
    store.record_turn("s", "q2", 1, {**_payload(java_log), "question": "q2", "cost": {"usd": None}})
    assert store.session_costs() == {"s": 0.12}
    with pytest.raises(ValueError):
        store.set_feedback("s", 1, "meh")
    assert not store.set_feedback("s", 9, "up")
    assert store.set_feedback("s", 2, "up", "  很准  ")
    assert store.feedback("s")[2]["comment"] == "很准"
    assert store.feedback_summary()[0]["question"] == "q2"


# ---- 评测 ---------------------------------------------------------------------


def test_root_cause_hit_from_evidence_or_text():
    payload = {"analysis": _analysis([{"source": "src/OrderService.java", "line_start": 15, "line_end": 18,
                                       "excerpt": "x"}]), "report": ""}
    assert root_cause_hit(["OrderService.java:17"], payload)
    assert not root_cause_hit(["CouponRepository.java:24"], payload)
    assert root_cause_hit(["CouponRepository.java:24"], {"analysis": None, "report": "见 CouponRepository.java:23-25"})
    assert root_cause_hit([], payload) is None


def test_score_case_weights():
    case = EvalCase("c", ".", "q", ["a.log"], [], {"assessment": "finding", "root_cause": ["OrderService.java:17"],
                                                  "keywords": ["SPRING2026", "nothing"]})
    payload = {"structured_status": "valid", "report": "SPRING2026 过期",
               "analysis": _analysis([{"source": "OrderService.java", "line_start": 17, "line_end": 17, "excerpt": "x"}]),
               "evidence_check": {"total": 2, "verified": 1, "shifted": 0}}
    score = score_case(case, payload)
    assert score["score"] == 10 + 30 + 40 + 10 and score["keyword_recall"] == 0.5
    clear = EvalCase("c", ".", "q", ["a.log"], [], {"assessment": "clear"})
    assert score_case(clear, {"structured_status": "valid", "analysis": _analysis([], "clear")})["score"] == 100


def test_export_turn_as_case_round_trip(tmp_path):
    payload = {"question": "为什么 500", "logs": [str(tmp_path / "app.log")], "code": [str(tmp_path)],
               "settings": {"timezone": "+08:00"}, "model": "m", "generated_at": "now",
               "analysis": _analysis([{"source": "src/OrderService.java", "line_start": 17, "line_end": 17, "excerpt": "x"},
                                      {"source": "app.log", "line_start": 3, "line_end": 3, "excerpt": "y"}])}
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "case.toml").write_text(case_from_turn(payload, {"rating": "up"}), encoding="utf-8")
    case = load_case(case_dir)
    assert case.expected["assessment"] == "finding" and case.expected["root_cause"] == ["OrderService.java:17"]
    assert case.settings == {"timezone": "+08:00"}

    (case_dir / "case.toml").write_text(case_from_turn(payload, {"rating": "wrong", "comment": "是库存超时"}),
                                        encoding="utf-8")
    case = load_case(case_dir)
    assert "assessment" not in case.expected and case.expected["notes"] == "是库存超时"


@pytest.mark.skipif(not hasattr(__import__("signal"), "SIGALRM"), reason="需要 SIGALRM")
def test_eval_run_case_times_out_instead_of_hanging(monkeypatch):
    import time as _time

    from log_agent import agent as agent_mod
    from log_agent.evals import EvalCase, RunSpec, run_case

    monkeypatch.setattr(agent_mod, "build_agent", lambda **_: _time.sleep(5))
    case = EvalCase(name="slow", directory=".", question="q", logs=[], code=[], expected={})
    started = _time.perf_counter()
    record = run_case(RunSpec(case=case, model="m", structured="inline", timeout=1))
    assert _time.perf_counter() - started < 3
    assert record["error"].startswith("TimeoutError")
