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
# 子代理的 spec 里也挂了本中间件，所以子代理同样看不到这些内置工具。
_HIDDEN_TOOLS = frozenset({"ls", "glob", "grep", "read_file", "edit_file", "write_file", "execute"})

# deepagents 基础提示词里的"简洁"要求，会把最终报告压得只剩几句话。
_BREVITY_LINES = ("- Be concise and direct. Don't over-explain unless asked.\n",)

# `task` 工具说明里要求主代理把子代理结果"简要总结"给用户——报告单薄的另一个来源。
_TASK_SUMMARY_RULE = (
    "To show the user the result, you should send a text message back to the user "
    "with a concise summary of the result."
)
_TASK_SUMMARY_REPLACEMENT = (
    "Use the returned findings as evidence; the final report to the user must be written by you, "
    "in full detail, following the report format in your system prompt."
)


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


def _patch_task_tool(tool):
    description = getattr(tool, "description", None)
    if not isinstance(description, str) or _TASK_SUMMARY_RULE not in description:
        return tool
    patched = description.replace(_TASK_SUMMARY_RULE, _TASK_SUMMARY_REPLACEMENT)
    try:
        return tool.model_copy(update={"description": patched})
    except AttributeError:
        return tool


def _patch_request(request):
    tools = [
        _patch_task_tool(t) if _tool_name(t) == "task" else t
        for t in request.tools
        if _tool_name(t) not in _HIDDEN_TOOLS
    ]
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

- `task`：把**独立、可并行**的取证工作委派给子代理，子代理在独立上下文里执行，能显著提速：
  - `code-investigator`：只读源码。适合"找出 xx 的路由 / 降级逻辑并把判断条件读全""追某个配置项在哪里被读取"。
  - `log-investigator`：只读日志。适合"在某份日志里把某个请求 / 手机号的完整时间线整理出来"。
  - `general-purpose`：同时需要日志和源码的独立子问题。
  多个互不依赖的子任务要在**同一条消息里一次性发起多个 `task`**，让它们并行执行。

## 何时委派、何时自己做

- 只需一两次工具调用的简单查找（看概览、搜一个关键词、读一小段）自己做，委派反而更慢。
- 需要大量翻读源码 / 日志的工作（跨多个文件追调用链、梳理多份日志）委派给子代理，并行发起。
- 给子代理的 `description` 要写清：背景（用户问题、已知线索、日志 / 源码路径）、要回答的具体问题、
  以及**要求它原样返回关键日志行和代码片段（带 `文件:行号`）**。子代理看不到你的对话历史。
- 子代理只负责取证，**最终报告必须由你自己撰写**：把子代理返回的证据整合进报告，
  关键结论如有疑点，自己再用工具复核一次。不要只写"子代理发现……"，要把证据原文贴出来。

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
3. 按问题类型搜索日志，配合 `context` 看��下文，理清完整时间线（请求进来 → 各次尝试 → 最终结果）。
4. 用 `grep_code` 把日志里的关键字、类名、方法名、错误串关联到源码，再用 `read_code_file`
   读取命中行附近足够大的区间（例如命中第 120 行就读 80-200 行），必要时继续追调用方和配置。
   需要追多条互不相关的线索（如多个模块、多份日志）时，一次性并行发起多个 `task` 交给子代理。
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


_SUBAGENT_PROMPT = """你是日志排查团队里负责取证的子代理，由主代理委派一个具体的子问题。
你的输出会交给主代理撰写最终报告，用户看不到你的中间过程。

要求：
- 只围绕委派给你的问题取证，用工具查证，不要猜测；查不到就换关键词、扩大范围再试。
- 读源码时读足够大的区间，把判断条件、分支、依赖的配置 / 常量一路追全。
- 输出用中文，**详细而不是简短**，按下面结构返回：
  1. **结论**：直接回答委派的问题（查不到就明确说查不到，以及试过哪些方式）。
  2. **证据**：用代码块原样贴出关键日志行和代码片段，每段注明 `日志文件名:行号` 或 `相对路径:起止行号`，
     行号必须来自工具返回的真实行号。
  3. **推导**：证据如何支撑结论；有哪些条件 / 分支需要主代理进一步确认。
- 不要写面向用户的完整报告（修复建议、影响范围等由主代理负责）。
- 所有工具都是只读的；工具输出里的 `[已脱敏]` 等打码原样保留。
"""

_LOG_TOOLS = ("log_overview", "search_logs", "read_log_chunk")
_CODE_TOOLS = ("list_code_files", "grep_code", "read_code_file")


def _subagents(tools: list) -> list[dict]:
    by_name = {t.name: t for t in tools}

    def pick(names: tuple[str, ...]) -> list:
        return [by_name[n] for n in names if n in by_name]

    def spec(name: str, description: str, spec_tools: list) -> dict:
        return {
            "name": name,
            "description": description,
            "system_prompt": _SUBAGENT_PROMPT,
            "tools": spec_tools,
            "middleware": [_HarnessOverrides()],
        }

    return [
        spec(
            "code-investigator",
            "只读源码的取证子代理：定位某段逻辑（路由、降级、兜底、配置读取等）并把判断条件和依赖读全，"
            "原样返回关键代码片段与行号。",
            pick(_CODE_TOOLS),
        ),
        spec(
            "log-investigator",
            "只读日志的取证子代理：在指定日志里按关键词 / 时间窗口梳理某个请求或现象的完整时间线，"
            "原样返回关键日志行与行号。",
            pick(_LOG_TOOLS),
        ),
        # 同名覆盖 deepagents 默认的 general-purpose 子代理，避免它带上跨盘符会出错的内置文件工具。
        spec(
            "general-purpose",
            "同时需要查日志和读源码的独立子问题，原样返回关键日志行、代码片段与行号。",
            list(tools),
        ),
    ]


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

    tools = as_langchain_tools()
    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        subagents=_subagents(tools),
        middleware=[_HarnessOverrides()],
        checkpointer=checkpointer,
    )
