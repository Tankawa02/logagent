"""token 用量的统一口径：输入 / 输出 / 总量，外加缓存命中、缓存写入与推理 tokens。

LangChain 把各家的用量统一成 `usage_metadata`：
- `input_tokens` 已经包含命中缓存的部分，`input_token_details.cache_read` 是其中命中缓存的 tokens；
- `input_token_details.cache_creation` 是本次写入缓存的 tokens（Anthropic 等按写入单独计价）；
- `output_token_details.reasoning` 是推理模型的思考 tokens，已包含在 `output_tokens` 里。
不支持的 provider 这些字段缺省为 0，不影响三项基础数。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

USAGE_KEYS = ("input", "output", "total", "cache_read", "cache_write", "reasoning")


def empty_usage() -> dict[str, int]:
    return dict.fromkeys(USAGE_KEYS, 0)


def usage_from_metadata(usage: Mapping[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    inputs = usage.get("input_token_details") or {}
    outputs = usage.get("output_token_details") or {}
    return {
        "input": int(usage.get("input_tokens") or 0),
        "output": int(usage.get("output_tokens") or 0),
        "total": int(usage.get("total_tokens") or 0),
        "cache_read": int(inputs.get("cache_read") or 0),
        "cache_write": int(inputs.get("cache_creation") or 0),
        "reasoning": int(outputs.get("reasoning") or 0),
    }


def add_usage(into: dict[str, int], extra: Mapping[str, Any] | None) -> dict[str, int]:
    for key in USAGE_KEYS:
        into[key] = int(into.get(key) or 0) + int((extra or {}).get(key) or 0)
    return into


def cache_hit_rate(usage: Mapping[str, Any] | None) -> float | None:
    """输入 tokens 里命中缓存的比例；没有输入时为 None。"""
    usage = usage or {}
    total_input = int(usage.get("input") or 0)
    if total_input <= 0:
        return None
    return int(usage.get("cache_read") or 0) / total_input
