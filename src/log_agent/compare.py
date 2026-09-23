"""两段时间窗口的对比：级别分布、新出现 / 明显增多 / 消失的错误签名。"""

from __future__ import annotations

from .timefilter import TimeWindow, parse_window

_SURGE_RATIO = 2.0
_MAX_ROWS = 8


def parse_range(text: str) -> TimeWindow:
    """解析 `13:00~13:30`、`13:00-13:30`、`2026-06-09 13:00~2026-06-09 13:30`。

    带日期时日期里本身有 `-`，只能用 `~` 分隔。

    Raises:
        ValueError: 格式不对，或起点晚于终点。
    """
    raw = (text or "").strip()
    if "~" in raw:
        since, _, until = raw.partition("~")
    elif raw.count("-") == 1:
        since, _, until = raw.partition("-")
    else:
        raise ValueError(f"看不懂的时间段：{text}（示例：13:00~13:30；带日期时用 ~ 分隔）")
    since, until = since.strip(), until.strip()
    if not since or not until:
        raise ValueError(f"时间段需要同时给出起点和终点：{text}")
    return parse_window(since, until)


def _rate(count: int, lines: int) -> str:
    return f"{count / lines:.1%}" if lines else "-"


def diff_signatures(
    base: dict[str, int], target: dict[str, int], base_lines: int, target_lines: int
) -> tuple[list, list, list]:
    """返回 (新出现, 明显增多, 减少或消失)，各自按影响排序。

    "明显增多"按每行日志的出现率比较：两段时间长度 / 流量不同，直接比次数会误判；
    也不按占全部错误的比例比，否则一类新错误暴增会把其它错误的占比"稀释"掉。出现率翻倍以上才算。
    """
    base_lines, target_lines = base_lines or 1, target_lines or 1
    new = sorted(((sig, n) for sig, n in target.items() if sig not in base), key=lambda x: -x[1])
    surged, dropped = [], []
    for sig, n in target.items():
        if sig in base and n > base[sig] and n / target_lines >= _SURGE_RATIO * base[sig] / base_lines:
            surged.append((sig, base[sig], n))
    for sig, n in base.items():
        after = target.get(sig, 0)
        if after < n:
            dropped.append((sig, n, after))
    surged.sort(key=lambda x: -(x[2] - x[1]))
    dropped.sort(key=lambda x: -(x[1] - x[2]))
    return new, surged, dropped


def render_comparison(name: str, base_desc: str, target_desc: str, base, target, redact) -> str:
    """base / target 是 tools._OverviewStats；redact 用来给错误签名脱敏截断。"""
    out = [f"--- 窗口对比 {name} ---"]
    out.append(f"基线：{base_desc}，L{base.window_first}-L{base.window_last}，共 {base.window_lines:,} 行")
    out.append(f"目标：{target_desc}，L{target.window_first}-L{target.window_last}，共 {target.window_lines:,} 行")
    out.append("")
    out.append("级别分布（次数 / 占窗口行数）：")
    for level in ("FATAL", "ERROR", "WARN"):
        b, t = base.levels[level], target.levels[level]
        if b or t:
            out.append(
                f"  {level:<5}  基线 {b:,} ({_rate(b, base.window_lines)})"
                f"  →  目标 {t:,} ({_rate(t, target.window_lines)})"
            )
    if not any(base.levels[x] or target.levels[x] for x in ("FATAL", "ERROR", "WARN")):
        out.append("  两段都没有 WARN 及以上级别的日志")

    new, surged, dropped = diff_signatures(
        base.signatures, target.signatures, base.window_lines, target.window_lines
    )
    if new:
        out.append("")
        out.append("基线里没有、目标时段新出现的错误：")
        for sig, n in new[:_MAX_ROWS]:
            out.append(f"  x{n}  首次 L{target.first_seen[sig]}  {redact(sig)}")
    if surged:
        out.append("")
        out.append("明显增多的错误（按每行出现率，翻倍以上）：")
        for sig, before, after in surged[:_MAX_ROWS]:
            out.append(f"  {before} → {after}  首次 L{target.first_seen[sig]}  {redact(sig)}")
    if dropped:
        out.append("")
        out.append("减少或消失的错误：")
        for sig, before, after in dropped[:_MAX_ROWS]:
            out.append(f"  {before} → {after}  {redact(sig)}")
    new_exc = [exc for exc in target.exceptions if exc not in base.exceptions]
    if new_exc:
        out.append("")
        out.append("新出现的异常类型：" + "、".join(
            f"{exc} x{target.exceptions[exc]}（首次 L{target.exc_first[exc]}）" for exc in new_exc[:_MAX_ROWS]
        ))
    if not (new or surged or dropped or new_exc):
        out.append("")
        out.append("两段的错误签名没有明显差异。")
    return "\n".join(out)
