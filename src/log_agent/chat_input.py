"""chat 模式的输入框：历史记录、方向键、Ctrl+R 搜索、斜杠命令补全，三个平台行为一致。

优先用 prompt_toolkit（原生支持 Windows 控制台与中文输入法）；
stdin 不是终端（管道/测试）或初始化失败时，退回 Rich 的普通 input。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .chat_commands import COMMANDS
from .term import console, glyphs

SLASH_COMMANDS: dict[str, str] = {name: spec.description for name, spec in COMMANDS.items()}


def make_completer():
    from prompt_toolkit.completion import Completer, PathCompleter, WordCompleter
    from prompt_toolkit.document import Document

    class ChatCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            command, separator, arg = text.partition(" ")
            if separator and command in {"/add-log", "/add-code", "/remove-log", "/save", "/save-brief", "/save-ticket"}:
                if command.startswith("/save") and arg.lstrip().startswith("--turn"):
                    import re

                    selection = re.match(r"\s*--turn\s+[1-9][0-9]*\s+(.*)", arg)
                    if not selection:
                        return
                    arg = selection[1]
                path = arg.lstrip().lstrip('"\'')
                completer = PathCompleter(expanduser=True, only_directories=command == "/add-code")
                yield from completer.get_completions(Document(path, len(path)), complete_event)
            else:
                completer = WordCompleter(list(SLASH_COMMANDS), meta_dict=SLASH_COMMANDS, sentence=True)
                yield from completer.get_completions(document, complete_event)

    return ChatCompleter()


class ChatInput:
    def __init__(self, history_file: Path) -> None:
        self._session = None
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.history import FileHistory

            history_file.parent.mkdir(parents=True, exist_ok=True)
            completer = make_completer()
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
