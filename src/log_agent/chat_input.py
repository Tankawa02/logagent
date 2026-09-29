"""chat 模式的输入框：历史记录、方向键、Ctrl+R 搜索、斜杠命令补全，三个平台行为一致。

优先用 prompt_toolkit（原生支持 Windows 控制台与中文输入法）；
stdin 不是终端（管道/测试）或初始化失败时，退回 Rich 的普通 input。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .term import console, glyphs

SLASH_COMMANDS: dict[str, str] = {
    "/history": "列出当前会话历史，/history [关键词] 搜索问题和回答",
    "/show": "查看指定轮次的完整报告，/show 3",
    "/help": "显示可用命令",
    "/save": "保存报告，/save [--turn 轮次] [路径]，默认上一轮",
    "/save-brief": "保存上一轮速览，/save-brief [--turn 轮次] [路径]",
    "/save-ticket": "保存上一轮工单，/save-ticket [--turn 轮次] [路径]",
    "/copy": "把上一条回答复制到剪贴板（Markdown 原文）",
    "/retry": "重新回答上一个问题，可附补充要求，/retry [补充]",
    "/add-log": "给当前会话追加日志文件，/add-log <路径或通配符>",
    "/add-code": "给当前会话追加源码目录，/add-code <目录>",
    "/remove-log": "移除会话中的日志，/remove-log <完整路径或唯一文件名>（不删除文件）",
    "/window": "调整时间窗口，/window 14:00~14:30；/window off 取消",
    "/baseline": "调整正常时段，/baseline 13:00~13:30；/baseline off 取消",
    "/settings": "查看有效模型、时区、时间范围、基线和预算",
    "/new": "开一个新会话（当前会话已保存，可用 -s 续上）",
    "/sources": "查看当前会话使用的日志与源码",
    "/stats": "查看本次运行的累计用量",
    "/remember": "记住一条偏好或项目知识，/remember [-g] <内容>，-g 表示全局",
    "/memory": "查看记忆；/memory review 处理待确认的建议，/memory edit <编号> <新内容> 修改",
    "/forget": "删除记忆，/forget <编号…>",
    "/exit": "退出",
}


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
