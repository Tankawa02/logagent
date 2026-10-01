# 开发

[← 返回 README](../README.md) · [文档目录](README.md)


```bash
uv sync
uv run pytest          # 单元测试 + 基于剧本模型的端到端测试，不需要 API Key
uv run ruff check src tests
```

Web 界面的前端在 `web/`，构建产物提交在 `src/log_agent/web/static/`，改动后需要 `cd web && npm ci && npm run build`，
见 [Web 界面 · 开发](web.md#开发)。

CI 在 Ubuntu / Windows / macOS × Python 3.11 / 3.13 上运行同一套测试。其中 `tests/test_smoke.py`
用真实子进程跑完整的 `analyze`（只把模型换成脚本），覆盖 Windows 默认 GBK 控制台、中文 / 带空格路径、
日志和源码不在同一盘符等场景；也可以手动跑 `uv run python -m tests.smoke_driver <日志> src <输出.json>` 看实际终端效果。

### CLI 与渲染模块

`cli.py` 负责命令行参数、配置恢复与资源生命周期；共享的上下文消息和面板数据在
`cli_context.py`。交互会话分为：

- `chat_session.py`：会话状态与累计统计，来源变化时统一刷新引用链接并保存会话。
- `chat_commands.py`：斜杠命令注册表与处理函数；命令名、说明、处理函数在同一处注册，
  `/help` 和 `chat_input.py` 的补全列表从注册表生成。
- `chat_loop.py`：读取输入、分发命令、调用模型并保存每轮结果。

新增命令时，用 `@register("/命令", "说明")` 装饰接收 `(state, command, arg)` 的处理函数。
本地命令返回 `None` 后继续等待输入；需要提交问题或退出时返回 `CommandResult`。
当前来源、设置与历史报告快照分开保存，修改会话设置不能改写已生成的报告。

`render.py` 保留 `StreamRenderer` 的流式事件与 Live 生命周期，以及已有导入入口。
通用文本、路径和面板在 `render_common.py`，工具参数与结果展示在 `render_tools.py`，
报告结果模型、Markdown 与证据提示在 `render_report.py`。这些模块不反向依赖 `render.py`。

### 升级依赖

deepagents、langchain、langgraph、openai 在 `pyproject.toml` 里带了版本上限，`uv.lock` 锁定了测过的版本，
因为我们对它们有几处"依赖内部细节"的改写（删掉基础提示词里的"简洁"要求、隐藏内置文件工具、
监听 SDK 重试日志、靠回调看到子代理的过程）。上游一改写法，这些改写不会报错而是悄悄失效。

`tests/test_upstream_contracts.py` 把这些假设逐条钉住。升级流程：

```bash
uv lock --upgrade-package deepagents   # 或放宽 pyproject.toml 里的上限后 uv lock --upgrade
uv run pytest tests/test_upstream_contracts.py tests/test_smoke.py
```

契约测试失败时，失败信息会说明是哪条假设变了，对应去改 `agent.py` / `netguard.py` 里的常量。
CI 每周一还会用允许范围内的最新版本自动跑一次这两组测试，提前发现问题。

### 找回历史分析

在 chat 中查看当前会话的历史，不调用模型：

```text
/history
/history 超时
/show 3
/save --turn 3 "第三轮报告.json"
/save-ticket --turn 3 ticket.md
```

轮次按会话从 1 开始编号，恢复会话后保持不变。查看和导出历史不会改变当前来源、设置、
`/copy` 或 `/retry` 使用的上一轮回答。不指定 `--turn` 时仍导出上一轮。
旧版没有保存报告快照的轮次会留空，不会把后来的报告重新编号或伪造成早期报告。
`--turn` 放在路径之前；路径可包含空格，也可省略使用默认文件名。

跨会话搜索可以使用：

```bash
log-agent sessions list --search "超时"
```

搜索范围包括会话名、首个问题、当前日志路径，以及已保存的每轮问题、结论和回答正文；
按普通文本匹配，不区分大小写。旧版没有快照的回答不参与正文搜索。
