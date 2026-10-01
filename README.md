# log-agent

基于 [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 的命令行日志分析智能体。
它会结合**日志文件**和**源代码目录**，自动检索异常、关联代码，最终输出根因分析与修复建议。

## 快速开始

```bash
uv tool install git+https://github.com/yourorg/log-agent.git  # 安装
log-agent init                                                 # 检查 Key、选模型、识别日志格式，生成 .log-agent.toml
log-agent analyze -l app.log                                   # 分析；配置里有 code 时会结合源码定位根因
```

还没有 Key？先 `export OPENAI_API_KEY="sk-..."`（Windows 见[配置 API Key](docs/configuration.md#配置-api-key)），
再用 `log-agent doctor --ping` 确认 Key 和网关真的能用。

## 功能

- 输入日志文件路径 + 源码目录路径，自动定位问题根因
- 两种模式：`analyze` 单次分析，`chat` 多轮对话（连续追问，记住上下文）
- 引导配置：`init` 检查 Key、选模型、抽样日志识别格式，生成 `.log-agent.toml`
- 本地预检查：`inspect` 查看编码、识别覆盖率和窗口样例；`doctor` 检查有效配置和依赖，`doctor --ping` 再真正发一次最小请求验证 Key 和网关
- 内置只读工具：日志概览（级别分布 / 高频错误 / 异常类型）、分块读日志、带上下文搜索日志、
  列源码、按行号读源码、grep 源码（装了 `rg` 会自动用 ripgrep 加速）
- 按请求追踪：给一个 traceId / requestId / 手机号，跨多份日志把同一请求的所有行按时间合并排好，连带堆栈
- 日志级别支持大小写与 JSON 字段；错误聚类保留 HTTP 状态码、业务错误码，避免把不同原因合并
- 异常链聚类：解析 Java / Python / Go / Node.js / .NET 的整段堆栈，按**根因异常 + 首个业务栈帧**归类，同一根因被不同外层包装时合并
- 日志格式：内置 nginx / Apache、syslog、glog、Tomcat、logfmt、Docker / CRI 容器日志、pino / Serilog / ECS JSON 等；也可在配置里自定义格式
- 跨日志时间比较保留 `Z` / `+08:00` 偏移；无偏移时间可通过 `--timezone` 指定时区
- 配置文件：模型、接口地址、默认源码目录写进 `.log-agent.toml`，命令行只写日志和问题
- 日志输入：可传多份、支持通配符与 `.gz`、支持管道 `-l -`；自动识别 UTF-8 / GBK / UTF-16 编码
- 大日志友好：稀疏行索引让跳读 GB 级日志的第 N 行近乎瞬时，超长单行自动截断
- 默认脱敏：token、密码、手机号、身份证、邮箱、IP 在发给模型前打码
- 报告可导出为 Markdown / JSON，方便贴进工单或接入自动化
- 证据回查：报告里每条证据的 `文件:行号` 与摘录都会在本地回读原文核对，编造或记错的引用会被标出来
- 关联代码变更：源码目录是 git 仓库时，agent 能查问题开始前的提交、报错行的 blame 和可疑提交的 diff
- 利用 deepagents 的子代理委派与上下文压缩
- 使用 OpenAI 模型（可切换其他 provider）
- 跨平台：macOS / Linux / Windows 行为一致（搜索为纯 Python 实现，不依赖系统 `grep`）

## 常用命令

| 命令 | 用途 | 要不要 Key |
|------|------|-----------|
| `log-agent init` | 引导配置，生成 `.log-agent.toml` | 可选（有 Key 时能列模型、验证） |
| `log-agent analyze -l app.log -c ./src -q "…"` | 单次分析，出一份报告 | 要 |
| `log-agent chat -l app.log` | 多轮对话，连续追问 | 要 |
| `log-agent watch -l app.log` | 追踪日志，出现新错误时自动分析 | 要 |
| `log-agent inspect -l app.log` | 本地检查格式、编码、时间窗口 | 不要 |
| `log-agent doctor [--ping]` | 检查有效配置和依赖；`--ping` 真正连一次模型服务 | `--ping` 时要 |
| `log-agent config` | 查看加载了哪些配置文件、各项取值 | 不要 |

## 文档

| 文档 | 内容 |
|------|------|
| [安装](docs/installation.md) | uv tool install / uvx 临时运行 / 本地开发 |
| [配置](docs/configuration.md) | `init` 引导、API Key（macOS / Linux / Windows）、自定义网关、`.log-agent.toml` |
| [使用](docs/usage.md) | `inspect` / `doctor` / `analyze` / `watch` / `chat` 的用法和示例 |
| [参数参考](docs/options.md) | 全部命令行参数、超时重试、时区规则 |
| [长期记忆与 Skills](docs/memory-and-skills.md) | 跨会话记忆、排查手册 |
| [分析能力](docs/analysis.md) | 报告导出、证据回查、关联 git 变更、异常链聚类 |
| [日志格式](docs/log-formats.md) | 内置格式列表与 `[[log_formats]]` 自定义格式 |
| [终端显示](docs/terminal.md) | 流式输出、超链接、Windows 兼容 |
| [安全说明](docs/security.md) | 只读工具、脱敏、git 沙箱 |
| [开发](docs/development.md) | 测试、升级依赖、找回历史分析 |
