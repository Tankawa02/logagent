"""`log-agent init`：检查 Key → 选模型 → 抽样日志识别格式 → 生成 .log-agent.toml。

原则：
- Key 只放在环境变量里，永远不写进配置文件（配置文件经常被提交进仓库）。
- 每一步都有可检测的默认值，`--yes` 时不提问、直接用默认值，方便脚本和 CI。
- 生成的文件先用真正的配置加载逻辑校验一遍，写出去的一定能被 analyze / chat 读懂。
"""

from __future__ import annotations

import json
import os
import shlex
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import typer
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from .term import IS_WINDOWS, console, glyphs

PRESET_MODELS = ("openai:gpt-4.1", "openai:gpt-4.1-mini", "openai:gpt-4o")
_LOG_GLOBS = ("*.log", "logs/*.log", "log/*.log", "*.log.gz", "logs/*.log.gz", "log/*.log.gz")
_PROJECT_MARKERS = (".git", "pyproject.toml", "setup.py", "pom.xml", "build.gradle", "build.gradle.kts",
                    "package.json", "go.mod", "Cargo.toml", "composer.json", "Gemfile")
# 未识别比例超过这个值时，在配置里附上一份自定义格式模板
_CUSTOM_FORMAT_THRESHOLD = 0.3
_MAX_LISTED_MODELS = 15


@dataclass
class InitChoices:
    model: str
    base_url: str | None = None
    code: list[str] = field(default_factory=list)
    timezone: str | None = None
    samples: list = field(default_factory=list)  # diagnostics.LogSample


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------


def discover_logs(cwd: Path, limit: int = 3) -> list[Path]:
    """当前目录下最近改动过的几份日志，作为抽样的默认值。"""
    found: dict[Path, float] = {}
    for pattern in _LOG_GLOBS:
        for path in cwd.glob(pattern):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    found[path] = path.stat().st_mtime
            except OSError:
                continue
    return sorted(found, key=lambda p: -found[p])[:limit]


def looks_like_project(cwd: Path) -> bool:
    return any((cwd / marker).exists() for marker in _PROJECT_MARKERS) or any(cwd.glob("*.csproj"))


def local_offset() -> str:
    offset = datetime.now().astimezone().strftime("%z") or "+0000"
    return f"{offset[:3]}:{offset[3:]}"


def unsafe_url_reason(url: str) -> str | None:
    """网关地址里带了凭据时不能写进配置文件。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return "地址格式无效"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return "需要 http(s) 地址"
    if parts.username or parts.password:
        return "地址里带了用户名 / 密码"
    if parts.query:
        return "地址里带了查询参数（可能是 token）"
    if parts.fragment or "#" in url:
        return "地址里带了 # 片段（可能是 token）"
    return None


# ---------------------------------------------------------------------------
# 生成配置
# ---------------------------------------------------------------------------


def _toml(value: str) -> str:
    # JSON 字符串的转义规则是 TOML 基本字符串的子集
    return json.dumps(value, ensure_ascii=False)


def render_config(choices: InitChoices, *, today: str | None = None) -> str:
    from .logfile import clip_line
    from .redact import redact_log

    today = today or datetime.now().strftime("%Y-%m-%d")
    out = [
        f"# log-agent 项目配置（log-agent init 生成于 {today}）",
        "# 命令行参数 > 环境变量 > 本文件；相对路径以本文件所在目录为准。",
        "# API Key 不写在这里：请放在环境变量 OPENAI_API_KEY 里。",
        "",
        f"model = {_toml(choices.model)}",
    ]
    if choices.base_url:
        out.append(f"base_url = {_toml(choices.base_url)}")
    if choices.code:
        out.append("code = [" + ", ".join(_toml(c) for c in choices.code) + "]")
    if choices.timezone:
        out.append(f"timezone = {_toml(choices.timezone)}      # 日志时间不带时区时按这个时区解释")

    unknown = [s for s in choices.samples if s.unknown_ratio >= _CUSTOM_FORMAT_THRESHOLD and s.unknown_example]
    if choices.samples:
        out += ["", "# 抽样识别结果："]
        for sample in choices.samples:
            top = "、".join(f"{name} {ratio:.0%}" for name, ratio in sample.formats[:3]) or "空文件"
            out.append(f"#   {Path(sample.path).name}（{sample.encoding}）：{top}")
    if unknown:
        worst = max(unknown, key=lambda s: s.unknown_ratio)
        example = clip_line(redact_log(worst.unknown_example), 200).replace("\n", " ")
        out += [
            "",
            f"# {Path(worst.path).name} 有 {worst.unknown_ratio:.0%} 的行没认出来。按下面的模板写一个自定义格式",
            "# （去掉行首 # 生效；pattern 至少要有 (?P<time>…) 或 (?P<level>…) 分组，sample 会在启动时校验）：",
            "# [[log_formats]]",
            '# name = "my-app"',
            "# pattern = '^(?P<time>\\S+ \\S+) (?P<level>\\w+) (?P<message>.*)$'",
            '# time_format = "%Y-%m-%d %H:%M:%S"',
            '# levels = { E = "ERROR", W = "WARN", I = "INFO" }',
            f"# sample = {_toml(example)}",
        ]
    out += [
        "",
        "# 其它常用项（去掉行首 # 生效）：",
        '# skills = ["./ops/skills"]   # 额外的 skill 目录；.log-agent/skills 与 ~/.log-agent/skills 会自动加载',
        '# budget = "300k"             # 单轮 tokens 上限，用到 80% 时自动收尾出报告',
        "# timeout = 120               # 单次模型请求超时（秒）",
        "# max_retries = 3             # 模型请求失败自动重试次数",
        '# app_packages = ["com.acme"] # 业务代码包名前缀，异常链据此找业务栈帧',
        '# memory = "suggest"          # 长期记忆：suggest / explicit / off',
        "",
        "# [analyze]                   # 只对 analyze 生效",
        "# verbose = true",
        "",
    ]
    return "\n".join(out)


def validate_config_text(text: str) -> list[str]:
    """用真正的加载逻辑校验生成的配置，返回警告；有错误时抛 ConfigError。"""
    from . import logformat, stacktrace
    from .config import COMMANDS, LoadedConfig, _read, apply_log_settings

    formats, packages = logformat.custom_formats(), stacktrace.app_packages()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".log-agent.toml"
            path.write_text(text, encoding="utf-8")
            loaded = LoadedConfig()
            _read(path, loaded)
            for command in COMMANDS:
                apply_log_settings(loaded.for_command(command))
    finally:  # apply_log_settings 会改全局状态，校验完恢复原样
        logformat._custom = formats
        stacktrace.set_app_packages(packages)
    return [w.replace(str(path), ".log-agent.toml") for w in loaded.warnings]


# ---------------------------------------------------------------------------
# 交互
# ---------------------------------------------------------------------------


def _step(index: int, title: str) -> None:
    console.print()
    console.print(Text(f"{index}. {title}", style="accent.strong"))


def _ok(message: str) -> None:
    console.print(Text(f"  {glyphs.ok} {message}", style="ok"))


def _warn(message: str) -> None:
    console.print(Text(f"  {glyphs.fail} {message}", style="warn"))


def _note(message: str) -> None:
    console.print(Text(f"  {message}", style="muted"))


def key_hints() -> list[tuple[str, str]]:
    """可以直接复制执行的命令；说明文字放在 key_hint_note()，不要拼进命令里。"""
    if IS_WINDOWS:
        return [
            ("PowerShell", '$env:OPENAI_API_KEY="sk-..."'),
            ("CMD", "set OPENAI_API_KEY=sk-..."),
            ("永久生效", 'setx OPENAI_API_KEY "sk-..."'),
        ]
    return [("Shell", 'export OPENAI_API_KEY="sk-..."')]


def key_hint_note() -> str:
    if IS_WINDOWS:
        return "前两条只对当前终端生效；setx 写入用户环境变量，需要重开终端。"
    return "只对当前终端生效；写进 ~/.bashrc 或 ~/.zshrc 可以永久生效。"


def print_key_hints(indent: str = "  ") -> None:
    for label, command in key_hints():
        console.print(Text.assemble((indent, ""), (f"{label}: ", "muted"), (command, "accent")))
    console.print(Text(indent + key_hint_note(), style="muted"))


def _check_key(ask: bool) -> bool:
    from .probe import api_key, mask_key

    key = api_key()
    if key:
        _ok(f"OPENAI_API_KEY 已设置（{mask_key(key)}）")
        return True
    _warn("没有设置 OPENAI_API_KEY")
    print_key_hints("    ")
    if ask:
        entered = typer.prompt("  现在粘贴 Key 做一次验证（只用于本次检查，不会保存；回车跳过）",
                               default="", show_default=False, hide_input=True).strip()
        if entered:
            os.environ["OPENAI_API_KEY"] = entered
            _note("已用于本次检查。记得按上面的方式把它设到环境变量里，否则下次运行仍会缺 Key。")
            return True
    _note("Key 只放在环境变量里，init 不会把它写进配置文件。")
    return False


def _choose_base_url(default: str | None, given: str | None, ask: bool) -> tuple[str | None, bool]:
    """返回 (写进配置的网关地址, 显式给出的地址是否因为不安全被拒绝)。

    带凭据的地址既不写进文件，也不作为提示的默认值显示出来。
    """
    if default and unsafe_url_reason(default):
        _note("当前网关地址带凭据，保留在环境变量里（运行时仍会使用），不写入配置。")
        default = None
    value = given if given is not None else default
    explicit = given is not None
    if ask and given is None:
        value = typer.prompt("  OpenAI 兼容网关地址（回车使用官方接口）", default=default or "",
                             show_default=bool(default)).strip() or None
        explicit = bool(value) and value != default
    if not value:
        _ok("配置里不写 base_url")
        return None, False
    reason = unsafe_url_reason(value)
    if reason:
        _warn(f"不把网关地址写进配置：{reason}。请放在环境变量 OPENAI_BASE_URL 里。")
        return None, explicit
    _ok(f"网关：{value}")
    return value, False


def _available_models(base_url: str | None, has_key: bool) -> list[str] | None:
    if not has_key:
        return None
    from .probe import list_models

    try:
        with console.status("正在获取接口上可用的模型…", spinner=glyphs.spinner):
            return list_models(base_url)
    except Exception as exc:  # noqa: BLE001 — 网关不支持 /models 很常见，退回内置列表
        _note(f"获取模型列表失败（{type(exc).__name__}），改用常用模型列表。")
        return None


def _model_options(current: str, listed: list[str] | None) -> list[str]:
    if not listed:
        return list(dict.fromkeys([current, *PRESET_MODELS]))
    names = [f"openai:{name}" for name in listed]
    preferred = [m for m in dict.fromkeys([current, *PRESET_MODELS]) if m in names]
    rest = [m for m in names if m not in preferred]
    return (preferred + rest)[:_MAX_LISTED_MODELS]


def _normalize_model(name: str) -> str:
    name = name.strip()
    return name if ":" in name else f"openai:{name}"


def _choose_model(current: str, given: str | None, listed: list[str] | None, ask: bool) -> str:
    if given:
        model = _normalize_model(given)
    elif not ask:
        model = current
    else:
        options = _model_options(current, listed)
        source = "接口返回的模型" if listed else "常用模型"
        table = Table.grid(padding=(0, 2))
        for index, name in enumerate(options, start=1):
            mark = Text("（当前）", style="muted") if name == current else Text("")
            table.add_row(Text(f"  {index}", style="accent"), Text(name), mark)
        console.print(Text(f"  {source}：", style="muted"))
        console.print(table)
        raw = typer.prompt("  选择编号，或直接输入 provider:model", default="1").strip()
        model = options[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(options) else _normalize_model(raw)
    if listed and model.split(":", 1)[-1] not in listed:
        _warn(f"{model} 不在接口返回的模型列表里，请确认名称拼写。")
    _ok(f"模型：{model}")
    return model


def display_path(path: str | Path, base: Path) -> str:
    """给用户看 / 复制的路径：能相对就相对，统一用 /（Windows 上 Path 和 glob 也认 /）。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(base.resolve()).as_posix() or "."
    except ValueError:
        return resolved.as_posix()


def quote_arg(text: str, windows: bool | None = None) -> str:
    """交互式路径输入的引号；不用于生成 shell 命令。"""
    windows = IS_WINDOWS if windows is None else windows
    if text and not any(ch.isspace() or ch in "\"'`$&|;<>()" for ch in text):
        return text
    return f'"{text}"' if windows else shlex.quote(text)


def shell_arg(text: str, windows: bool | None = None) -> str | None:
    """只输出能同时安全用于 cmd 和 PowerShell 的参数，否则不生成命令。"""
    windows = IS_WINDOWS if windows is None else windows
    if windows:
        if any(ch in text for ch in '$`%!"\r\n'):
            return None
        return f'"{text}"'
    return shlex.quote(text)


def split_args(answer: str, windows: bool | None = None) -> list[str]:
    """拆分用户输入的多个路径。Windows 不按 POSIX 规则处理反斜杠，但要去掉包裹用的引号。"""
    windows = IS_WINDOWS if windows is None else windows
    try:
        tokens = shlex.split(answer, posix=not windows)
    except ValueError:  # 引号不成对：退回按空白拆
        tokens = answer.split()
    if windows:
        tokens = [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in tokens]
    return [t for t in tokens if t]


def _choose_logs(given: list[str] | None, cwd: Path, ask: bool) -> list[str]:
    from .inputs import LogInputError, resolve_log_inputs

    found = [display_path(p, cwd) for p in discover_logs(cwd)]
    raw = list(given or [])
    if not raw:
        if ask:
            answer = typer.prompt("  用来抽样的日志文件（多个用空格分隔，支持通配符；回车跳过）",
                                  default=" ".join(quote_arg(p) for p in found), show_default=bool(found))
            raw = split_args(answer)
        else:
            raw = found
    raw = [item for item in raw if item and item != "-"]
    if not raw:
        _note("没有抽样日志，跳过格式识别（之后可以用 log-agent inspect -l app.log 单独检查）。")
        return []
    try:
        return resolve_log_inputs(raw)
    except LogInputError as exc:
        _warn(str(exc))
        return []


def _show_samples(paths: list[str]) -> list:
    from .diagnostics import sample_log

    samples = []
    for path in paths:
        try:
            with console.status(f"正在抽样 {Path(path).name}…", spinner=glyphs.spinner):
                sample = sample_log(path)
        except (OSError, EOFError) as exc:
            _warn(f"读取失败：{path}：{exc}")
            continue
        samples.append(sample)
        top = "、".join(f"{name} {ratio:.0%}" for name, ratio in sample.formats[:3]) or "空文件"
        levels = " · ".join(f"{k} {v}" for k, v in sample.levels.most_common(4)) or "未识别到级别"
        poor = sample.unknown_ratio >= _CUSTOM_FORMAT_THRESHOLD
        (_warn if poor else _ok)(f"{Path(path).name}：{top}")
        _note(f"编码 {sample.encoding} · 前 {sample.lines} 行 · {levels}")
        if poor:
            _note(f"{sample.unknown_ratio:.0%} 的行没认出来，配置里会附一份自定义格式模板（log_formats），按提示填好 pattern 即可。")
    return samples


def _choose_timezone(samples: list, given: str | None, ask: bool) -> str | None:
    from .timefilter import set_default_timezone

    if given:
        value = given
    elif not any(s.needs_timezone for s in samples):
        if samples and any(s.aware_stamps for s in samples):
            _ok("日志时间自带时区，不需要设置 timezone")
        return None
    else:
        default = local_offset()
        value = default
        if ask:
            value = typer.prompt("  日志里的时间不带时区，按哪个时区解释？（如 +08:00、Asia/Shanghai、UTC）",
                                 default=default).strip()
    try:
        set_default_timezone(value)
    except ValueError as exc:
        _warn(f"时区无效：{exc}，不写入 timezone。")
        return None
    finally:
        set_default_timezone()
    _ok(f"时区：{value}")
    return value


def _choose_code(given: list[Path] | None, cwd: Path, ask: bool, base: Path) -> list[str]:
    """base：配置文件所在目录。配置里的相对路径以它为准，而不是当前目录。"""
    if given:
        code = [display_path(path.expanduser(), base) for path in given]
        _ok("源码目录：" + "、".join(code))
        return code
    if not looks_like_project(cwd):
        _note("当前目录不像代码仓库，不设置源码目录（之后可用 -c 指定）。")
        return []
    if ask and not typer.confirm("  把当前目录作为源码目录（code）？分析时会结合源码定位根因", default=True):
        return []
    value = display_path(cwd, base)
    _ok("源码目录：" + ("（当前目录）" if value == "." else value))
    return [value]


def _ping(model: str, base_url: str | None) -> bool:
    from .probe import endpoint_label, ping

    with console.status("正在发送一次最小请求验证模型接口…", spinner=glyphs.spinner):
        result = ping(model, base_url)
    if result.ok:
        served = f" · 实际模型 {result.served_model}" if result.served_model else ""
        _ok(f"模型接口可用：{model} @ {endpoint_label(base_url)} · {result.latency:.2f}s{served} · {result.message}")
    else:
        _warn(f"模型接口检查失败：{result.message}")
    return result.ok


def _config_target(cwd: Path) -> tuple[Path, bool]:
    """运行时会读的那份配置文件。LOG_AGENT_CONFIG 设置时运行时只读它，写项目里的 .log-agent.toml 会被忽略。"""
    from .config import PROJECT_FILE

    explicit = os.environ.get("LOG_AGENT_CONFIG", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        return (path if path.is_absolute() else cwd / path).resolve(), True
    return cwd / PROJECT_FILE, False


def _base_layer(target: Path) -> dict:
    """除了要生成的文件之外，运行时还会读到的配置（用户级 ~/.log-agent/config.toml）。"""
    from .config import LoadedConfig, _read, user_config_path

    user = user_config_path()
    if not user.is_file() or user.resolve() == target.resolve():
        return {}
    loaded = LoadedConfig()
    _read(user, loaded)
    return loaded.for_command("analyze")


def _runtime_value(env: str, written: str | None, base: dict, key: str) -> tuple[str | None, bool]:
    """按运行时的优先级（环境变量 > 本文件 > 用户级配置）算出实际会用的值；第二项表示是否被环境变量覆盖。"""
    # Match cli_defaults / analyze: nonempty overrides are used verbatim, including whitespace.
    override = os.environ.get(env, "")
    if override:
        return override, bool(written) and override != written
    return written or base.get(key), False


def run_init(
    *,
    logs: list[str] | None,
    model: str | None,
    base_url: str | None,
    code: list[Path] | None,
    timezone: str | None,
    ping: bool | None,
    yes: bool,
    force: bool,
    cwd: Path | None = None,
) -> int:
    """返回退出码：0 成功；1 生成了配置但显式要求的 --ping 失败；2 没有生成配置。"""
    from .cli import DEFAULT_MODEL
    from .config import ConfigError, load_config
    from .probe import endpoint_label

    cwd = (cwd or Path.cwd()).resolve()
    ask = not yes
    target, explicit_target = _config_target(cwd)
    console.print(Text("log-agent init · 生成配置 " + str(target), style="muted"))
    if explicit_target:
        _note(f"LOG_AGENT_CONFIG 已设置：运行时只读 {target}（以及用户级配置），不会读项目里的 .log-agent.toml，"
              "所以配置写到这个文件。不想这样的话先 unset LOG_AGENT_CONFIG 再运行 init。")
    if target.exists() and not force:
        if not ask:
            console.print(Text(f"{glyphs.fail} {target} 已存在；加 --force 覆盖。", style="err"))
            return 2
        if not typer.confirm(f"{target} 已存在，覆盖？", default=False):
            return 2
    try:
        base = _base_layer(target)
    except ConfigError as exc:
        _warn(f"现有配置读取失败：{exc}。请先修复用户级配置，再运行 init。")
        return 2
    try:
        existing = base if explicit_target and not target.exists() else load_config(cwd).for_command("analyze")
    except ConfigError as exc:
        _warn(f"现有配置读取失败，忽略：{exc}")
        existing = {}
    current_model = os.environ.get("LOG_AGENT_MODEL") or existing.get("model") or base.get("model") or DEFAULT_MODEL
    current_url = os.environ.get("OPENAI_BASE_URL") or existing.get("base_url") or base.get("base_url")

    _step(1, "检查 API Key")
    has_key = _check_key(ask)

    _step(2, "选择模型")
    chosen_url, rejected = _choose_base_url(current_url, base_url, ask)
    runtime_url, url_overridden = _runtime_value("OPENAI_BASE_URL", chosen_url, base, "base_url")
    if url_overridden:
        _warn(f"环境变量 OPENAI_BASE_URL 会覆盖配置里的 base_url，运行时实际连 {endpoint_label(runtime_url)}。")
    elif runtime_url != chosen_url:
        source = "环境变量 OPENAI_BASE_URL" if os.environ.get("OPENAI_BASE_URL", "") else "用户级配置"
        _note(f"运行时实际连 {endpoint_label(runtime_url)}（来自{source}）。")
    elif not runtime_url:
        _note("运行时使用官方接口。")
    if rejected:
        # 用户要的是刚才那个地址；不能拿 Key 去请求旧配置 / 官方接口这类别的地方
        _note("刚才的网关地址没有生效，跳过模型列表和连通性验证（不会把 Key 发到其它地址）。")
    probe_ok = has_key and not rejected
    listed = _available_models(runtime_url, probe_ok) if ask and not model else None
    chosen_model = _choose_model(current_model, model, listed, ask)
    runtime_model, model_overridden = _runtime_value("LOG_AGENT_MODEL", chosen_model, base, "model")
    runtime_model = runtime_model or chosen_model
    if model_overridden:
        _warn(f"环境变量 LOG_AGENT_MODEL={runtime_model} 会覆盖配置里的 model，运行时实际用 {runtime_model}。")

    pinged: bool | None = None
    if probe_ok and (ping or (ping is None and ask and typer.confirm("  发一次最小请求验证 Key 和模型？", default=True))):
        pinged = _ping(runtime_model, runtime_url)  # 验证运行时真正会用的模型和网关
    elif ping:
        _warn("没有 Key，跳过 --ping" if not has_key else "网关地址未生效，跳过 --ping")
        pinged = False

    _step(3, "抽样日志识别格式")
    samples = _show_samples(_choose_logs(logs, cwd, ask))
    chosen_tz = _choose_timezone(samples, timezone, ask)

    _step(4, "源码目录")
    chosen_code = _choose_code(code, cwd, ask, target.parent)

    choices = InitChoices(chosen_model, chosen_url, chosen_code, chosen_tz, samples)
    text = render_config(choices)
    try:
        warnings = validate_config_text(text)
    except ConfigError as exc:  # 生成逻辑有问题，不写出坏文件
        console.print(Text(f"{glyphs.fail} 生成的配置没通过校验：{exc}", style="err"))
        return 2
    for warning in warnings:
        _warn(warning)

    _step(5, f"写入 {target.name}")
    console.print(Syntax(text, "toml", theme="ansi_dark", background_color="default", word_wrap=True))
    if ask and not typer.confirm(f"写入 {target}？", default=True):
        _note("已取消，没有写入任何文件。")
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    console.print(Text.assemble((f"{glyphs.ok} 已生成 ", "ok"), (str(target), "accent")))

    console.print()
    console.print(Text("下一步：", style="accent.strong"))
    if not has_key:
        console.print(Text("  先设置 OPENAI_API_KEY（见上面的命令）", style="warn"))
    if not pinged:
        console.print(Text.assemble(("  log-agent doctor --ping", "accent"), ("      验证 Key 和模型接口", "muted")))
    best = min(samples, key=lambda s: s.unknown_ratio) if samples else None
    sample = shell_arg(display_path(best.path, cwd)) if best else "app.log"
    if sample is None:
        _note("日志文件名包含 shell 特殊字符，请先重命名文件，再使用 log-agent analyze -l 指定日志。")
    else:
        console.print(Text.assemble((f"  log-agent analyze -l {sample}", "accent"), ("      开始分析", "muted")))
    return 1 if ping and pinged is False else 0
