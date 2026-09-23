"""自定义工具：让 agent 能安全地读取日志文件、检索源码（全部只读、纯 Python、跨平台）。

约定：成功时返回正文；失败以 `[错误]` 开头，无结果以 `[提示]` 开头（展示层据此判断状态）。
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import time
from collections import Counter, deque
from pathlib import Path

from .logfile import MAX_LINE_CHARS, clip_line, open_log, read_text_file
from .redact import redact_code, redact_log

# 搜索时跳过的目录
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".idea", ".mypy_cache", ".pytest_cache",
}

# 只检索这些代码 / 文本扩展名（白名单），其它一律跳过。
# 比黑名单更可靠：真实仓库里的二进制类型五花八门，列举永远不全。
CODE_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".scala", ".groovy",
    ".go", ".rs", ".rb", ".php", ".pl", ".pm",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx",
    ".cs", ".swift", ".m", ".mm", ".dart", ".lua", ".r",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".md", ".txt", ".env", ".properties", ".gradle",
    ".vue", ".svelte", ".astro",
    ".tf", ".tfvars", ".dockerfile",
}

CODE_FILENAMES = {
    "dockerfile", "makefile", "rakefile", "gemfile", "procfile",
    ".gitignore", ".dockerignore", ".env",
}

MAX_SEARCH_FILE_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_CHUNK_LINES = 1000
MAX_CODE_LINES = 400
MAX_CONTEXT_LINES = 10

# Windows 保留设备名：os.walk 可能列出同名文件/junction，传给 relpath/abspath 会被解析成
# \\.\nul 之类的设备路径并抛 ValueError，直接跳过。
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _is_code_file(name: str) -> bool:
    lower = name.lower()
    if lower in CODE_FILENAMES:
        return True
    return Path(name).suffix.lower() in CODE_EXTENSIONS


def _safe_relpath(root: str, name: str, base: Path) -> str | None:
    """纯词法计算相对路径，不触碰文件系统，也不会把 Windows 保留名解析成设备路径。"""
    if os.name == "nt" and name.split(".")[0].lower() in _WINDOWS_RESERVED:
        return None
    try:
        return str((Path(root) / name).relative_to(base))
    except ValueError:
        return None


def _compile(pattern: str, regex: bool, ignore_case: bool) -> tuple[re.Pattern[str], str]:
    """编译搜索模式。正则无效时自动退化为普通文本搜索，而不是直接报错让模型重试。"""
    flags = re.IGNORECASE if ignore_case else 0
    if not regex:
        return re.compile(re.escape(pattern), flags), ""
    try:
        return re.compile(pattern, flags), ""
    except re.error:
        return re.compile(re.escape(pattern), flags), "（正则无效，已按普通文本搜索）"


def _open_log_or_error(path: str):
    try:
        return open_log(path), None
    except FileNotFoundError:
        return None, f"[错误] 日志文件不存在: {path}"
    except OSError as exc:
        return None, f"[错误] 无法打开日志: {exc}"


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


# ---------------------------------------------------------------------------
# 日志工具
# ---------------------------------------------------------------------------

_LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|SEVERE|ERROR|ERR|WARNING|WARN|INFO|DEBUG|TRACE)\b")
_LEVEL_ALIAS = {"CRITICAL": "FATAL", "SEVERE": "FATAL", "ERR": "ERROR", "WARNING": "WARN"}
_LEVEL_ORDER = ("FATAL", "ERROR", "WARN", "INFO", "DEBUG", "TRACE")
_TS_RE = re.compile(
    r"\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?"
    r"|\b\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?\b"
)
_EXC_RE = re.compile(r"\b((?:[a-z_][\w$]*\.)*[A-Z][\w$]*(?:Exception|Error))\b")
_NORMALIZE = [
    (re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"), "<uuid>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b[0-9a-fA-F]{12,}\b"), "<hex>"),
    (re.compile(r"\d+"), "#"),
]


def _error_signature(line: str, level_end: int) -> str:
    """把一条错误日志归一化成"签名"：去掉时间戳、数字、id，同类错误才能聚到一起。"""
    body = line[level_end:].lstrip(" ]:|-\t")
    for pattern, repl in _NORMALIZE:
        body = pattern.sub(repl, body)
    return body[:160].strip()


def log_overview(path: str) -> str:
    """快速了解整份日志：行数、大小、编码、时间范围、各级别数量、高频错误与异常类型。

    建议作为排查的第一步调用，先掌握全局再决定搜索什么。大文件会扫描一遍全文，
    同时建立行索引，之后的 read_log_chunk 跳读会更快。

    Args:
        path: 日志文件路径。
    """
    log, error = _open_log_or_error(path)
    if error:
        return error

    levels: Counter[str] = Counter()
    signatures: Counter[str] = Counter()
    first_seen: dict[str, int] = {}
    exceptions: Counter[str] = Counter()
    exc_first: dict[str, int] = {}
    first_ts = last_ts = ""
    total = 0

    try:
        for lineno, line in log.iter_lines(1):
            total = lineno
            if len(line) > 4000:
                line = line[:4000]
            ts = _TS_RE.search(line[:80])
            if ts:
                if not first_ts:
                    first_ts = ts.group(0)
                last_ts = ts.group(0)
            level_match = _LEVEL_RE.search(line[:200])
            if level_match:
                level = _LEVEL_ALIAS.get(level_match.group(1), level_match.group(1))
                levels[level] += 1
                if level in ("FATAL", "ERROR"):
                    sig = _error_signature(line, level_match.end())
                    if sig:
                        signatures[sig] += 1
                        first_seen.setdefault(sig, lineno)
            for exc in set(_EXC_RE.findall(line)):
                exceptions[exc] += 1
                exc_first.setdefault(exc, lineno)
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"

    if total == 0:
        return f"[提示] 日志为空: {path}"

    meta = [f"大小 {_human_size(log.size)}", f"共 {total:,} 行", f"编码 {log.encoding}"]
    if log.gz:
        meta.append("gzip 压缩")
    out = [f"--- 日志概览 {log.path.name} ---", " · ".join(meta)]
    if first_ts:
        out.append(f"时间范围：{first_ts} → {last_ts}")
    if levels:
        ordered = [f"{name} {levels[name]:,}" for name in _LEVEL_ORDER if levels[name]]
        out.append("级别分布：" + " · ".join(ordered))
    else:
        out.append("级别分布：未识别到标准日志级别")

    if signatures:
        out.append("")
        out.append("高频错误（数字 / id 已归一化，按次数排序）：")
        for i, (sig, count) in enumerate(signatures.most_common(10), start=1):
            out.append(f"  {i}. x{count}  首次 L{first_seen[sig]}  {redact_log(clip_line(sig, 200))}")
    if exceptions:
        out.append("")
        out.append("异常类型：")
        for exc, count in exceptions.most_common(10):
            out.append(f"  {exc}  x{count}  首次 L{exc_first[exc]}")
    return "\n".join(out)


def read_log_chunk(path: str, start_line: int = 1, num_lines: int = 200) -> str:
    """读取日志文件的指定行区间（带行号）。

    日志通常很大，不要试图一次读完。单行超过 500 字会被截断。

    Args:
        path: 日志文件路径。
        start_line: 起始行号（从 1 开始）。
        num_lines: 读取的行数，默认 200，最多 1000。
    """
    log, error = _open_log_or_error(path)
    if error:
        return error
    start_line = max(1, int(start_line))
    num_lines = max(1, min(int(num_lines), MAX_CHUNK_LINES))
    end_line = start_line + num_lines - 1

    lines: list[str] = []
    try:
        for lineno, text in log.iter_lines(start_line):
            if lineno > end_line:
                break
            lines.append(f"{lineno}: {clip_line(text)}")
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"

    if not lines:
        total = f"（文件共 {log.total_lines:,} 行）" if log.total_lines is not None else ""
        return f"[提示] 第 {start_line} 行之后没有内容{total}。"
    last = start_line + len(lines) - 1
    total = f"，共 {log.total_lines:,} 行" if log.total_lines is not None else ""
    header = f"--- {path} 第 {start_line}-{last} 行{total} ---"
    return header + "\n" + redact_log("\n".join(lines))


def search_logs(
    path: str,
    pattern: str,
    regex: bool = True,
    ignore_case: bool = False,
    context: int = 0,
    start_line: int = 1,
    end_line: int = 0,
    max_results: int = 50,
) -> str:
    """在日志中搜索关键字/正则，返回命中行（`行号: 内容`）及可选的上下文行（`行号- 内容`）。

    用于快速定位 ERROR、Exception、Traceback、特定 request id 等。

    Args:
        path: 日志文件路径。
        pattern: 搜索模式。默认按正则解释，如 `ERROR|Exception`。
        regex: 为 False 时按普通文本搜索。搜索含 `[ ] ( ) . * ?` 的原文（如 `[ERROR]`、
            `foo(bar)`）时务必传 False，否则会被当成正则元字符。
        ignore_case: 是否忽略大小写。
        context: 每个命中额外返回前后各几行上下文（0-10），省去再调 read_log_chunk。
        start_line: 只搜索从该行开始的内容。
        end_line: 只搜索到该行为止，0 表示到文件末尾。
        max_results: 最多返回的命中数。
    """
    log, error = _open_log_or_error(path)
    if error:
        return error
    compiled, note = _compile(pattern, regex, ignore_case)
    context = max(0, min(int(context), MAX_CONTEXT_LINES))
    max_results = max(1, int(max_results))
    end_line = int(end_line or 0)

    out: list[str] = []
    before: deque[tuple[int, str]] = deque(maxlen=context or 1)
    after_left = 0
    last_printed = 0
    hits = 0
    truncated = False
    try:
        for lineno, text in log.iter_lines(max(1, int(start_line))):
            if end_line and lineno > end_line:
                break
            if compiled.search(text):
                if hits >= max_results:
                    truncated = True
                    break
                hits += 1
                if context:
                    first = before[0][0] if before else lineno
                    if out and first > last_printed + 1:
                        out.append("--")
                    out.extend(f"{no}- {clip_line(t)}" for no, t in before)
                    before.clear()
                out.append(f"{lineno}: {clip_line(text)}")
                last_printed = lineno
                after_left = context
            elif after_left:
                out.append(f"{lineno}- {clip_line(text)}")
                last_printed = lineno
                after_left -= 1
            elif context:
                before.append((lineno, text))
    except OSError as exc:
        return f"[错误] 搜索失败: {exc}"

    if not hits:
        return f"[提示] 未匹配到 '{pattern}'{note}。"
    body = redact_log("\n".join(out))
    tail = f"\n... 命中超过 {max_results} 条，仅显示前 {max_results} 条（可缩小行范围或换更精确的模式）。" if truncated else ""
    return (f"{note}\n" if note else "") + body + tail


# ---------------------------------------------------------------------------
# 源码工具
# ---------------------------------------------------------------------------

_FILE_LIST_TTL = 60.0
_file_list_cache: dict[str, tuple[float, list[str]]] = {}


def _code_files(base: Path) -> list[str]:
    """列出 base 下全部代码文件的相对路径（已排序），60 秒内复用结果。"""
    key = str(base.resolve())
    cached = _file_list_cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < _FILE_LIST_TTL:
        return cached[1]
    results: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if not _is_code_file(name):
                continue
            rel = _safe_relpath(root, name, base)
            if rel is not None:
                results.append(rel)
    results.sort()
    _file_list_cache[key] = (now, results)
    return results


def list_code_files(code_dir: str, path_glob: str = "", max_files: int = 300) -> str:
    """列出源码目录下的代码文件（自动跳过 node_modules、.git 等无关目录）。

    Args:
        code_dir: 源码目录路径。
        path_glob: 可选的路径过滤，如 `*.java`、`src/order/*`。
        max_files: 最多列出的文件数量。
    """
    base = Path(code_dir).expanduser()
    if not base.is_dir():
        return f"[错误] 代码目录不存在: {code_dir}"
    files = _code_files(base)
    if path_glob:
        files = [f for f in files if _glob_match(f, path_glob)]
    if not files:
        return f"[提示] {code_dir} 下没有匹配的代码文件。"
    shown = files[:max_files]
    tail = f"\n... 共 {len(files)} 个文件，已达上限 {max_files}，可用 path_glob 缩小范围。" if len(files) > max_files else ""
    return "\n".join(shown) + tail


def _glob_match(rel: str, pattern: str) -> bool:
    normalized = rel.replace("\\", "/")
    pattern = pattern.replace("\\", "/")
    return fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(normalized.rsplit("/", 1)[-1], pattern)


def read_code_file(code_dir: str, rel_path: str, start_line: int = 1, end_line: int = 0) -> str:
    """读取源码文件的指定行区间，每行带行号（报告里引用 `文件:行号` 时以此为准）。

    不指定 end_line 时最多返回 400 行；大文件请结合 grep_code 的行号按区间读取。

    Args:
        code_dir: 源码根目录。
        rel_path: 相对于根目录的文件路径。
        start_line: 起始行号（从 1 开始）。
        end_line: 结束行号（含），0 表示从 start_line 起读 400 行。
    """
    base = Path(code_dir).expanduser().resolve()
    target = (base / rel_path).resolve()
    if not target.is_relative_to(base):
        return f"[错误] 非法路径（越界）: {rel_path}"
    if not target.is_file():
        return f"[错误] 文件不存在: {rel_path}"
    try:
        size = target.stat().st_size
    except OSError as exc:
        return f"[错误] 无法访问文件: {exc}"
    if size > MAX_SEARCH_FILE_SIZE:
        return (
            f"[错误] 文件过大（{_human_size(size)}，上限 {_human_size(MAX_SEARCH_FILE_SIZE)}），"
            f"已拒绝读取。请用 grep_code 定位行号后再按区间读取。"
        )
    try:
        all_lines = read_text_file(target).splitlines()
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"

    total = len(all_lines)
    if total == 0:
        return f"[提示] 文件为空: {rel_path}"
    start = max(1, int(start_line))
    if start > total:
        return f"[提示] 第 {start} 行超出文件范围（共 {total} 行）。"
    end = int(end_line or 0)
    end = min(total, end if end >= start else start + MAX_CODE_LINES - 1)
    end = min(end, start + MAX_CODE_LINES * 2 - 1)

    width = len(str(end))
    body = "\n".join(
        f"{no:>{width}} | {clip_line(all_lines[no - 1], MAX_LINE_CHARS)}" for no in range(start, end + 1)
    )
    header = f"--- {rel_path} 第 {start}-{end} 行，共 {total} 行 ---"
    tail = f"\n... 还有 {total - end} 行未显示，用 start_line={end + 1} 继续读取。" if end < total else ""
    return header + "\n" + redact_code(body) + tail


def _grep_with_rg(base: Path, compiled_src: str, literal: bool, ignore_case: bool, path_glob: str, max_results: int) -> list[str] | None:
    """用 ripgrep 加速检索；rg 不可用、正则语法不兼容或出错时返回 None，交给纯 Python 实现。"""
    rg = shutil.which("rg")
    if not rg or os.environ.get("LOG_AGENT_NO_RG"):
        return None
    args = [
        rg, "--no-heading", "--line-number", "--color", "never", "--no-messages",
        "--sort", "path", "--max-filesize", "5M", "--max-columns", str(MAX_LINE_CHARS + 50),
    ]
    if literal:
        args.append("--fixed-strings")
    if ignore_case:
        args.append("--ignore-case")
    for ext in sorted(CODE_EXTENSIONS):
        args += ["--iglob", f"*{ext}"]
    for name in sorted(CODE_FILENAMES):
        args += ["--iglob", name]
    for skip in sorted(SKIP_DIRS):
        args += ["--glob", f"!{skip}/"]
    # path_glob 不交给 rg：rg 的多个正向 glob 是并集，会绕过上面的扩展名白名单，改为下面逐行过滤
    # 必须显式给出搜索路径：stdin 不是终端时 rg 会改为搜索 stdin
    args += ["-e", compiled_src, "--", "."]

    try:
        proc = subprocess.Popen(
            args, cwd=base, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, encoding="utf-8", errors="replace",
        )
    except OSError:
        return None

    matches: list[str] = []
    assert proc.stdout is not None
    try:
        for raw in proc.stdout:
            m = re.match(r"^(.*?):(\d+):(.*)$", raw.rstrip("\r\n"))
            if not m:
                continue
            rel, lineno, text = m.groups()
            rel = rel.removeprefix("./").removeprefix(".\\")
            if path_glob and not _glob_match(rel, path_glob):
                continue
            matches.append(f"{rel}:{lineno}: {clip_line(text)}")
            if len(matches) > max_results:
                break
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    # 退出码 2 = rg 报错（通常是正则语法与 Python 不兼容），回退
    if proc.returncode == 2 and not matches:
        return None
    return matches


def grep_code(
    code_dir: str,
    pattern: str,
    regex: bool = True,
    ignore_case: bool = False,
    path_glob: str = "",
    max_results: int = 80,
) -> str:
    """在源码目录中递归搜索关键字/正则，返回 `文件:行号: 内容`。

    用于把日志里的报错信息（函数名、错误字符串、异常类名）关联回源码位置。

    Args:
        code_dir: 源码根目录。
        pattern: 搜索模式，默认按正则解释。
        regex: 为 False 时按普通文本搜索（搜索含括号、点号的原文时建议 False）。
        ignore_case: 是否忽略大小写。
        path_glob: 可选的路径过滤，如 `*.java`、`src/order/*`。
        max_results: 最多返回的命中条数。
    """
    base = Path(code_dir).expanduser()
    if not base.is_dir():
        return f"[错误] 代码目录不存在: {code_dir}"
    compiled, note = _compile(pattern, regex, ignore_case)
    literal = not regex or bool(note)

    matches = _grep_with_rg(base, pattern, literal, ignore_case, path_glob, max_results)
    if matches is None:
        matches = []
        for rel in _code_files(base):
            if path_glob and not _glob_match(rel, path_glob):
                continue
            fpath = base / rel
            try:
                if fpath.stat().st_size > MAX_SEARCH_FILE_SIZE:
                    continue
                content = read_text_file(fpath)
            except OSError:
                continue
            for i, line in enumerate(content.splitlines(), start=1):
                if compiled.search(line):
                    matches.append(f"{rel}:{i}: {clip_line(line)}")
                    if len(matches) > max_results:
                        break
            if len(matches) > max_results:
                break

    if not matches:
        return f"[提示] 源码中未匹配到 '{pattern}'{note}。"
    shown = matches[:max_results]
    tail = f"\n... 命中超过 {max_results} 条，仅显示前 {max_results} 条（可用 path_glob 缩小范围）。" if len(matches) > max_results else ""
    return (f"{note}\n" if note else "") + redact_code("\n".join(shown)) + tail


ALL_TOOLS = [
    log_overview,
    read_log_chunk,
    search_logs,
    list_code_files,
    read_code_file,
    grep_code,
]
