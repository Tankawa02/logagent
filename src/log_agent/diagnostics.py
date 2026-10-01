"""本地预检查，不创建 agent、不发送日志或调用模型。"""

from __future__ import annotations

import codecs
import os
from collections import Counter
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from urllib.parse import urlsplit

from .config import _ENV_OVERRIDES, _KEYS, load_config

UNKNOWN = "未识别"


def inspect_log(path: str, window) -> str:
    from .logfile import open_log
    from .redact import redact_log
    from .tools import _human_size, _scan_overview

    log = open_log(path)
    stats = _scan_overview(log, window)
    total = stats.total
    lines = [
        f"日志：{path}",
        f"大小 {_human_size(log.size)} · 编码 {log.encoding} · {'gzip · ' if log.gz else ''}共 {total:,} 行",
        f"首末时间：{stats.first_ts or '未识别'} → {stats.last_ts or '未识别'}",
        f"时间戳识别：{stats.timestamp_lines}/{total} 行（{stats.timestamp_lines / (total or 1):.1%}）",
        f"日志级别识别：{stats.level_lines}/{total} 行（{stats.level_lines / (total or 1):.1%}）",
        f"当前窗口：{window.describe() if window else '全文'} · 覆盖 {stats.window_lines:,} 行",
        "窗口级别分布：" + (" · ".join(f"{k} {v}" for k, v in stats.levels.items()) or "未识别"),
    ]
    formats = _format_breakdown(log)
    if formats:
        lines.insert(4, "识别格式（前 2000 行抽样，不含堆栈续行）：" + " · ".join(formats))
    clusters = stats.chains.top(3)
    if clusters:
        from .tools import _chain_lines

        lines.append(f"异常链：共 {len(stats.chains.clusters)} 类（按根因异常 + 首个业务栈帧），最多的几类：")
        lines.extend(_chain_lines(clusters))
    if not total:
        lines.append("提示：文件为空。")
    elif not stats.saw_timestamp:
        lines.append("提示：未识别到时间戳，无法验证时间窗口；分析工具可能回退到全文。")
    elif window and not stats.window_lines:
        lines.append("提示：当前时间窗口没有日志，请核对时间范围和时区。")
    lines.append("识别比例按物理行计算；堆栈续行没有独立时间戳或级别通常是正常的。")
    lines.append("高频错误：")
    lines.extend(
        f"  x{count} · 首次 L{stats.first_seen[sig]} · {redact_log(sig)}"
        for sig, count in stats.signatures.most_common(5)
    )
    if not stats.signatures:
        lines.append("  未识别到 ERROR / FATAL 签名（不代表业务一定正常）")
    lines.append("样例（当前窗口，最多 3 行，跟随脱敏设置）：")
    lines.extend(f"  L{lineno}: {redact_log(line)}" for lineno, line in stats.samples)
    return "\n".join(lines)


_MAX_CONTINUATION = 50


def _looks_like_continuation(line: str) -> bool:
    from .stacktrace import starts_block

    return line[:1] in " \t" or line.lstrip()[:1] in ")]}," or starts_block(line)


@dataclass
class FormatCounts:
    counts: Counter[str] = field(default_factory=Counter)
    seen: int = 0
    unknown_example: str = ""


def _format_counts(log, sample: int = 2000) -> FormatCounts:
    from contextlib import closing

    from .logformat import detect_format, is_stack_line

    result = FormatCounts()
    previous_known = False
    run = 0  # 已经连续当成续行跳过的行数
    with closing(log.iter_lines(1)) as lines:
        for lineno, line in lines:
            if lineno > sample:
                break
            if not line.strip() or is_stack_line(line):
                continue
            kind = detect_format(line)
            # 只把“像续行”的行当成上一条的一部分（异常头、缩进的多行 SQL / JSON），并且有长度上限；
            # 否则一条识别成功的行后面跟着的所有未识别记录都会被吞掉，覆盖率虚高到 100%
            if kind is None and previous_known and run < _MAX_CONTINUATION and _looks_like_continuation(line):
                run += 1
                continue
            run = 0
            previous_known = kind is not None
            result.seen += 1
            result.counts[kind or UNKNOWN] += 1
            if kind is None and not result.unknown_example:
                result.unknown_example = line
    return result


def _format_breakdown(log, sample: int = 2000) -> list[str]:
    result = _format_counts(log, sample)
    if not result.seen:
        return []
    return [f"{name} {count / result.seen:.0%}" for name, count in result.counts.most_common(4)]


@dataclass
class LogSample:
    """init 用的抽样结果：只看文件开头一段，不扫全文。"""

    path: str
    encoding: str
    formats: list[tuple[str, float]]
    unknown_ratio: float
    unknown_example: str
    naive_stamps: int
    aware_stamps: int
    levels: Counter[str]
    lines: int

    @property
    def needs_timezone(self) -> bool:
        return self.naive_stamps > 0


def sample_log(path: str, sample: int = 2000) -> LogSample:
    """抽样识别一份日志：格式分布、时间戳是否带时区、级别分布。

    Raises:
        OSError / EOFError: 文件读不了。
    """
    from contextlib import closing
    from datetime import datetime

    from .logfile import open_log
    from .logformat import level_and_body
    from .timefilter import find_timestamp

    log = open_log(path)
    formats = _format_counts(log, sample)
    naive = aware = lines = 0
    levels: Counter[str] = Counter()
    with closing(log.iter_lines(1)) as rows:
        for lineno, line in rows:
            if lineno > sample:
                break
            lines = lineno
            found = find_timestamp(line)
            if found:
                if isinstance(found[1], datetime) and found[1].tzinfo is not None:
                    aware += 1
                else:
                    naive += 1
            parsed = level_and_body(line)
            if parsed:
                levels[parsed[0]] += 1
    seen = formats.seen or 1
    ratios = [(name, count / seen) for name, count in formats.counts.most_common()]
    unknown = formats.counts.get(UNKNOWN, 0) / seen if formats.seen else 0.0
    return LogSample(str(path), log.encoding, ratios, unknown, formats.unknown_example, naive, aware, levels, lines)


def effective_config(app, command: str) -> tuple[object, dict, dict]:
    """使用 CLI 参数定义解析默认值和配置，避免 doctor 另设一套默认值。"""
    from typer.main import get_command

    config = load_config()
    parameters = {p.name: p for p in get_command(app).commands[command].params}
    values, origins = {}, {}
    configured = config.for_command(command)
    for key in _KEYS:
        if key not in parameters and key not in {"timeout", "max_retries"}:
            continue
        value = parameters[key].default if key in parameters else {"timeout": 120, "max_retries": 3}[key]
        origin = "内置默认"
        if key in configured:
            value, origin = configured[key], "配置文件"
        env = _ENV_OVERRIDES.get(key)
        if env and os.environ.get(env):
            value, origin = os.environ[env], f"环境变量 {env}"
        if key in parameters and value is not None:
            # type_cast_value 同时处理多路径选项和 enum。
            value = parameters[key].type_cast_value(None, value)
        values[key], origins[key] = value, origin
    if "model" in values and not values["model"]:
        from .cli import DEFAULT_MODEL

        values["model"] = DEFAULT_MODEL
    return config, values, origins


def safe_config_value(key: str, value) -> str:
    if key == "base_url" and value:
        # 网关 URL 可能包含密码或 query token；诊断仅显示协议和主机。
        try:
            url = urlsplit(str(value))
            return f"{url.scheme}://{url.hostname or '(无主机)'}/…"
        except ValueError:
            return "（接口地址格式无效）"
    return str(value) if value is not None else "未设置"


def configuration_checks(values: dict) -> list[tuple[bool, str]]:
    from .budget import parse_budget
    from .compare import parse_range
    from .timefilter import parse_window, set_default_timezone

    checks = []
    validators = [
        ("时区", lambda: set_default_timezone(values.get("timezone") or "UTC")),
        ("编码", lambda: codecs.lookup(values.get("encoding") or "utf-8")),
        ("预算", lambda: parse_budget(values["budget"]) if values.get("budget") else None),
        ("基线", lambda: parse_range(values["baseline"]) if values.get("baseline") else None),
        ("时间范围", lambda: parse_window(values.get("since"), values.get("until"))),
    ]
    for label, validate in validators:
        try:
            validate()
        except (ValueError, LookupError, TypeError) as exc:
            checks.append((False, f"{label}配置无效：{exc}"))
        else:
            checks.append((True, f"{label}配置可解析"))
    for key, cast in (("timeout", float), ("max_retries", int)):
        try:
            number = cast(values.get(key, 0))
            valid = 0 <= number < float("inf")
        except (ValueError, TypeError):
            valid = False
        checks.append((valid, f"{key} {'有效' if valid else '必须为非负有限数值'}"))
    for key in ("code", "skills"):
        for path in values.get(key) or []:
            checks.append((Path(path).is_dir(), f"{key} 目录：{path}"))
    if values.get("base_url"):
        try:
            url = urlsplit(values["base_url"])
            valid = url.scheme in {"http", "https"} and bool(url.hostname)
        except ValueError:
            valid = False
        checks.append((valid, "模型接口地址格式" + ("有效" if valid else "无效，需要 http(s) 地址")))
    if "model" in values:
        present = bool(os.environ.get("OPENAI_API_KEY"))
        checks.append((present, "OPENAI_API_KEY " + ("已设置（仅检查存在；用 doctor --ping 验证是否有效）" if present else "未设置")))
    return checks


def dependency_checks() -> list[tuple[bool, str]]:
    try:
        from packaging.requirements import Requirement
    except ImportError:
        return [(False, "缺少 packaging，无法核对依赖；请运行 uv sync")]
    try:
        requirements = metadata.requires("log-agent") or []
    except metadata.PackageNotFoundError:
        return [(False, "项目尚未安装，请运行 uv sync")]
    results = []
    for text in requirements:
        requirement = Requirement(text)
        if requirement.marker and not requirement.marker.evaluate({"extra": ""}):
            continue
        try:
            installed = metadata.version(requirement.name)
        except metadata.PackageNotFoundError:
            results.append((False, f"缺少依赖：{requirement.name}"))
            continue
        valid = requirement.specifier.contains(installed)
        results.append((valid, f"{requirement.name} {installed} · 要求 {requirement.specifier or '任意版本'}"))
    return results
