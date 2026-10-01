"""CLI 各模式共用的上下文消息、启动面板数据与运行配置。"""

from __future__ import annotations

from pathlib import Path

from rich.text import Text

from .term import console


def _build_context_message(log_paths: list[str], code_paths: list[str], question: str, baseline=None) -> str:
    """把日志/源码路径和问题拼成给 agent 的首条消息。baseline 是 --baseline 解析出的 TimeWindow。"""
    lines: list[str] = []
    if len(log_paths) == 1:
        lines.append(f"日志文件路径：{log_paths[0]}")
    else:
        lines.append(
            f"共提供了 {len(log_paths)} 份日志，请先分别调用 log_overview；"
            "追同一个请求时可用 trace_request 一次传入全部路径，按时间合并各日志：",
        )
        lines.extend(f"  {i}. {p}" for i, p in enumerate(log_paths, start=1))

    if code_paths:
        if len(code_paths) == 1:
            lines.append(f"源码目录路径：{code_paths[0]}")
        else:
            lines.append(f"共提供了 {len(code_paths)} 个源码目录，可分别检索：")
            lines.extend(f"  {i}. {p}" for i, p in enumerate(code_paths, start=1))
            lines.append(
                "（调用 list_code_files / read_code_file / grep_code 时，"
                "请用对应仓库的目录路径作为 code_dir，按需逐个排查。）"
            )
    else:
        lines.append("（本次未提供源码目录，只分析日志。）")

    from .timefilter import default_timezone, default_window

    lines.append(f"时间解释：无偏移时间和纯时刻边界使用 {default_timezone()}；带偏移日志按实际时刻比较。")
    window = default_window()
    if window:
        lines.append(
            f"时间窗口：{window.describe()}。log_overview / search_logs 不传 since/until 时会自动只看这个窗口；"
            "需要对比窗口之前的情况时可以显式传入其它时间。read_log_chunk 按行号读取，不受窗口限制。"
        )
    else:
        lines.append("时间窗口：全文，没有默认时间限制。")
    if baseline:
        target = "上面的时间窗口" if window else "全文"
        lines.append(
            f"对比基线：用户给出的正常时段是 {baseline.describe()}。请先对每份日志调用 compare_windows"
            f'（baseline_since="{baseline.since.raw}"，baseline_until="{baseline.until.raw}"，'
            f"目标时段为{target}），再围绕新出现和明显增多的错误深入排查。"
        )
    else:
        lines.append("对比基线：未设置。")
    lines.append(f"\n用户问题：{question}")
    return "\n".join(lines)


def _skill_sources(skills: list[Path] | None):
    from .skills import resolve_skill_sources

    return resolve_skill_sources(skills or [])


def _skills_value(sources) -> Text:
    value = Text()
    for i, source in enumerate(sources):
        if i:
            value.append("\n")
        value.append(f"{source.skill_count()} 个", style="accent")
        value.append(f"  {source.directory}", style="muted")
    return value


def _base_rows(
    log_paths: list[str],
    code_paths: list[str],
    model: str,
    base_url: str | None,
    skill_sources=(),
) -> list[tuple[str, Text | str]]:
    from . import redact
    from .logfile import open_log
    from .render import display_path

    # 面板边框、内边距、标签列约占 16 列，再给编码标签留 12 列
    width = max(30, console.width - 16)
    log_value = Text()
    for i, path in enumerate(log_paths):
        if i:
            log_value.append("\n")
        log_value.append(display_path(path, width - 12))
        try:
            meta = open_log(path)
            tags = [meta.encoding] + (["gzip"] if meta.gz else [])
            log_value.append(f"  {' · '.join(tags)}", style="muted")
        except OSError:
            pass

    code_value = (
        Text("\n".join(display_path(p, width) for p in code_paths)) if code_paths else Text("（无）", style="muted")
    )
    rows: list[tuple[str, Text | str]] = [
        ("日志", log_value),
        ("源码", code_value),
        ("模型", Text(model, style="accent")),
    ]
    if base_url:
        rows.append(("接口", base_url))
    if skill_sources:
        rows.append(("Skills", _skills_value(skill_sources)))

    from .timefilter import default_timezone, default_window

    rows.append(("时区", str(default_timezone())))
    if default_window():
        rows.append(("时间", Text(default_window().describe(), style="accent")))
    rows.append(("脱敏", Text("开启", style="ok") if redact.is_enabled() else Text("已关闭", style="warn")))

    from .config import loaded

    if loaded().files:
        rows.append(("配置", Text("\n".join(str(p) for p in loaded().files), style="muted")))
    return rows


def _run_config(max_steps: int, thread_id: str | None = None) -> dict:
    # langgraph 的每个节点执行算一步，一次"模型 + 工具"大约 2-3 步
    config: dict = {"recursion_limit": max_steps * 3}
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    return config
