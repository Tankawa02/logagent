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
            rel = os.path.relpath(os.path.join(root, name), base)
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
            try:
                with fpath.open("r", encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, start=1):
                        if regex.search(line):
                            rel = os.path.relpath(fpath, base)
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
