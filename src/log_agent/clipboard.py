"""把文本放进系统剪贴板，不引入额外依赖。

依次尝试各平台自带的命令；都不可用时（例如 SSH 到服务器上）退回 OSC 52，
由本地终端代为写入剪贴板（iTerm2、Windows Terminal、kitty、WezTerm 等支持）。
"""

from __future__ import annotations

import base64
import os
import platform
import shutil
import subprocess
import sys

from .term import console


class ClipboardError(RuntimeError):
    pass


def _commands() -> list[tuple[list[str], str]]:
    """(命令, 编码)。Windows 的 clip 需要带 BOM 的 UTF-16 才能正确处理中文。"""
    system = platform.system()
    if system == "Darwin":
        return [(["pbcopy"], "utf-8")]
    if system == "Windows":
        return [(["clip"], "utf-16")]
    commands: list[tuple[list[str], str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        commands.append((["wl-copy"], "utf-8"))
    if os.environ.get("DISPLAY"):
        commands.append((["xclip", "-selection", "clipboard"], "utf-8"))
        commands.append((["xsel", "--clipboard", "--input"], "utf-8"))
    commands.append((["clip.exe"], "utf-16"))  # WSL 里可以直接调用 Windows 的剪贴板
    return commands


def copy_text(text: str) -> str:
    """复制成功时返回所用的方式；全部失败时抛出 ClipboardError。"""
    for command, encoding in _commands():
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text.encode(encoding), check=True, timeout=5, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            continue
        return command[0]
    if console.is_terminal:
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        sys.stdout.write(f"\x1b]52;c;{payload}\a")
        sys.stdout.flush()
        return "OSC 52"
    raise ClipboardError("没有找到可用的剪贴板工具（pbcopy / clip / wl-copy / xclip / xsel）")
