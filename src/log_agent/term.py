"""终端能力探测与跨平台适配（Windows / macOS / Linux）。

要解决的几个跨平台问题：

1. **Windows 经典控制台（conhost）默认没开 VT 模式**：Rich 只会“探测”不会“开启”，
   探测失败就退回 Win32 API 逐段写屏，Live 刷新会明显闪烁。这里主动开启 VT。
2. **输出被重定向时的编码**：Windows 下 `> out.txt` 或管道时 stdout 走系统代码页
   （中文系统是 cp936/GBK），遇到 ✓ ▸ 等符号直接 UnicodeEncodeError。统一改成
   UTF-8 + errors=replace，永不因为一个符号崩掉整次分析。
3. **东亚歧义宽度字符**：中文 Windows 的 conhost（非 65001 代码页）会把 ─ ○ ✓ 等
   “歧义宽度”字符画成双宽，而 Rich 按单宽计算，导致边框错位、Live 回退行数算错、
   残影叠行。这类终端自动切到纯 ASCII 字形集。可用环境变量强制：
   `LOG_AGENT_GLYPHS=ascii` / `LOG_AGENT_GLYPHS=unicode`。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from rich import box
from rich.console import Console
from rich.theme import Theme

IS_WINDOWS = os.name == "nt"

# 这些环境变量由“现代终端”设置：它们支持 VT、字体回退完整、歧义字符按单宽渲染
_MODERN_TERMINAL_ENV = (
    "WT_SESSION",  # Windows Terminal
    "TERM_PROGRAM",  # VS Code / iTerm2 / Hyper / WezTerm 等
    "ConEmuANSI",  # ConEmu / Cmder
    "ALACRITTY_WINDOW_ID",
    "WEZTERM_EXECUTABLE",
    "TERMINAL_EMULATOR",  # JetBrains 内置终端
)


@dataclass(frozen=True)
class Glyphs:
    """一整套界面字形。Unicode 版给现代终端，ASCII 版给经典 conhost / 非 UTF-8 环境。"""

    unicode: bool
    spinner: str
    log: str
    code: str
    other: str
    ok: str
    fail: str
    notice: str
    prompt: str
    sep: str
    up: str
    down: str
    ellipsis: str
    cursor: str
    rule: str
    branch: str
    box: box.Box


UNICODE_GLYPHS = Glyphs(
    unicode=True,
    spinner="dots",
    log="≡",
    code="◆",
    other="•",
    ok="✓",
    fail="✗",
    # 用 ▸ 而不是 ▶：后者带 emoji 变体，部分终端会画成双宽彩色图标
    notice="▸",
    prompt="❯",
    sep="·",
    up="↑",
    down="↓",
    ellipsis="…",
    cursor="▍",
    rule="─",
    branch="└",
    box=box.ROUNDED,
)

ASCII_GLYPHS = Glyphs(
    unicode=False,
    spinner="line",
    log="#",
    code="*",
    other="-",
    ok="+",
    fail="x",
    notice="[>]",
    prompt=">",
    sep="|",
    up="in ",
    down="out ",
    ellipsis="...",
    cursor="_",
    rule="-",
    branch="`-",
    box=box.ASCII,
)

THEME = Theme(
    {
        "accent": "cyan",
        "accent.strong": "bold cyan",
        "muted": "bright_black",
        "ok": "green",
        "err": "red",
        "warn": "yellow",
        "tool.log": "yellow",
        # 经典 conhost 的深蓝在黑底上几乎看不清，统一用亮蓝
        "tool.code": "bright_blue",
        "tool.other": "cyan",
        "shimmer.base": "cyan",
        "shimmer.edge": "bold cyan",
        "shimmer.peak": "bold bright_cyan",
    }
)


def _enable_windows_vt() -> None:
    """在 Windows 控制台上开启 VT（ANSI 转义）处理，让 Rich 走高效的 ANSI 渲染路径。"""
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        for std_handle in (-11, -12):  # STD_OUTPUT_HANDLE, STD_ERROR_HANDLE
            handle = kernel32.GetStdHandle(std_handle)
            mode = wintypes.DWORD()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                kernel32.SetConsoleMode(handle, mode.value | enable_vt)
    except Exception:
        # 老系统（Win7/8）不支持 VT，Rich 会自动退回 legacy 渲染，功能不受影响
        pass


def _windows_console_is_utf8() -> bool:
    try:
        import ctypes

        return ctypes.windll.kernel32.GetConsoleOutputCP() == 65001  # type: ignore[attr-defined]
    except Exception:
        return False


def _harden_std_streams() -> None:
    """保证任何字符都能写出去：终端保持原编码只放宽错误处理，重定向时统一 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty():
                stream.reconfigure(errors="replace")
            else:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _detect_unicode() -> bool:
    forced = os.environ.get("LOG_AGENT_GLYPHS", "").strip().lower()
    if forced in {"ascii", "safe", "plain"}:
        return False
    if forced in {"unicode", "utf8", "utf-8", "fancy"}:
        return True

    stdout = sys.stdout
    if not getattr(stdout, "isatty", lambda: False)():
        # 重定向到文件 / 管道：已统一成 UTF-8，Unicode 安全
        return True

    if not IS_WINDOWS:
        encoding = (getattr(stdout, "encoding", "") or "").lower().replace("-", "")
        return encoding.startswith("utf")

    if any(os.environ.get(key) for key in _MODERN_TERMINAL_ENV):
        return True
    if os.environ.get("TERM"):
        # Git Bash / MSYS2 的 mintty 会设置 TERM
        return True
    # 经典 conhost：只有 65001 代码页下歧义字符才按单宽渲染
    return _windows_console_is_utf8()


def _is_legacy_windows_console() -> bool:
    return IS_WINDOWS and not any(os.environ.get(key) for key in _MODERN_TERMINAL_ENV)


if IS_WINDOWS:
    _enable_windows_vt()
glyphs: Glyphs = UNICODE_GLYPHS if _detect_unicode() else ASCII_GLYPHS
_harden_std_streams()

# conhost 重绘开销大，刷新率稍低一些反而更顺滑、不撕裂
REFRESH_PER_SECOND = 8 if _is_legacy_windows_console() else 12

# 和 Claude Code 一样使用终端实际宽度，不做全局硬限宽。
# emoji=False：日志 / 源码里常见 `a:b:c` 形式的文本，不能被当成 :emoji: 代码替换。
console = Console(theme=THEME, highlight=False, emoji=False, safe_box=True)


def reset_cursor_line() -> None:
    """把光标拉回行首并清空当前行，再开始绘制任何带边框的内容。

    VS Code 等终端的 shell integration 会在命令执行前发一段
    `OSC 633;E;<命令行>;<nonce>` 标记；命令行折行时终端偶尔会把它的尾巴
    （形如 `r.md;6f5008f7-...`）当普通文本打出来，光标停在行中间，
    导致信息面板的上边框整体右移、与正文错位。
    """
    if not console.is_terminal or console.legacy_windows:
        return
    try:
        console.file.write("\r\x1b[2K")
        console.file.flush()
    except (OSError, ValueError):
        pass
