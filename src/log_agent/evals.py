"""用真实模型回答质量的评测：标准案例 + 打分 + 多模型 / 多结构化方式对比。

案例目录结构（`evals/cases/<名字>/case.toml`，logs / code 写相对案例目录的路径或绝对路径）：

    question = "下单接口为什么开始报 500？"
    logs = ["logs/app.log"]
    code = ["code"]
    timezone = "+08:00"          # 可选，另有 since / until / baseline / encoding

    [expected]
    assessment = "finding"       # finding / clear / unknown
    root_cause = ["OrderService.java:16"]   # 根因所在 文件:行号，命中任意一个即可（±3 行容差）
    keywords = ["SPRING2026"]    # 报告里应该出现的关键事实
    notes = "优惠券过期后 findValid 返回 null，placeOrder 未判空"

打分项（单案例满分 100）：判定正确 30、根因命中 40、证据回查通过率 20、结构化报告有效 10。
没有给出 root_cause（例如 clear 案例）时，这 40 分按判定是否正确计。
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LINE_TOLERANCE = 3
_SETTING_KEYS = ("timezone", "since", "until", "baseline", "encoding")


@dataclass
class EvalCase:
    name: str
    directory: str
    question: str
    logs: list[str]
    code: list[str]
    expected: dict[str, Any]
    settings: dict[str, str] = field(default_factory=dict)


def _resolve(base: Path, items: Iterable[str]) -> list[str]:
    return [str(p if (p := Path(os.path.expandvars(i)).expanduser()).is_absolute() else (base / p).resolve())
            for i in items]


def load_case(path: Path) -> EvalCase:
    file = path / "case.toml" if path.is_dir() else path
    data = tomllib.loads(file.read_text(encoding="utf-8"))
    base = file.parent
    question = str(data.get("question") or "").strip()
    if not question or not data.get("logs"):
        raise ValueError(f"{file}: 必须填写 question 和 logs")
    expected = dict(data.get("expected") or {})
    if expected.get("assessment") not in (None, "finding", "clear", "unknown"):
        raise ValueError(f"{file}: expected.assessment 只能是 finding / clear / unknown")
    return EvalCase(
        name=str(data.get("name") or base.name), directory=str(base), question=question,
        logs=_resolve(base, data["logs"]), code=_resolve(base, data.get("code") or []), expected=expected,
        settings={k: str(data[k]) for k in _SETTING_KEYS if data.get(k)},
    )


def load_cases(root: Path, names: Iterable[str] | None = None) -> list[EvalCase]:
    wanted = set(names or [])
    if (root / "case.toml").is_file():
        cases = [load_case(root)]
    else:
        cases = [load_case(p) for p in sorted(root.iterdir()) if (p / "case.toml").is_file()]
    return [c for c in cases if not wanted or c.name in wanted]


# ---------------------------------------------------------------------------
# 打分
# ---------------------------------------------------------------------------

_REF = re.compile(r"(?P<file>[\w./\\-]+\.[A-Za-z0-9]+):(?P<start>\d+)(?:\s*[-–~]\s*(?P<end>\d+))?")


def _parse_ref(ref: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"\s*(.+?):(\d+)\s*", ref)
    if not match:
        return None
    return match[1].replace("\\", "/").rsplit("/", 1)[-1], int(match[2])


def _covers(file: str, line: int, source: str, start: int, end: int) -> bool:
    name = source.replace("\\", "/").rsplit("/", 1)[-1]
    return name == file and start - LINE_TOLERANCE <= line <= max(start, end) + LINE_TOLERANCE


def root_cause_hit(expected_refs: list[str], payload: dict[str, Any]) -> bool | None:
    refs = [r for r in (_parse_ref(x) for x in expected_refs) if r]
    if not refs:
        return None
    analysis = payload.get("analysis") or {}
    spans: list[tuple[str, int, int]] = []
    texts = [payload.get("report") or "", analysis.get("conclusion") or ""]
    for issue in analysis.get("issues") or []:
        for evidence in issue.get("evidence") or []:
            spans.append((str(evidence.get("source") or ""), int(evidence.get("line_start") or 0),
                          int(evidence.get("line_end") or 0)))
        texts += [f"{h.get('explanation', '')} {h.get('reasoning', '')}" for h in issue.get("root_cause_hypotheses") or []]
        texts += issue.get("recommendations") or []
    for match in _REF.finditer("\n".join(texts)):
        start = int(match["start"])
        spans.append((match["file"], start, int(match["end"] or start)))
    return any(_covers(file, line, *span) for file, line in refs for span in spans)


def keyword_recall(keywords: list[str], payload: dict[str, Any]) -> float | None:
    if not keywords:
        return None
    haystack = (payload.get("report") or "") + json.dumps(payload.get("analysis") or {}, ensure_ascii=False)
    haystack = haystack.casefold()
    return sum(1 for k in keywords if str(k).casefold() in haystack) / len(keywords)


def score_case(case: EvalCase, payload: dict[str, Any]) -> dict[str, Any]:
    analysis = payload.get("analysis") or {}
    expected = case.expected
    structured = payload.get("structured_status") == "valid"
    assessment_ok = (analysis.get("assessment") == expected["assessment"]) if expected.get("assessment") else None
    hit = root_cause_hit(list(expected.get("root_cause") or []), payload)
    check = payload.get("evidence_check") or {}
    evidence_total = int(check.get("total") or 0)
    evidence_ok = int(check.get("verified") or 0) + int(check.get("shifted") or 0)
    evidence_rate = evidence_ok / evidence_total if evidence_total else None
    points = 10.0 * structured
    points += 30.0 * bool(assessment_ok) if assessment_ok is not None else 30.0 * structured
    points += 40.0 * bool(hit) if hit is not None else 40.0 * bool(assessment_ok)
    points += 20.0 * (evidence_rate if evidence_rate is not None else (1.0 if expected.get("assessment") == "clear" and structured else 0.0))
    return {
        "score": round(points, 1),
        "structured_ok": structured,
        "structured_source": payload.get("structured_source"),
        "assessment": analysis.get("assessment"),
        "assessment_ok": assessment_ok,
        "root_cause_hit": hit,
        "keyword_recall": keyword_recall(list(expected.get("keywords") or []), payload),
        "evidence_total": evidence_total,
        "evidence_ok": evidence_ok,
        "evidence_rate": evidence_rate,
        "repair": payload.get("evidence_repair"),
    }


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


@dataclass
class RunSpec:
    case: EvalCase
    model: str
    structured: str
    base_url: str | None = None
    max_steps: int = 80
    budget: str | None = None


def run_case(spec: RunSpec) -> dict[str, Any]:
    """在当前进程里跑一个案例（eval 用独立进程并行调用它：时区 / 时间窗口等是进程级设置）。"""
    from . import redact
    from .budget import TokenBudget, parse_budget
    from .citations import CitationLinker
    from .cli_context import _build_context_message, _run_config
    from .compare import parse_range
    from .export import build_payload
    from .logfile import set_forced_encoding
    from .render import StreamRenderer
    from .term import console
    from .timefilter import set_default_timezone, set_default_window
    from .usage import cache_hit_rate

    case = spec.case
    started = time.perf_counter()
    record: dict[str, Any] = {"case": case.name, "model": spec.model, "structured": spec.structured}
    try:
        from .agent import build_agent

        set_default_timezone(case.settings.get("timezone") or "UTC")
        set_forced_encoding(case.settings.get("encoding") or None)
        set_default_window(case.settings.get("since") or None, case.settings.get("until") or None)
        redact.set_enabled(True)
        budget = TokenBudget(parse_budget(spec.budget)) if spec.budget else None
        agent = build_agent(model=spec.model, base_url=spec.base_url, skill_dirs=[], budget=budget,
                            structured=spec.structured)
        baseline = parse_range(case.settings["baseline"]) if case.settings.get("baseline") else None
        message = _build_context_message(case.logs, case.code, case.question, baseline)
        console.quiet = True
        result = StreamRenderer(verbose=False, linker=CitationLinker(case.logs, case.code, mode="off"),
                                budget=budget).run(
            agent, {"messages": [{"role": "user", "content": message}]}, config=_run_config(spec.max_steps),
        )
        payload = build_payload(result, question=case.question, logs=case.logs, code=case.code, model=spec.model,
                                settings={**case.settings, "base_url": spec.base_url})
    except Exception as exc:  # noqa: BLE001 — 单个案例失败记成错误，不中断整批评测
        record.update(error=f"{type(exc).__name__}: {exc}"[:500], seconds=round(time.perf_counter() - started, 1))
        return record
    usage = payload.get("usage") or {}
    post_calls = [c for c in payload.get("llm_calls") or [] if c.get("purpose")]
    record.update(
        status=payload["status"], error=payload.get("error"),
        seconds=payload.get("elapsed_seconds"), usage=usage, cache_hit=cache_hit_rate(usage),
        cost=(payload.get("cost") or {}).get("usd"), tools=len(payload.get("tool_calls") or []),
        llm_calls=len(payload.get("llm_calls") or []),
        post_calls=[{k: c.get(k) for k in ("purpose", "method", "seconds", "error", "model")} for c in post_calls],
        summary=payload.get("summary"), analysis=payload.get("analysis"),
        evidence_check={k: v for k, v in (payload.get("evidence_check") or {}).items() if k != "items"} or None,
        report=payload.get("report"),
        **score_case(case, payload),
    )
    return record


def _run_spec_in_worker(spec_dict: dict[str, Any]) -> dict[str, Any]:
    spec = RunSpec(case=EvalCase(**spec_dict.pop("case")), **spec_dict)
    return run_case(spec)


def run_eval(specs: list[RunSpec], jobs: int = 1, on_result=None) -> list[dict[str, Any]]:
    """jobs > 1 时每个案例在独立进程里跑；按完成顺序回调 on_result。"""
    results: list[dict[str, Any]] = []
    if jobs <= 1:
        for spec in specs:
            record = run_case(spec)
            results.append(record)
            if on_result:
                on_result(record)
        return results
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor, as_completed

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=jobs, mp_context=context) as pool:
        futures = {pool.submit(_run_spec_in_worker, {**asdict(spec), "case": asdict(spec.case)}): spec for spec in specs}
        for future in as_completed(futures):
            spec = futures[future]
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001
                record = {"case": spec.case.name, "model": spec.model, "structured": spec.structured,
                          "error": f"{type(exc).__name__}: {exc}"[:500]}
            results.append(record)
            if on_result:
                on_result(record)
    return results


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def _mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def summarize(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in results:
        groups.setdefault((record["model"], record["structured"]), []).append(record)
    rows = []
    for (model, structured), records in sorted(groups.items()):
        ok = [r for r in records if "score" in r]
        evidence_total = sum(r.get("evidence_total") or 0 for r in ok)
        hits = [r["root_cause_hit"] for r in ok if r.get("root_cause_hit") is not None]
        verdicts = [r["assessment_ok"] for r in ok if r.get("assessment_ok") is not None]
        costs = [r.get("cost") for r in ok]
        rows.append({
            "model": model,
            "structured": structured,
            "runs": len(records),
            "errors": sum(1 for r in records if r.get("error") or "score" not in r),
            "score": _mean([r["score"] for r in ok]),
            "structured_rate": _mean([float(r["structured_ok"]) for r in ok]),
            "extracted": sum(1 for r in ok if r.get("structured_source") == "extracted"),
            "assessment_acc": _mean([float(v) for v in verdicts]),
            "root_cause_rate": _mean([float(v) for v in hits]),
            "evidence_rate": (sum(r.get("evidence_ok") or 0 for r in ok) / evidence_total) if evidence_total else None,
            "repairs_adopted": sum(1 for r in ok if (r.get("repair") or {}).get("adopted")),
            "seconds": _mean([r.get("seconds") for r in ok]),
            "tokens": _mean([float((r.get("usage") or {}).get("total") or 0) for r in ok]),
            "cache_hit": _mean([r.get("cache_hit") for r in ok]),
            "cost": _mean(costs) if all(c is not None for c in costs) and costs else None,
            "cost_total": sum(c for c in costs if c is not None) if any(c is not None for c in costs) else None,
        })
    return rows


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"


def _num(value: float | None, fmt: str = "{:.1f}") -> str:
    return "—" if value is None else fmt.format(value)


def markdown_table(rows: list[dict[str, Any]]) -> str:
    head = ("| 模型 | 结构化方式 | 次数 | 得分 | 结构化有效 | 判定正确 | 根因命中 | 证据通过 | 自动修正 | "
            "平均耗时 | 平均 tokens | 缓存命中 | 平均费用 | 错误 |")
    lines = [head, "|" + "|".join(["---"] * 14) + "|"]
    for r in rows:
        structured = _pct(r["structured_rate"]) + (f"（补抽 {r['extracted']}）" if r["extracted"] else "")
        lines.append("| " + " | ".join([
            f"`{r['model']}`", r["structured"], str(r["runs"]), _num(r["score"]), structured,
            _pct(r["assessment_acc"]), _pct(r["root_cause_rate"]), _pct(r["evidence_rate"]), str(r["repairs_adopted"]),
            _num(r["seconds"], "{:.0f}s"), _num(r["tokens"], "{:,.0f}"), _pct(r["cache_hit"]),
            _num(r["cost"], "${:.4f}"), str(r["errors"]),
        ]) + " |")
    return "\n".join(lines)


def case_table(results: list[dict[str, Any]]) -> str:
    head = "| 案例 | 模型 | 结构化方式 | 得分 | 判定 | 根因命中 | 证据 | 耗时 | tokens | 费用 | 备注 |"
    lines = [head, "|" + "|".join(["---"] * 11) + "|"]
    for r in sorted(results, key=lambda r: (r["case"], r["model"], r["structured"])):
        if "score" not in r:
            lines.append(f"| {r['case']} | `{r['model']}` | {r['structured']} | — | — | — | — | — | — | — | "
                         f"{(r.get('error') or '')[:80]} |")
            continue
        verdict = (r.get("assessment") or "—") + ("" if r.get("assessment_ok") in (None, True) else " ✗")
        hit = {True: "✓", False: "✗", None: "—"}[r.get("root_cause_hit")]
        evidence = f"{r['evidence_ok']}/{r['evidence_total']}" if r.get("evidence_total") else "—"
        notes = []
        if r.get("structured_source") == "extracted":
            notes.append("附录由补抽生成")
        if (r.get("repair") or {}).get("adopted"):
            notes.append("证据已自动修正")
        if r.get("error"):
            notes.append(str(r["error"])[:60])
        lines.append("| " + " | ".join([
            r["case"], f"`{r['model']}`", r["structured"], _num(r["score"]), verdict, hit, evidence,
            _num(r.get("seconds"), "{:.0f}s"), _num(float((r.get("usage") or {}).get("total") or 0), "{:,.0f}"),
            _num(r.get("cost"), "${:.4f}"), "；".join(notes) or "",
        ]) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 从会话导出案例（反馈闭环）
# ---------------------------------------------------------------------------


def _toml_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_list(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_str(v) for v in values) + "]"


def case_from_turn(payload: dict[str, Any], feedback: dict[str, str] | None = None) -> str:
    """把一轮分析导出为 case.toml 文本。

    - 反馈"有用"：把这轮的判定和根因证据当作标准答案（回归用例，防止换模型 / 改提示词后变差）；
    - 反馈"根因不对" / "没用"：判定与根因留空待人工补全，用户的纠正写进 notes。
    """
    feedback = feedback or {}
    analysis = payload.get("analysis") or {}
    settings = payload.get("settings") or {}
    trusted = feedback.get("rating") == "up"
    refs: list[str] = []
    if trusted:
        for issue in analysis.get("issues") or []:
            for evidence in issue.get("evidence") or []:
                source = str(evidence.get("source") or "")
                if source and not any(source.endswith(Path(p).name) for p in payload.get("logs") or []):
                    refs.append(f"{source.replace(chr(92), '/').rsplit('/', 1)[-1]}:{evidence.get('line_start')}")
    lines = [
        f"# 由会话导出：{payload.get('generated_at') or ''} · 模型 {payload.get('model') or ''}",
        f"question = {_toml_str(str(payload.get('question') or ''))}",
        f"logs = {_toml_list(payload.get('logs') or [])}",
        f"code = {_toml_list(payload.get('code') or [])}",
    ]
    lines += [f"{key} = {_toml_str(str(settings[key]))}" for key in _SETTING_KEYS if settings.get(key)]
    lines += ["", "[expected]"]
    if trusted and analysis.get("assessment"):
        lines.append(f"assessment = {_toml_str(analysis['assessment'])}")
        lines.append(f"root_cause = {_toml_list(dict.fromkeys(refs))}")
    else:
        lines.append('# assessment = "finding"   # 待补全：finding / clear / unknown')
        lines.append('root_cause = []            # 待补全：根因所在 文件:行号')
    lines.append("keywords = []")
    note = feedback.get("comment") or (analysis.get("conclusion") if trusted else "")
    if note:
        lines.append(f"notes = {_toml_str(str(note))}")
    return "\n".join(lines) + "\n"
