"""chat 模式的输入框：历史记录、方向键、Ctrl+R 搜索、斜杠命令补全，三个平台行为一致。

优先用 prompt_toolkit（原生支持 Windows 控制台与中文输入法）；
stdin 不是终端（管道/测试）或初始化失败时，退回 Rich 的普通 input。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .term import console, glyphs

SLASH_COMMANDS: dict[str, str] = {
    "/help": "显示可用命令",
    "/save": "保存上一条回答，/save [路径]，默认 ./log-agent-report-时间.md",
    "/new": "开一个新会话（当前会话已保存，可用 -s 续上）",
    "/sources": "查看当前会话使用的日志与源码",
    "/stats": "查看本次运行的累计用量",
    "/remember": "记住一条偏好或项目知识，/remember [-g] <内容>，-g 表示全局",
    "/memory": "查看记忆；/memory review 处理待确认的建议，/memory edit <编号> <新内容> 修改",
    "/forget": "删除记忆，/forget <编号…>",
    "/exit": "退出",
}


class ChatInput:
    def __init__(self, history_file: Path) -> None:
        self._session = None
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory

            history_file.parent.mkdir(parents=True, exist_ok=True)
            completer = WordCompleter(
                list(SLASH_COMMANDS),
                meta_dict=SLASH_COMMANDS,
                sentence=True,
                pattern=None,
            )
            self._session = PromptSession(
                history=FileHistory(str(history_file)),
                completer=completer,
                complete_while_typing=True,
                enable_history_search=False,
                mouse_support=False,
            )
        except Exception:
            self._session = None

    def read(self) -> str:
        """读取一行输入。Ctrl+D / Ctrl+C 抛出 EOFError / KeyboardInterrupt，由调用方处理。"""
        if self._session is None:
            from rich.text import Text

            return console.input(Text.assemble(("\n", ""), (f"{glyphs.prompt} ", "accent.strong")))

        from prompt_toolkit.formatted_text import FormattedText

        console.print()
        # 只用 ANSI 16 色，与 Rich 主题的 accent（cyan）一致，经典 conhost 也能显示
        return self._session.prompt(FormattedText([("ansicyan bold", f"{glyphs.prompt} ")]))
