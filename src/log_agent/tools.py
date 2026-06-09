"""自定义工具：让 agent 能安全地读取日志文件、检索源码、执行只读 shell 命令。"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 搜索时跳过的目录（跨平台，纯 Python 实现，不依赖外部 grep 命令）
SKIP_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__",
    "dist", "build", ".next", ".idea", ".mypy_cache", ".pytest_cache",
}

# 只检索这些代码 / 文本扩展名（白名单），其它一律跳过。
# 比黑名单更可靠：真实仓库里的二进制类型五花八门，列举永远不全；
# 反过来只关注代码文件，既快又不会误读二进制。
CODE_EXTENSIONS = {
    # 常见编程语言
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".java", ".kt", ".kts", ".scala", ".groovy",
    ".go", ".rs", ".rb", ".php", ".pl", ".pm",
    ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".hxx",
    ".cs", ".swift", ".m", ".mm", ".dart", ".lua", ".r",
    ".sql", ".sh", ".bash", ".zsh", ".ps1", ".bat", ".cmd",
    # 配置 / 标记 / 文本
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".html", ".htm", ".css", ".scss", ".sass", ".less",
    ".md", ".txt", ".env", ".properties", ".gradle",
    ".vue", ".svelte", ".astro",
    ".tf", ".tfvars", ".dockerfile",
}

# 没有扩展名但通常是文本的常见文件名（如 Dockerfile、Makefile）
CODE_FILENAMES = {
    "dockerfile", "makefile", "rakefile", "gemfile", "procfile",
    ".gitignore", ".dockerignore", ".env",
}


def _is_code_file(name: str) -> bool:
    """判断是否是值得检索的代码 / 文本文件（白名单）。"""
    lower = name.lower()
    if lower in CODE_FILENAMES:
        return True
    return Path(name).suffix.lower() in CODE_EXTENSIONS


# 搜索 / 读取源码文件时的单文件大小上限（字节），超过则跳过，避免卡死或内存暴涨
MAX_SEARCH_FILE_SIZE = 5 * 1024 * 1024  # 5 MB

# Windows 保留设备名：os.walk 可能列出同名文件/junction，
# 一旦传给 os.path.relpath/abspath 会被解析成 \\.\nul 之类的设备路径，
# 与正常盘符不在同一挂载点，导致 ValueError。这里直接跳过它们。
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def _safe_relpath(root: str, name: str, base: Path) -> str | None:
    """计算 base 下某文件的相对路径，跨平台安全。

    使用纯词法运算（PurePath.relative_to），不触碰文件系统，也不会把
    Windows 保留名（nul/con/...）解析成设备路径。无法计算时返回 None。
    """
    # 跳过 Windows 保留设备名（忽略扩展名，如 nul.txt 也按 nul 处理）
    if os.name == "nt" and name.split(".")[0].lower() in _WINDOWS_RESERVED:
        return None
    try:
        return str((Path(root) / name).relative_to(base))
    except ValueError:
        return None


def read_log_chunk(path: str, start_line: int = 1, num_lines: int = 500) -> str:
    """读取日志文件的指定行区间。

    日志通常很大，一次性读入会爆掉上下文。用这个工具按行区间分块读取。

    Args:
        path: 日志文件的绝对或相对路径。
        start_line: 起始行号（从 1 开始）。
        num_lines: 读取的行数，默认 500。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"[错误] 日志文件不存在: {path}"

    lines: list[str] = []
    end_line = start_line + num_lines - 1
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if i < start_line:
                    continue
                if i > end_line:
                    break
                lines.append(f"{i}: {line.rstrip()}")
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"

    if not lines:
        return f"[提示] 第 {start_line} 行之后没有内容（文件可能已读完）。"
    header = f"--- {path} 第 {start_line}-{start_line + len(lines) - 1} 行 ---"
    return header + "\n" + "\n".join(lines)


def search_logs(path: str, pattern: str, max_results: int = 100) -> str:
    """在日志文件中按关键字/正则搜索，返回命中的行号与内容。

    用于快速定位 ERROR、Exception、Traceback、特定 request id 等。

    Args:
        path: 日志文件路径。
        pattern: 要搜索的字符串或正则表达式。
        max_results: 最多返回的命中行数。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return f"[错误] 日志文件不存在: {path}"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"[错误] 正则表达式无效: {exc}"

    matches: list[str] = []
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f, start=1):
                if regex.search(line):
                    matches.append(f"{i}: {line.rstrip()}")
                    if len(matches) > max_results:
                        break
    except OSError as exc:
        return f"[错误] 搜索失败: {exc}"

    if not matches:
        return f"[提示] 未匹配到 '{pattern}'。"
    truncated = matches[:max_results]
    note = "" if len(matches) <= max_results else f"\n... 命中超过 {max_results} 条，仅显示前 {max_results} 条。"
    return "\n".join(truncated) + note


def list_code_files(code_dir: str, max_files: int = 300) -> str:
    """列出源码目录下的文件结构（自动跳过常见无关目录）。

    Args:
        code_dir: 源码目录路径。
        max_files: 最多列出的文件数量。
    """
    base = Path(code_dir).expanduser()
    if not base.is_dir():
        return f"[错误] 代码目录不存在: {code_dir}"

    results: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            # 只列代码 / 文本文件（白名单），跳过图片、二进制等无关文件
            if not _is_code_file(name):
                continue
            rel = _safe_relpath(root, name, base)
            if rel is None:
                # 跳过无法计算相对路径的特殊条目（如 Windows 保留设备名 nul/con 等）
                continue
            results.append(rel)
            if len(results) >= max_files:
                results.append(f"... 已达上限 {max_files} 个文件，省略其余。")
                return "\n".join(results)
    if not results:
        return f"[提示] {code_dir} 下没有文件。"
    return "\n".join(sorted(results))


def read_code_file(code_dir: str, rel_path: str, max_chars: int = 20000) -> str:
    """读取源码目录中的某个文件内容。

    Args:
        code_dir: 源码根目录。
        rel_path: 相对于根目录的文件路径。
        max_chars: 最多返回的字符数，防止超大文件爆上下文。
    """
    base = Path(code_dir).expanduser().resolve()
    target = (base / rel_path).resolve()
    # 防止路径穿越读到目录之外的文件（跨平台安全）
    if not target.is_relative_to(base):
        return f"[错误] 非法路径（越界）: {rel_path}"
    if not target.is_file():
        return f"[错误] 文件不存在: {rel_path}"
    # 读取前先检查体积，避免把超大文件整个加载进内存（截断是读完之后才发生的）
    try:
        size = target.stat().st_size
    except OSError as exc:
        return f"[错误] 无法访问文件: {exc}"
    if size > MAX_SEARCH_FILE_SIZE:
        return (
            f"[错误] 文件过大（{size} 字节，上限 {MAX_SEARCH_FILE_SIZE} 字节），"
            f"已拒绝读取。如需查看，请用 grep_code 检索关键字。"
        )
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"[错误] 读取失败: {exc}"
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n... [已截断，文件共 {len(content)} 字符]"
    return f"--- {rel_path} ---\n{content}"


def grep_code(code_dir: str, pattern: str, max_results: int = 80) -> str:
    """在源码目录中递归搜索关键字/正则，返回 文件:行号:内容。

    用于把日志里的报错信息（函数名、错误字符串、变量名）关联回源码位置。

    Args:
        code_dir: 源码根目录。
        pattern: 搜索的字符串或正则。
        max_results: 最多返回的命中条数。
    """
    base = Path(code_dir).expanduser()
    if not base.is_dir():
        return f"[错误] 代码目录不存在: {code_dir}"
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        return f"[错误] 正则表达式无效: {exc}"

    matches: list[str] = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            fpath = Path(root) / name
            rel = _safe_relpath(root, name, base)
            if rel is None:
                # 跳过无法计算相对路径的特殊条目（如 Windows 保留设备名 nul/con 等）
                continue
            # 只检索代码 / 文本文件（白名单），跳过图片、二进制、压缩包等
            if not _is_code_file(name):
                continue
            # 跳过超大文件，避免逐行读取时卡死或内存暴涨
            try:
                if fpath.stat().st_size > MAX_SEARCH_FILE_SIZE:
                    continue
            except OSError:
                continue
            try:
                with fpath.open("r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, start=1):
                        if regex.search(line):
                            matches.append(f"{rel}:{i}: {line.rstrip()}")
                            if len(matches) > max_results:
                                break
            except (OSError, UnicodeError):
                # 跳过二进制文件或无法读取的文件
                continue
            if len(matches) > max_results:
                break
        if len(matches) > max_results:
            break

    if not matches:
        return f"[提示] 源码中未匹配到 '{pattern}'。"
    truncated = matches[:max_results]
    note = "" if len(matches) <= max_results else f"\n... 命中超过 {max_results} 条，仅显示前 {max_results} 条。"
    return "\n".join(truncated) + note


ALL_TOOLS = [
    read_log_chunk,
    search_logs,
    list_code_files,
    read_code_file,
    grep_code,
]
