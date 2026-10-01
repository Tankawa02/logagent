"""本地预览 / 端到端检查用：造一份带错误尖峰的会话，用脚本化模型启动 Web 界面（不需要 API Key）。

用法：uv run python -m tests.web_preview [目录] [端口]
打开终端里打印的地址即可看到时间线、报告证据对照；网页续问会走脚本化模型，完整跑一遍真实的 agent 链路。
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from langchain_core.messages import AIMessage

from log_agent import agent as agent_module
from log_agent import cli
from log_agent.web.app import WebConfig, create_app

from .conftest import ScriptedChatModel, tool_call
from .web_fixtures import SESSION, report_text, seed_session, spike_log


def main(argv: list[str]) -> None:
    root = Path(argv[0] if argv else "/tmp/log-agent-preview").resolve()
    port = int(argv[1]) if len(argv) > 1 else 8765
    (root / "repo" / "app").mkdir(parents=True, exist_ok=True)
    (root / "repo" / "app" / "order.py").write_text(
        "def pay(order):\n    # 支付入口\n    return order['order_id']\n", encoding="utf-8",
    )
    log = spike_log(root / "app.log")
    db = root / "sessions.db"
    db.unlink(missing_ok=True)
    seed_session(db, log, root / "repo")

    real_build = agent_module.build_agent

    def scripted(**kwargs):
        script = [
            AIMessage(content="先看一下整体分布。", tool_calls=[
                {"name": "log_overview", "args": {"path": str(log)}, "id": "o1", "type": "tool_call"}]),
            tool_call("search_logs", "s1", path=str(log), pattern="KeyError", regex=False),
            AIMessage(content=report_text(log)),
        ]
        return real_build(model=ScriptedChatModel(script=script), checkpointer=kwargs.get("checkpointer"))

    agent_module.build_agent = scripted
    config = WebConfig(db_path=db, token="preview", agent_factory=cli._web_agent_factory, can_chat=True)
    print(f"http://127.0.0.1:{port}/sessions/{SESSION}?token=preview", flush=True)
    uvicorn.run(create_app(config), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(sys.argv[1:])
