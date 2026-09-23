"""端到端冒烟：用脚本化模型替掉真实 LLM，以真实子进程跑一遍 `log-agent analyze`。

不需要 API key，但除了模型之外全是真的：真实的控制台编码（Windows 上是 GBK / cp1252）、
真实的 rich Live 渲染、子代理、日志与源码跨目录（Windows CI 上日志在 C: 临时目录、源码在 D: 工作区）。

用法：python -m tests.smoke_driver <日志路径> <源码目录> <报告输出路径>
"""

from __future__ import annotations

import sys

from langchain_core.messages import AIMessage

from log_agent import agent as agent_module
from log_agent import cli

from .conftest import ScriptedChatModel, tool_call

REPORT = """### 结论

请求 T42 在 twilio 返回 30008（配额耗尽）后降级到 nexmo，属于预期内的兜底行为。

### 证据

- 日志第 2 行：`twilio failed trace=T42 code=30008`
- 源码 `log_agent/tools.py`：trace_request 按时间合并多份日志

### 建议

补充 twilio 配额告警 → 提前扩容。
"""


def main(argv: list[str]) -> None:
    log_path, code_dir, out_file = argv
    script = [
        tool_call("log_overview", "o1", path=log_path),
        tool_call("trace_request", "r1", paths=[log_path], key="T42"),
        # 搜中文关键字：GBK 日志没被正确解码的话这里会是 0 命中
        tool_call("search_logs", "s1", path=log_path, pattern="降级到", regex=False),
        tool_call("task", "t1", subagent_type="code-investigator", description="找到 trace_request 的实现位置"),
        tool_call("grep_code", "g1", code_dir=code_dir, pattern="def trace_request", regex=False),
        tool_call("read_code_file", "c1", code_dir=code_dir, rel_path="log_agent/tools.py", start_line=1, end_line=5),
        AIMessage(content="子代理：trace_request 定义在 log_agent/tools.py。"),
        AIMessage(content=REPORT),
    ]
    model = ScriptedChatModel(script=script)
    real_build = agent_module.build_agent
    agent_module.build_agent = lambda **kwargs: real_build(model=model)

    sys.argv = [
        "log-agent", "analyze", "-l", log_path, "-c", code_dir, "-v",
        "-o", out_file, "-q", "T42 为什么降级到 nexmo",
    ]
    cli.app()


if __name__ == "__main__":
    main(sys.argv[1:])
