"""构建 deepagents 日志分析智能体。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langchain.agents.middleware import AgentMiddleware

from .skills import build_skills, resolve_skill_sources
from .tools import as_langchain_tools

# 从模型请求里剔除的 deepagents 内置工具（0.7 起多了 delete）：
# - 这些文件工具操作的是代理状态里的虚拟文件，不是真实磁盘，模型用它们"浏览 / 搜索日志和源码"只会拿到空结果；
#   delete / write / edit 对只读排查也毫无意义。
# - 我们自己的工具是纯 Python、跨平台安全的，已完全覆盖读日志/读源码/搜索的需求。
# 子代理的 spec 里也挂了本中间件，所以子代理同样看不到这些内置工具。
_HIDDEN_TOOLS = frozenset({"ls", "glob", "grep", "edit_file", "write_file", "delete", "execute"})

# read_file 保留，但说明换成我们的：它是读取下面几类虚拟文件的唯一入口，隐藏掉会让这些内容永远读不到。
# - deepagents 会把超过约 8 万字符的工具结果转存到 /large_tool_results/<id>，只给模型留头尾预览，
#   并提示"用 read_file 分段读取"；
# - 对话过长被压缩时，被压缩掉的历史同样转存为虚拟文件；
# - skill 手册挂在 /skills/ 下（见 skills.py）。
_READ_FILE_DESCRIPTION = """读取智能体内部的虚拟文件（只读），支持 offset / limit 分段读取：
- `/skills/...`：skill 排查手册（SKILL.md）及其同目录参考文件；
- `/large_tool_results/...`：因过长被转存的工具结果，按提示分段读取，不要一次读完；
- 对话过长被压缩后转存的历史记录（压缩摘要里会给出路径）。

它**不能**读取磁盘上的日志或源码：日志请用 `read_log_chunk` / `search_logs`，源码请用 `read_code_file` / `grep_code`。"""

# 上游 `task` 工具说明是一长串"尽量拆成子任务并行"的示例，单仓库、单日志的问题也会被拆给子代理：
# 子代理要从零重新熟悉源码、交回两万字"证据"，整体比主代理自己查慢好几倍。
# 所以 task 说明整段换成我们自己的（子代理名单也是我们定义的，不依赖上游措辞），
# 何时委派以 SYSTEM_PROMPT 里的规则为准。
_TASK_DESCRIPTION = """把一个独立的取证子问题交给子代理，在独立上下文里执行，完成后返回一条取证结果。

可用的子代理（subagent_type）：
{agents}

使用须知：
- 默认自己用工具排查；只在 system prompt "何时委派" 一节允许的情况下使用。
- 子代理看不到你的对话历史，也不知道你已经查到了什么：description 里要写清用户问题、已知线索、
  已经定位到的文件 / 行号、要回答的具体问题，避免它从头重新摸索。
- 多个互不依赖的子任务可以在同一条消息里一次发起，并行执行。
- 子代理的结果用户看不到，只作为证据；最终报告由你按 system prompt 的报告格式完整撰写。"""


def _tool_name(tool) -> str | None:
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _with_description(tool, description: str):
    if isinstance(tool, dict):
        return {**tool, "description": description}
    try:
        return tool.model_copy(update={"description": description})
    except AttributeError:
        return tool


class _HarnessOverrides(AgentMiddleware):
    """在每次模型调用前剔除不需要的内置工具，并把 read_file、主代理 task 的说明换成我们的版本。

    deepagents 0.7 起不再注入自带的基础提示词和委派指南，系统提示词就是我们传入的原文（加上 skill 清单），
    这里只需要处理工具列表；契约测试会盯住上游是否又往系统提示词里加了东西。
    """

    def __init__(self, task_description: str | None = None):
        super().__init__()
        self.task_description = task_description

    def _patch(self, request):
        tools = []
        for tool in request.tools:
            name = _tool_name(tool)
            if name in _HIDDEN_TOOLS:
                continue
            if name == "read_file":
                tool = _with_description(tool, _READ_FILE_DESCRIPTION)
            elif name == "task" and self.task_description is not None:
                tool = _with_description(tool, self.task_description)
            tools.append(tool)
        return request.override(tools=tools)

    def wrap_model_call(self, request, handler):
        return handler(self._patch(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self._patch(request))


SYSTEM_PROMPT = """你是一名资深的 SRE / 后端工程师，专长是结合日志和源码排查线上问题。
你的任务是：围绕用户的具体问题，用日志和源码把"发生了什么、为什么会这样"讲清楚，
定位根因（root cause），并给出可执行的修复建议。

## 工具

- `log_overview`：一次拿到日志全貌——行数、时间范围、各级别数量、高频错误签名、异常类型及首次出现的行号。
- `search_logs`：在日志里搜索。默认按正则；搜 `[ERROR]`、`foo(bar)` 这类含元字符的原文时传 `regex=False`。
  传 `context=3` 可以连带返回命中行前后 3 行，通常就不必再调 `read_log_chunk`。
- `log_overview` 与 `search_logs` 都支持 `since` / `until`（如 `2026-06-09 14:00`、`14:00`），只看某个时间窗口；
  用户提到"几点到几点""故障发生在 xx 时"时优先用它，而不是自己估算行号。
- `trace_request`：传入 traceId / requestId / 订单号 / 手机号等请求标识，跨一份或多份日志找出所有相关行，
  按时间合并排序，连带后面的堆栈一起返回。追"某个请求经历了什么"时优先用它，比多次 `search_logs` 自己拼时间线更快更准。
- `read_log_chunk`：���行区间读取日志（日志可能很大，不要试图一次读完）。
- `list_code_files`：查看源码目录结构，可用 `path_glob` 过滤。
- `grep_code`：在源码里搜索，把日志中的关键字关联回具体代码位置（返回 `文件:行号`）。
- `read_code_file`：按行区间读取源码，每行带行号。
- `read_file`：只用来读 skill 手册，以及工具结果过长被转存后提示你去读的虚拟文件；不能读日志和源码。

- `task`：把独立的取证子问题委派给子代理（`code-investigator` 只读源码、`log-investigator` 只读日志、
  `general-purpose` 两者都能用）。

## 何时委派、何时自己做

**默认自己查。** 你的工具都很快（一次搜索通常不到 1 秒），同一条消息里可以并行发起多个工具调用；
而每委派一次，子代理都要从零重新熟悉日志和源码、再写一份长报告交回来，通常比你自己查慢好几倍。

- 只有一份日志、一个源码仓库的问题（绝大多数情况）：**不要委派**，自己按下面的工作流查。
  需要同时搜多个关键词、读多个文件时，在同一条消息里并行发起多个工具调用即可。
- 只在这些情况下委派，且一次最多 2-3 个：
  - 有多份日志要各自梳理时间线（例如多个服务 / 多台机器的日志），每份交给一个 `log-investigator`；
  - 问题同时牵涉多个**彼此无关**的源码仓库或模块，而你已经知道各自要查什么。
- 做决策的核心代码（路由、降级、兜底分支）要**自己读**，不要委派：根因推导需要你亲眼看到判断条件。
- 委派时 `description` 要写清：背景（用户问题、已知线索、日志 / 源码路径、已定位到的文件和行号）、
  要回答的具体问题，并要求它**只返回关键**日志行和代码片段（带 `文件:行号`）。子代理看不到你的对话历史。
- 子代理只负责取证，**最终报告必须由你自己撰写**：把返回的证据整合进报告，
  关键结论如有疑点，自己再用工具复核一次。不要只写"子代理发现……"，要把证据原文贴出来。

## 先判断问题类型

- **报错 / 异常类**（"为什么报错""接口 500""任务失败"）：从 `log_overview` 的高频错误和异常入手。
- **行为 / 业务��辑类**（"为什么走了某个分支""为什么降级 / 切换到某供应商""为什么没发出去""为什么选了 A 而不是 B"）：
  这类问题日志里往往没有 ERROR，**不要只盯着错误级别**。应该：
  1. 从问题里提取关键词（供应商名、渠道名、功能名、业务类型、手机号段 / 国家码、配置项名等），
     同时考虑中英文、大小写、驼峰 / 下划线等写法，分别用 `search_logs` 和 `grep_code` 搜。
  2. 在源码里找到**做出这个决策的代码**（路由、选择、降级、权重、黑白名单、开关、兜底分支），
     把判断条件一条条读全，并追到条件依赖的配置 / 常量 / 数据来源。
  3. 回到日志，确认本次请求实际命中了哪个条件（请求参数、上一个供应商的返回码、重试次数、配置值等）。
     拿到请求标识（traceId、手机号等）后用 `trace_request` 把这条请求在所有日志里的完整链路一次拉出来。
  4. 如果用户说"某东西已经不用了但还在被使用"，要重点找：配置 / 枚举 / 默认值 / 兜底列表里是否还残留它。

## 推荐工作流

1. 每份日志先调用一次 `log_overview`，掌握全局。
2. 按问题类型搜索日志，配合 `context` 看上下文，理清完整时间线（请求进来 → 各次尝试 → 最终结果）。
3. 用 `grep_code` 把日志里的关键字、类名、方法名、错误串关联到源码，再用 `read_code_file`
   读取命中行附近足够大的区间（例如命中第 120 行就读 80-200 行），必要时继续追调用方和配置。
   互不依赖的搜索和读取放在同一条消息里并行发起。
4. 证据链闭环之前不要急着下结论：至少要有"日志现象 ↔ 代码分支 ↔ 触发条件"三者对应。
   查不到时换关键词、放宽正则、扩大时间窗口再试。
5. 输出最终报告。

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
- 直接开始查，不要写计划。互不依赖的搜索 / 读取放在同一条消息里并行发起，减少来回轮次。
- 委派说明里已经给出的文件、行号、线索直接用，不要从头重新浏览整个仓库。
- 读源码时读足够大的区间，把判断条件、分支、依赖的配置 / 常量追全；与问题无关的调用链不要展开。
- 问题回答清楚了就立刻返回，不要为了"更完整"继续扩大搜索范围。
- 输出用中文，按下面结构返回，**总长度控制在 4000 字以内**（主代理要读完你的结果，越长越慢）：
  1. **结论**：直接回答委派的问题（查不到就明确说查不到，以及试过哪些方式）。
  2. **证据**：用代码块原样贴出**最关键的**日志行和代码片段（做出判断的那几行，不要整段整文件贴），
     每段注明 `日志文件名:行号` 或 `相对路径:起止行号`，行号必须来自工具返回的真实行号。
  3. **推导**：证据如何支撑结论；有哪些条件 / 分支需要主代理进一步确认。
- 不要写面向用户的完整报告（修复建议、影响范围等由主代理负责）。
- 所有工具都是只读的；工具输出里的 `[已脱敏]` 等打码原样保留。
"""

_LOG_TOOLS = ("log_overview", "search_logs", "trace_request", "read_log_chunk")
_CODE_TOOLS = ("list_code_files", "grep_code", "read_code_file")


def _subagent_lines(subagents: list[dict]) -> str:
    return "\n".join(f"- {s['name']}: {s['description']}" for s in subagents)


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


def _resolve_chat_model(model: Any, base_url: str | None) -> Any:
    """字符串模型统一带上超时与自动重试；测试等场景直接传入的模型实例原样使用。"""
    if not isinstance(model, str):
        return model

    from .netguard import install_retry_watch, max_retries, request_timeout

    install_retry_watch()
    options = {"timeout": request_timeout(), "max_retries": max_retries()}
    if base_url:
        # 显式走 OpenAI 兼容接口：去掉可能存在的 "openai:" 前缀，得到纯模型名；api_key 仍读 OPENAI_API_KEY
        model_name = model.split(":", 1)[1] if model.startswith("openai:") else model
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model_name, base_url=base_url, **options)

    from langchain.chat_models import init_chat_model

    try:
        return init_chat_model(model, **options)
    except (TypeError, ValueError):
        # 个别 provider 不认这两个参数时退回默认构造，至少保证能用
        return model


def build_agent(
    model: str = "openai:gpt-4.1",
    checkpointer=None,
    base_url: str | None = None,
    skill_dirs: Sequence[str | Path] | None = None,
):
    """创建并返回一个配置好的日志分析 deep agent。

    Args:
        model: provider:model 格式的模型字符串，默认使用 OpenAI。
        checkpointer: 可选的 checkpointer，用于在多轮对话中保存状态。
            传入后即可通过同一 thread_id 进行连续追问。
        base_url: 可选的自定义 OpenAI 兼容接口地址（如自建网关 / 代理 /
            Azure / 第三方兼容服务）。传入后会显式构造一个 ChatOpenAI 实例，
            并把模型字符串里的 "openai:" 前缀去掉，只保留模型名。
        skill_dirs: 额外的 skill 目录（优先级高于用户级 / 项目级默认目录）。
            传 None 时完全不加载 skill，包括默认目录；CLI 总会传入列表（可以为空）。
    """
    resolved_model = _resolve_chat_model(model, base_url)
    tools = as_langchain_tools()
    subagents = _subagents(tools)
    middleware: list[AgentMiddleware] = [
        _HarnessOverrides(task_description=_TASK_DESCRIPTION.format(agents=_subagent_lines(subagents))),
    ]
    # skill 只挂在主代理上：子代理只做主代理委派的窄取证任务，需要手册里的要点时由主代理写进 description。
    backend, skills_middleware = build_skills(resolve_skill_sources(skill_dirs) if skill_dirs is not None else [])
    if skills_middleware is not None:
        middleware.append(skills_middleware)
    return create_deep_agent(
        model=resolved_model,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
        subagents=subagents,
        middleware=middleware,
        backend=backend,
        checkpointer=checkpointer,
    )
