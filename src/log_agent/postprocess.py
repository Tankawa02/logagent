"""报告正文写完之后的两步收尾：结构化抽取与证据修正。

1. 结构化抽取（structured = extract / auto）
   - extract：系统提示词里不再要求模型手写 JSON 附录，正文写完后用 `with_structured_output`
     单独抽取一次，流式正文里也就没有附录需要隐藏；
   - auto（默认）：仍让模型在正文末尾写附录，只有附录缺失或校验不过时才补一次抽取。
   抽取优先用 json_schema（原生结构化输出），接口不支持时退回 function calling。

2. 证据修正（回查出现 shifted / mismatch 时，最多一轮）
   - 行号偏移（shifted）：回查已经知道实际行号，直接改正，不调用模型；
   - 与原文不符（mismatch）：先在整份来源里逐字查找摘录（行号写错太多的情况），
     仍找不到的，把所引行附近的真实原文回传给模型，让它修正摘录 / 行号，或者删掉无法对应原文的证据；
   - 改完重新回查，只有结果确实变好时才采用，否则保留原样并记录尝试过程。
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .report import REPORT_RULES, Analysis
from .usage import usage_from_metadata

STRUCTURED_MODES = ("inline", "extract", "auto")
# 单轮最多交给模型修正的证据条数：再多说明报告整体不可靠，修正也救不回来
MAX_REPAIR_ITEMS = 8
_REPORT_CHARS = 60_000

_STRUCTURE_PROMPT = """你是日志排查报告的结构化助手。把用户给出的排查报告正文整理成符合 JSON Schema 的结构化数据。
只依据报告正文，不补充、不推测正文里没有的信息。
evidence 只能取自报告里逐字贴出的日志 / 源码原文：source 用报告里写的文件路径，line_start / line_end 用报告标注的行号，
excerpt 逐字复制原文（可以去掉行号前缀，用 … 省略中间部分），不要改写、翻译或概括；报告里没有贴原文的结论不要编造证据。
""" + REPORT_RULES

_REPAIR_PROMPT = """你是日志排查报告的证据校对助手。程序按 source 和行号回查原文后，发现下面几条证据的摘录与原文对不上。
每条都附上了所引行附近的真实原文（行号: 内容，已按同样规则脱敏）。对每条证据二选一：
- fix：在给出的原文里找到能支持这条证据的行，返回正确的 line_start / line_end，excerpt 逐字复制这些行（去掉行号前缀）；
- drop：给出的原文里没有能支持它的内容，删除这条证据。
不要编造原文里没有的内容，不要改动未列出的证据。"""


class EvidenceFix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue: int = Field(ge=1, description="问题序号（从 1 开始）")
    index: int = Field(ge=1, description="证据在该问题 evidence 列表里的序号（从 1 开始）")
    action: Literal["fix", "drop"]
    line_start: int | None = Field(default=None, description="fix 时必填")
    line_end: int | None = Field(default=None, description="fix 时必填")
    excerpt: str | None = Field(default=None, description="fix 时必填：逐字复制的原文")


class EvidenceFixes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixes: list[EvidenceFix]


def normalize_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in STRUCTURED_MODES else "auto"


_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def _loose_parse(schema: type[BaseModel], raw: Any) -> BaseModel | None:
    """接口忽略了结构化输出参数、把 JSON 写在正文里时，从文本里捞出来再校验。"""
    content = getattr(raw, "content", "")
    if isinstance(content, list):
        content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
    match = _JSON_BLOCK.search(str(content or ""))
    if not match:
        return None
    try:
        return schema.model_validate(json.loads(match.group(0)))
    except ValueError:
        return None


def _call_record(purpose: str, method: str, raw: Any, started: float, error: str = "") -> dict[str, Any]:
    metadata = getattr(raw, "response_metadata", None) or {}
    usage = usage_from_metadata(getattr(raw, "usage_metadata", None)) if raw is not None else None
    return {
        "purpose": purpose,
        "method": method,
        "t0": started,
        "seconds": round(time.perf_counter() - started, 3),
        "input": usage["input"] if usage else None,
        "output": usage["output"] if usage else None,
        "cache_read": usage["cache_read"] if usage else None,
        "reasoning": usage["reasoning"] if usage else None,
        "usage": usage,
        "model": str(metadata.get("model_name") or metadata.get("model") or ""),
        "finish_reason": str(metadata.get("finish_reason") or ""),
        "error": error,
        "incomplete": raw is None,
        "tool_calls": 0,
    }


def invoke_structured(model: Any, schema: type[BaseModel], messages: list[dict[str, str]],
                      purpose: str) -> tuple[BaseModel | None, list[dict[str, Any]]]:
    """依次尝试 json_schema / function_calling，返回（解析结果, 每次尝试的调用记录）。"""
    calls: list[dict[str, Any]] = []
    for method in ("json_schema", "function_calling"):
        started = time.perf_counter()
        try:
            runnable = model.with_structured_output(schema, method=method, include_raw=True)
            out = runnable.invoke(messages)
        except Exception as exc:  # noqa: BLE001 — 接口不支持某种方式时换下一种
            calls.append(_call_record(purpose, method, None, started, f"{type(exc).__name__}: {exc}"[:300]))
            continue
        raw, parsed = out.get("raw"), out.get("parsed")
        if parsed is None:
            parsed = _loose_parse(schema, raw)
        error = "" if parsed is not None else str(out.get("parsing_error") or "未返回可解析的结构化结果")[:300]
        calls.append(_call_record(purpose, method, raw, started, error))
        if parsed is not None:
            return parsed, calls
    return None, calls


class PostProcessor:
    """挂在 build_agent 返回的图上（`agent.log_agent_post`），StreamRenderer.run 收尾时调用。"""

    def __init__(self, model: Any, mode: str = "auto", repair: bool = True) -> None:
        self.model = model
        self.mode = normalize_mode(mode)
        self.repairs = repair

    @property
    def usable(self) -> bool:
        return hasattr(self.model, "with_structured_output")

    @property
    def extracts(self) -> bool:
        return self.usable and self.mode in ("extract", "auto")

    def structure(self, report: str) -> tuple[dict | None, list[dict[str, Any]]]:
        text = report if len(report) <= _REPORT_CHARS else report[:_REPORT_CHARS] + "\n…（正文过长，已截断）"
        parsed, calls = invoke_structured(self.model, Analysis, [
            {"role": "system", "content": _STRUCTURE_PROMPT},
            {"role": "user", "content": f"排查报告正文如下：\n\n{text}"},
        ], "structure")
        if parsed is None:
            return None, calls
        try:
            return Analysis.model_validate(parsed.model_dump()).model_dump(), calls
        except ValueError as exc:
            calls[-1]["error"] = f"校验失败：{exc}"[:300]
            return None, calls

    def repair(self, analysis: dict, check: dict, log_paths, code_dirs) -> tuple[dict, dict | None, dict, list[dict]]:
        return repair_evidence(analysis, check, log_paths, code_dirs, self.model if self.usable else None)


def _evidence(analysis: dict, issue: int, index: int) -> dict | None:
    issues = analysis.get("issues") or []
    if not 1 <= issue <= len(issues):
        return None
    items = issues[issue - 1].get("evidence") or []
    return items[index - 1] if 1 <= index <= len(items) else None


def _counts(check: dict | None) -> dict[str, int]:
    check = check or {}
    return {key: int(check.get(key) or 0) for key in ("total", "verified", "shifted", "mismatch", "unresolved")}


def repair_evidence(analysis: dict, check: dict, log_paths, code_dirs,
                    model: Any = None) -> tuple[dict, dict | None, dict, list[dict]]:
    """返回（采用后的 analysis, 采用后的回查结果, 修正记录, 模型调用记录）。没有变好时原样返回前两项。"""
    from .evidence import SourceResolver, check_analysis, relocate, source_window

    fixed = deepcopy(analysis)
    resolver = SourceResolver(log_paths, code_dirs)
    record: dict[str, Any] = {"before": _counts(check), "shifted_fixed": 0, "relocated": 0,
                              "model_fixed": 0, "dropped": 0, "adopted": False}
    calls: list[dict] = []
    pending: list[tuple[dict, dict]] = []
    for item in check.get("items") or []:
        evidence = _evidence(fixed, item["issue"], item["index"])
        if evidence is None:
            continue
        if item["status"] == "shifted" and item.get("actual_start"):
            evidence["line_start"], evidence["line_end"] = item["actual_start"], item["actual_end"]
            record["shifted_fixed"] += 1
        elif item["status"] == "mismatch":
            span = relocate(resolver, evidence)
            if span is not None:
                evidence["line_start"], evidence["line_end"] = span
                record["relocated"] += 1
            else:
                pending.append((item, evidence))

    drops: set[tuple[int, int]] = set()
    if pending and model is not None:
        entries = []
        for item, evidence in pending[:MAX_REPAIR_ITEMS]:
            window = source_window(resolver, evidence["source"], evidence["line_start"], evidence["line_end"])
            entries.append({
                "issue": item["issue"], "index": item["index"], "source": evidence["source"],
                "line_start": evidence["line_start"], "line_end": evidence["line_end"],
                "excerpt": evidence["excerpt"], "problem": item.get("note") or "与原文不符",
                "original_text": window or "（读不到所引位置附近的原文）",
            })
        parsed, calls = invoke_structured(model, EvidenceFixes, [
            {"role": "system", "content": _REPAIR_PROMPT},
            {"role": "user", "content": json.dumps(entries, ensure_ascii=False, indent=1)},
        ], "repair")
        allowed = {(e["issue"], e["index"]) for e in entries}
        for fix in (parsed.fixes if isinstance(parsed, EvidenceFixes) else []):
            key = (fix.issue, fix.index)
            evidence = _evidence(fixed, *key)
            if key not in allowed or evidence is None:
                continue
            if fix.action == "drop":
                drops.add(key)
            elif fix.line_start and fix.excerpt and fix.excerpt.strip():
                start = int(fix.line_start)
                end = max(start, int(fix.line_end or start))
                evidence.update(line_start=start, line_end=end, excerpt=fix.excerpt)
                record["model_fixed"] += 1
        for issue, index in sorted(drops, reverse=True):
            del fixed["issues"][issue - 1]["evidence"][index - 1]
        record["dropped"] = len(drops)

    changed = record["shifted_fixed"] + record["relocated"] + record["model_fixed"] + record["dropped"]
    if not changed:
        return analysis, check, record, calls
    try:
        fixed = Analysis.model_validate(fixed).model_dump()
        new_check = check_analysis(fixed, log_paths, code_dirs)
    except Exception as exc:  # noqa: BLE001
        record["error"] = f"{type(exc).__name__}: {exc}"[:300]
        return analysis, check, record, calls
    before, after = record["before"], _counts(new_check)
    record["after"] = after
    supported_before = before["verified"] + before["shifted"]
    supported_after = after["verified"] + after["shifted"]
    better = (after["mismatch"] < before["mismatch"] or after["shifted"] < before["shifted"])
    if new_check is not None and better and after["mismatch"] <= before["mismatch"] and supported_after >= supported_before:
        record["adopted"] = True
        return fixed, new_check, record, calls
    return analysis, check, record, calls
