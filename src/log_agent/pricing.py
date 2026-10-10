"""把 token 用量换算成金额（美元）。

价格来源按优先级：
1. 配置文件里的 `[pricing."模型名"]`（美元 / 百万 tokens：input、output，可选 cache_read、cache_write）；
2. 接口地址是 OpenRouter 时，读取它公开的模型目录 `/models`（本地缓存 24 小时，取不到就跳过）；
3. 内置的少量常见模型价格。
都找不到时金额记为未知（None），界面显示"—"而不是 0，避免误导。
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .usage import add_usage, empty_usage

CATALOG_TTL_SECONDS = 24 * 3600
_FETCH_TIMEOUT = 5.0


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    source: str = "builtin"


_BUILTIN: dict[str, Price] = {
    "gpt-4.1": Price(2.0, 8.0, 0.5),
    "gpt-4.1-mini": Price(0.4, 1.6, 0.1),
    "gpt-4o": Price(2.5, 10.0, 1.25),
    "gpt-4o-mini": Price(0.15, 0.6, 0.075),
}

_catalog_lock = threading.Lock()
_catalog_memo: dict[str, dict[str, Price]] = {}


def normalize_model(model: str) -> str:
    """去掉 `openai:` 之类的 provider 前缀，得到接口真正认的模型名。"""
    name = (model or "").strip()
    head, sep, rest = name.partition(":")
    if sep and "/" not in head and head.isidentifier():
        return rest
    return name


def _is_openrouter(base_url: str | None) -> bool:
    return bool(base_url) and urlparse(base_url).hostname in {"openrouter.ai", "www.openrouter.ai"}


def _per_million(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number * 1_000_000 if number >= 0 else None


def _cache_path(base_url: str) -> Path:
    host = urlparse(base_url).hostname or "catalog"
    return Path.home() / ".log-agent" / "cache" / f"models-{host}.json"


def _parse_catalog(data: dict) -> dict[str, Price]:
    prices: dict[str, Price] = {}
    for item in data.get("data") or []:
        pricing = item.get("pricing") or {}
        model_id, prompt, completion = item.get("id"), _per_million(pricing.get("prompt")), _per_million(pricing.get("completion"))
        if not model_id or prompt is None or completion is None:
            continue
        prices[model_id] = Price(
            prompt, completion, _per_million(pricing.get("input_cache_read")),
            _per_million(pricing.get("input_cache_write")), source="openrouter",
        )
    return prices


def _load_catalog(base_url: str) -> dict[str, Price]:
    with _catalog_lock:
        if base_url in _catalog_memo:
            return _catalog_memo[base_url]
        path = _cache_path(base_url)
        data: dict | None = None
        try:
            if time.time() - path.stat().st_mtime < CATALOG_TTL_SECONDS:
                data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if data is None:
            try:
                url = base_url.rstrip("/") + "/models"
                with urllib.request.urlopen(url, timeout=_FETCH_TIMEOUT) as resp:  # noqa: S310 — 固定的 https 目录地址
                    data = json.loads(resp.read().decode("utf-8"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data), encoding="utf-8")
            except (OSError, ValueError):
                data = {}
        _catalog_memo[base_url] = _parse_catalog(data)
        return _catalog_memo[base_url]


def _configured() -> dict[str, Price]:
    from .config import loaded

    table = loaded().shared.get("pricing") or {}
    prices: dict[str, Price] = {}
    for name, entry in table.items():
        if not isinstance(entry, dict):
            continue
        try:
            prices[str(name)] = Price(
                float(entry["input"]), float(entry["output"]),
                float(entry["cache_read"]) if "cache_read" in entry else None,
                float(entry["cache_write"]) if "cache_write" in entry else None,
                source="config",
            )
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def _match(table: dict[str, Price], name: str) -> Price | None:
    if name in table:
        return table[name]
    # 接口回报的模型名常带日期等后缀（`grok-4.7-20260901`）：取目录里最长的前缀匹配
    candidates = [key for key in table if name.startswith(key)]
    return table[max(candidates, key=len)] if candidates else None


def price_for(model: str, base_url: str | None = None) -> Price | None:
    name = normalize_model(model)
    if not name:
        return None
    base_url = base_url if base_url is not None else os.environ.get("OPENAI_BASE_URL")
    found = _match(_configured(), name)
    if found is None and _is_openrouter(base_url):
        found = _match(_load_catalog(base_url), name)
    return found or _match(_BUILTIN, name)


def cost_of(usage: dict[str, Any] | None, price: Price | None) -> float | None:
    """按价格换算一份用量；价格未知时返回 None。命中缓存的输入按缓存价计，缺缓存价时按普通输入价。"""
    if price is None or not usage:
        return None
    input_tokens = int(usage.get("input") or 0)
    cache_read = min(int(usage.get("cache_read") or 0), input_tokens)
    cache_write = min(int(usage.get("cache_write") or 0), input_tokens - cache_read)
    fresh = input_tokens - cache_read - cache_write
    total = (
        fresh * price.input
        + cache_read * (price.cache_read if price.cache_read is not None else price.input)
        + cache_write * (price.cache_write if price.cache_write is not None else price.input)
        + int(usage.get("output") or 0) * price.output
    )
    return round(total / 1_000_000, 6)


def turn_cost(usage_by_model: dict[str, dict[str, int]], fallback_model: str,
              base_url: str | None = None) -> dict[str, Any]:
    """按实际调用的模型分别计价后求和。

    usage_by_model 的键是接口回报的模型名（主模型、子代理模型、备用模型可能不同）；
    回报里没有模型名的用量记在空字符串下，按会话配置的模型计价。
    """
    total = 0.0
    priced = empty_usage()
    unpriced = empty_usage()
    sources: set[str] = set()
    for name, usage in usage_by_model.items():
        price = price_for(name or fallback_model, base_url) or (price_for(fallback_model, base_url) if name else None)
        amount = cost_of(usage, price)
        if amount is None:
            add_usage(unpriced, usage)
            continue
        total += amount
        add_usage(priced, usage)
        sources.add(price.source)
    complete = unpriced["total"] == 0 and unpriced["input"] == 0
    return {
        "usd": round(total, 6) if priced["total"] or priced["input"] else (0.0 if complete else None),
        "complete": complete,
        "unpriced_tokens": unpriced["total"] or unpriced["input"] + unpriced["output"],
        "sources": sorted(sources),
    }
