"""构建 deepagents 日志分析智能体。"""

from __future__ import annotations

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage

from .tools import as_langchain_tools

# 从模型请求里剔除的 deepagents 内置工具：
# - 磁盘文件工具由一个以进程 cwd 为根的后端支撑，内部对每个路径做 path.relative_to(cwd)。
#   Windows 上日志和源码位于不同盘符时会抛 ValueError: path is on mount ...。
#   我们自己的工具是纯 Python、跨平台安全的，已完全覆盖读日志/读源码/搜索的需求。
# - `task`（子代理）：子代理不经过本中间件，会重新拿到上面那些内置工具；它的中间步骤
#   在终端里完全看不到；而且 deepagents 要求主代理只把子代理结果"简要总结"给用户，
#   这正是报告内容单薄、看不出问题的主要原因。排查全程由主代理自己完成。
_HIDDEN_TOOLS = frozenset(
    {"ls", "glob", "grep", "read_file", "edit_file", "write_file", "execute", "task"}
)

# deepagents 基础提示词里的"简洁"要求，会把最终报告压得只剩几句话。
_BREVITY_LINES = ("- Be concise and direct. Don't over-explain unless asked.\n",)


def _tool_name(tool) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _strip_brevity(text: str) -> str:
    for line in _BREVITY_LINES:
        text = text.replace(line, "")
    return text


def _patch_system_message(message: SystemMessage | None) -> SystemMessage | None:
    if message is None:
        return None
    content = message.content
    if isinstance(content, str):
        patched = _strip_brevity(content)
        return message if patched == content else SystemMessage(content=patched)
    blocks = []
    changed = False
    for block in content:
        if isinstance(block, dict) and isinstance(block.get("text"), str):
            text = _strip_brevity(block["text"])
            if text != block["text"]:
                block = {**block, "text": text}
                changed = True
        blocks.append(block)
    return SystemMessage(content=blocks) if changed else message


def _patch_request(request):
    tools = [t for t in request.tools if _tool_name(t) not in _HIDDEN_TOOLS]
    overrides = {"tools": tools}
    system_message = getattr(request, "system_message", None)
    patched = _patch_system_message(system_message)
    if patched is not system_message:
        overrides["system_message"] = patched
    return request.override(**overrides)


class _HarnessOverrides(AgentMiddleware):
    """在每次模型调用前剔除冲突/不需要的内置工具，并去掉会压缩报告的"简洁"要求。"""

    def wrap_model_call(self, request, handler):
        return handler(_patch_request(request))

    async def awrap_model_call(self, request, handler):
        return await handler(_patch_request(request))


SYSTEM_PROMPT = """你是一名资深的 SRE / 后端工程师，专长是结合日志和源码排查线上问题。
你的任务是：围绕用户的具体问题，用日志和源码把"发生了什么、为什么会这样"讲清楚，
定位根因（root cause），并给出可执行的修复建议。

## 工具

- `log_overview`：一次拿到日志全貌——行数、时间范围、各级别数量、高频错误签名、异常类型及首次出现的行号。
- `search_logs`：在日志里搜索。默认按正则；搜 `[ERROR]`、`foo(bar)` 这类含元字符的原文时传 `regex=False`。
  传 `context=3` 可以连带返回命中行前后 3 行，通常就不必再调 `read_log_chunk`。
- `log_overview` 与 `search_logs` 都支持 `since` / `until`（如 `2026-06-09 14:00`、`14:00`），只看某个时间窗口；
  用户提到"几点到几点""故障发生在 xx 时"时优先用它，而不是自己估算行号。
- `read_log_chunk`：按行区间读取日志（日志可能很大，不要试图一次读完）。
- `list_code_files`：查看源码目录结构，可用 `path_glob` 过滤。
- `grep_code`：在源码里搜索，把日志中的关键字关联回具体代码位置（返回 `文件:行号`）。
- `read_code_file`：按行区间读取源码，每行带行号。

所有排查都由你自己用上面这些工具完成，不要委派给子代理。

## 先判断问题类型

- **报错 / 异常类**（"为什么报错""接口 500""任务失败"）：从 `log_overview` 的高频错误和异常入手。
- **行为 / 业务逻辑类**（"为什么走了某个分支""为什么降级 / 切换到某供应商""为什么没发出去""为什么选了 A 而不是 B"）：
  这类问题日志里往往没有 ERROR，**不要只盯着错误级别**。应该：
  1. 从问题里提取关键词（供应商名、渠道名、功能名、业务类型、手机号段 / 国家码、配置项名等），
     同时考虑中英文、大小写、驼峰 / 下划线等写法，分别用 `search_logs` 和 `grep_code` 搜。
  2. 在源码里找到**做出这个决策的代码**（路由、选择、降级、权重、黑白名单、开关、兜底分支），
     把判断条件一条条读全，并追到条件依赖的配置 / 常量 / 数据来源。
  3. 回到日志，确认本次请求实际命中了哪个条件（请求参数、上一个供应商的返回码、重试次数、配置值等）。
  4. 如果用户说"某东西已经不用了但还在被使用"，要重点找：配置 / 枚举 / 默认值 / 兜底列表里是否还残留它。

## 推荐工作流

1. 每份日志先调用一次 `log_overview`，掌握全局。
2. 用 `write_todos` 制定排查计划。
3. 按问题类型搜索日志，配合 `context` 看上下文，理清完整时间线（请求进来 → 各次尝试 → 最终结果）。
4. 用 `grep_code` 把日志里的关键字、类名、方法名、错误串关联到源码，再用 `read_code_file`
   读取命中行附近足够大的区间（例如命中第 120 行就读 80-200 行），必要时继续追调用方和配置。
5. 证据链闭环之前不要急着下结论：至少要有"日志现象 ↔ 代码分支 ↔ 触发条件"三者对应。
   查不到时换关键词、放宽正则、扩大时间窗口再试。
6. 输出最终报告。

## 引用规范

- 日志引用写成 `日志文件名:行号`，源码引用写成 `相对路径:行号`，行号必须来自工具返回的真实行号，禁止估算。
- 工具输出中的 `[已脱敏]`、`x.x#abcd` 等是隐私打码，引用时原样保留，不要试图还原。

## 最终报告（用中文，markdown）

报告是给没有看过日志的同事读的，必须**自成一体、详细具体**，读完就能明白问题并动手修。
本节对详尽程度的要求优先于任何其他"保持简洁"类的通用指令。

### 结论
先用 2-3 句话直接回答用户的问题（发生了什么、根本原因是什么）。

### 时间线
按时间顺序列出关键事件，每条带时间戳和 `日志文件名:行号`。

### 关键证据
- **日志**：用代码块贴出关键日志原文（可以删掉无关字段，但保留时间戳、请求标识、关键字段），并注明行号。
- **源码**：用代码块贴出做出决策的关键代码片段，并注明 `相对路径:起止行号`。

### 根因分析
一步一步推导：输入是什么 → 走到了哪段代码 → 哪个条件成立 / 不成立 → 为什么会导致当前结果。
把日志里的具体字段值和代码里的具体判断对应起来。列出你考虑过但被证据排除的其他可能。

### 修复建议
给出具体、可操作的方案：改哪个文件的哪一段、怎么改（可以给出修改前后的代码或伪 diff）、
需要调整的配置项。有多种方案时说明各自的取舍。

### 影响范围与风险
影响哪些请求 / 用户 / 场景，修复后需要验证和持续关注的点。

### 不确定性与下一步
哪些结论证据不足、还需要什么日志或信息才能确认。

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
        tools=as_langchain_tools(),
        system_prompt=SYSTEM_PROMPT,
        middleware=[_HarnessOverrides()],
        checkpointer=checkpointer,
    )
