"""构建 deepagents 日志分析智能体。"""

from __future__ import annotations

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware

from .tools import ALL_TOOLS

# deepagents 默认会注入一套内置文件工具，它们由一个以进程 cwd 为根的磁盘后端支撑，
# 内部对每个路径做 path.relative_to(cwd)。在 Windows 上，当日志和源码位于不同盘符
# （例如日志在 C:、源码在 D:、而命令从 D: 启动）时，跨盘符算相对路径会抛
# ValueError: path is on mount ... start on mount ...。
# 我们自己的 5 个工具是纯 Python、跨平台安全的，已完全覆盖读日志/读源码/搜索的需求，
# 所以这里把这些会冲突的内置工具从模型请求中过滤掉，让模型只用我们的工具。
_BUILTIN_FS_TOOLS = frozenset(
    {"ls", "glob", "grep", "read_file", "edit_file", "write_file", "execute"}
)


def _tool_name(tool) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class _StripBuiltinFsTools(AgentMiddleware):
    """在每次模型调用前，剔除 deepagents 内置的磁盘文件工具（跨平台安全）。"""

    def wrap_model_call(self, request, handler):
        filtered = [t for t in request.tools if _tool_name(t) not in _BUILTIN_FS_TOOLS]
        return handler(request.override(tools=filtered))

    async def awrap_model_call(self, request, handler):
        filtered = [t for t in request.tools if _tool_name(t) not in _BUILTIN_FS_TOOLS]
        return await handler(request.override(tools=filtered))

SYSTEM_PROMPT = """你是一名资深的 SRE / 后端工程师，专长是排查线上故障。
你的任务是：结合日志文件和源代码，定位问题的根因（root cause），并给出可执行的修复建议。

你拥有以下工具：

- `read_log_chunk`：按行区间分块读取日志（日志可能很大，不要试图一次读完）。
- `search_logs`：在日志里按关键字/正则搜索（先用它定位 ERROR / Exception / Traceback / 关键 id）。
- `list_code_files`：查看源码目录结构。
- `read_code_file`：读取某个源码文件的内容。
- `grep_code`：在源码里搜索关键字，把日志中的报错信息关联回具体代码位置。

## 推荐工作流

1. 先用 `write_todos` 制定排查计划。
2. 用 `search_logs` 在日志中搜索 `ERROR|FATAL|Exception|Traceback|panic` 等，定位异常发生的时间点和上下文。
3. 用 `read_log_chunk` 读取异常附近的行，理清事件时间线。
4. 从异常信息中提取关键线索（异常类型、函数名、错误字符串、文件名），用 `grep_code` 在源码中定位。
5. 用 `read_code_file` 阅读相关代码，确认问题逻辑。
6. 输出最终报告。

## 最终报告格式（用中文，markdown）

### 问题概述
一句话说明发生了什么。

### 关键证据
- 引用日志行（含行号）和源码位置（文件:行号）。

### 根因分析
解释为什么会发生，串起日志现象与代码逻辑。

### 修复建议
给出具体、可操作的修改方案。

### 影响范围与风险
简要说明影响面和后续需要关注的点。

注意：所有工具都是只读的，你不能修改任何文件。如果证据不足，要诚实说明不确定性，不要编造。
"""


def build_agent(model: str = "openai:gpt-4.1", checkpointer=None, base_url: str | None = None):
    """创建并返回一个配置好的日志分析 deep agent。

    Args:
        model: provider:model 格式的模型字符串，默认使用 OpenAI。
        checkpointer: 可选的 checkpointer，用于在多轮对话中保存状态。
            传入后即可通过同一 thread_id 进行连续追问。
        base_url: 可选的自定义 OpenAI 兼容接口地址（如自建网关 / 代理 /
            Azure / 第三方兼容服务）。传入后会显式构造一个 ChatOpenAI 实例，
            并把模型字符串里的 "openai:" 前缀去掉，只保留模型名。
    """
    resolved_model = model
    if base_url:
        # 显式走 OpenAI 兼容接口：去掉可能存在的 "openai:" 前缀，得到纯模型名
        model_name = model.split(":", 1)[1] if model.startswith("openai:") else model
        from langchain_openai import ChatOpenAI

        # api_key 仍从环境变量 OPENAI_API_KEY 读取
        resolved_model = ChatOpenAI(model=model_name, base_url=base_url)

    return create_deep_agent(
        model=resolved_model,
        tools=ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        middleware=[_StripBuiltinFsTools()],
        checkpointer=checkpointer,
    )
