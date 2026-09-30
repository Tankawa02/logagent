"""Versioned report contract and deterministic handoff views, independent of the agent."""
from __future__ import annotations

import json
import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class ReportView(StrEnum):
    brief = "brief"
    detailed = "detailed"
    ticket = "ticket"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(Record):
    source: str = Field(min_length=1)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    excerpt: str = Field(min_length=1)

    @model_validator(mode="after")
    def ordered(self):
        if self.line_end < self.line_start:
            raise ValueError("invalid evidence line range")
        return self


class Hypothesis(Record):
    explanation: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low"]
    reasoning: str = Field(min_length=1)


class Issue(Record):
    title: str = Field(min_length=1)
    symptoms: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    evidence: list[Evidence]
    root_cause_hypotheses: list[Hypothesis]
    open_questions: list[str]
    recommendations: list[str]
    reproduction_conditions: list[str]
    verification_steps: list[str]


class Analysis(Record):
    assessment: Literal["finding", "clear", "unknown"]
    confidence: Literal["high", "medium", "low"]
    conclusion: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    next_steps: list[str]
    issues: list[Issue]
    open_questions: list[str]

    @model_validator(mode="after")
    def consistent(self):
        if self.assessment == "finding" and not self.issues:
            raise ValueError("finding requires issues")
        if self.assessment == "clear" and self.issues:
            raise ValueError("clear cannot contain issues")
        return self


# A dedicated fence keeps ordinary JSON examples in evidence untouched.
_MARKER = re.compile(r"^```log-agent-report\s*$", re.MULTILINE)
_BLOCK = re.compile(r"^```log-agent-report\s*\n(.*?)\n```[ \t]*(?:\n|$)", re.MULTILINE | re.DOTALL)


def visible_report(text: str) -> str:
    """Hide the machine appendix, including a still-streaming/incomplete fence."""
    marker = _MARKER.search(text)
    return text[:marker.start()].rstrip() if marker else text


def extract_analysis(text: str) -> tuple[str, dict | None, str]:
    markers = list(_MARKER.finditer(text))
    if not markers:
        return text, None, "missing"
    blocks = list(_BLOCK.finditer(text))
    if len(markers) != 1 or len(blocks) != 1 or text[blocks[0].end():].strip():
        return text, None, "invalid"
    try:
        data = Analysis.model_validate(json.loads(blocks[0][1])).model_dump()
    except (ValueError, ValidationError):
        return text, None, "invalid"
    return visible_report(text), data, "valid"


def report_body(payload: dict, view: str = "detailed") -> str:
    ReportView(view)  # Reject misspellings rather than silently changing presentation.
    analysis = payload.get("analysis")
    if not analysis:
        return "结构化数据不可用，以下为原始回答（异常状态无法判定）。\n\n" + payload.get("report", "")
    lines = [f"## 结论\n\n{analysis['conclusion']}", f"## 影响范围\n\n{analysis['impact']}"]

    def section(title, items):
        lines.append(f"### {title}\n\n" + ("\n".join(f"- {s}" for s in items) or "待确认 / 暂无信息"))

    section("下一步", analysis["next_steps"])
    if view != "brief":
        for i, issue in enumerate(analysis["issues"], 1):
            lines.append(f"## 问题 {i}：{issue['title']}\n\n现象：{issue['symptoms']}\n\n影响：{issue['impact']}")
            section("复现条件（未验证的条件仍需确认）", issue["reproduction_conditions"])
            section("证据", [_evidence_entry(payload.get("evidence_check"), i, j, e)
                           for j, e in enumerate(issue["evidence"], 1)])
            section("根因假设", [f"{h['explanation']}（{h['confidence']}）：{h['reasoning']}"
                                 for h in issue["root_cause_hypotheses"]])
            section("待确认项", issue["open_questions"])
            section("处理建议", issue["recommendations"])
            section("验证方法", issue["verification_steps"])
    section("分析待确认项", analysis["open_questions"])
    if view == "detailed":
        lines.append("## 完整分析与推导\n\n" + payload.get("report", ""))
    return "\n\n".join(lines)


def _evidence_entry(check: dict | None, issue: int, index: int, evidence: dict) -> str:
    from .evidence import item_for, item_label

    head = f"{evidence['source']}:{evidence['line_start']}-{evidence['line_end']}"
    item = item_for(check, issue, index)
    if item:
        head += f"（{item_label(item)}）"
    return head + "\n\n    " + evidence["excerpt"].replace("\n", "\n    ")


REPORT_INSTRUCTIONS = """
## 机器可读报告附录（主代理每轮最终回答必须提供）
在所有正文之后追加且仅追加一个 ```log-agent-report 围栏，内容是符合下方 JSON Schema 的 JSON 对象。
不要在该围栏之后输出其他内容。正文仍按上述要求撰写；附录供本地程序校验与生成速览和工单。
assessment 必须显式填写 finding（发现问题）、clear（检查后未发现问题）、unknown（无法判定）。
不能仅根据是否有 ERROR 判断。finding 必须有问题列表，clear 的 issues 必须为空。
confidence 使用 high/medium/low；结论措辞不作为自动判定依据。简短追问、取证不足可用 unknown。
每个问题写明现象、影响、证据、根因假设及推导、待确认项、处理建议、复现条件、验证步骤。
只写取证支持的信息：影响未知写“待确认”；列表无信息用 []；不得编造复现步骤、行号或日志原文。
证据 source 使用工具返回的路径，line_start/line_end 使用真实行号，excerpt 保留脱敏内容。
程序会按 source 和行号回查原文核对 excerpt：excerpt 必须逐字摘自工具输出（可去掉行号前缀、用 … 省略中间部分），
不要改写、翻译或概括；核对不通过的证据会在报告里标为“与原文不符”。git 提交不作为 evidence 条目，写在根因假设的 reasoning 里。
根因是待验证的假设时明确标注，不能包装成已证实事实。不要自行填写运行时间、时区或来源清单，程序会记录。
Schema：
""" + json.dumps(Analysis.model_json_schema(), ensure_ascii=False)
