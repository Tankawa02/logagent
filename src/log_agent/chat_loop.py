"""读取输入、分发本地命令、执行与保存模型回合。"""

from __future__ import annotations

from typing import Any

from rich.rule import Rule
from rich.text import Text

from .chat_commands import dispatch
from .chat_input import ChatInput
from .chat_session import ChatSession
from .cli_context import _build_context_message, _run_config
from .export import build_payload
from .memory_cli import after_turn
from .render import StreamRenderer
from .term import console, glyphs


def run_chat_loop(
    state: ChatSession, chat_input: ChatInput, agent: Any, *, verbose: bool, token_budget: Any, max_steps: int
) -> None:
    first_prompt = True
    while True:
        if not first_prompt:
            console.print()
            console.print(Rule(style="muted", characters=glyphs.rule))
        first_prompt = False
        try:
            user_input = chat_input.read().strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not user_input:
            first_prompt = True
            continue
        if user_input.lower() in {"exit", "quit", ":q", "退出", "结束"}:
            break
        retry_prefix = ""
        if state.suggestions and user_input.isdigit() and (1 <= int(user_input) <= len(state.suggestions)):
            user_input = state.suggestions[int(user_input) - 1]
            console.print(Text.assemble((f"{glyphs.notice} ", "accent"), (user_input, "muted")))
        if user_input.startswith("/"):
            outcome = dispatch(state, user_input)
            if outcome.exit:
                break
            if outcome.question is None:
                first_prompt = True
                continue
            user_input = outcome.question
            retry_prefix = outcome.retry_prefix
        if state.first_turn:
            message = _build_context_message(state.log_paths, state.code_paths, user_input, state.baseline_window)
            state.first_turn = False
        elif state.source_note:
            message = f"{state.source_note}\n\n{user_input}"
        else:
            message = user_input
        if retry_prefix:
            message = f"{retry_prefix}\n\n{message}"
        state.source_note = ""
        state.suggestions = []
        state.last_question = user_input
        payload = {"messages": [{"role": "user", "content": message}]}
        result = StreamRenderer(verbose=verbose, linker=state.linker, budget=token_budget).run(
            agent, payload, config=_run_config(max_steps, state.session)
        )
        state.totals.add(result)
        state.last = build_payload(
            result,
            question=user_input,
            logs=state.log_paths,
            code=state.code_paths,
            model=state.model,
            settings={**state.settings, "no_redact": state.no_redact},
        )
        state.store.record_turn(state.session, user_input, result.usage.get("total", 0), state.last)
        if result.interrupted:
            console.print(Text("本轮回答已中断，可以继续追问或换个问题。", style="muted"))
        after_turn(state.mem, user_input)
