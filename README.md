# log-agent

基于 [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 的日志分析智能体。
它会结合**日志文件**和**源代码目录**，自动检索异常、关联代码，最终输出带证据行号的根因分析与修复建议。
提供 **Web 界面**（推荐，适合日常排查）和**命令行**（适合服务器、脚本与自动化），两边共用同一份配置和会话。

## 快速开始

### 推荐：Web 界面

```bash
uv tool install 'log-agent[web] @ git+https://github.com/yourorg/log-agent.git'   # 安装（含 Web 依赖）
log-agent serve                                                                   # 启动，服务就绪后自动打开浏览器
```

1. 打开侧边栏的 **Settings**，填写 API Key、接口地址（用自建网关时）和默认模型，点「测试连接」确认能用，保存后立即生效，不用重启。
2. 回到首页，浏览选择日志文件和源码目录，用一句话描述问题，开始分析。
3. 在会话页看执行过程、报告与证据原文，继续追问，或生成只读链接分享给同事。

### 命令行

```bash
uv tool install git+https://github.com/yourorg/log-agent.git  # 安装
log-agent init                                                 # 检查 Key、选模型、识别日志格式，生成 .log-agent.toml
log-agent analyze -l app.log                                   # 分析；配置里有 code 时会结合源码定位根因
```

还没有 Key？可以在网页「设置」里填，也可以 `export OPENAI_API_KEY="sk-..."`（Windows 见[配置 API Key](docs/configuration.md#配置-api-key)），
再用 `log-agent doctor --ping` 确认 Key 和网关真的能用。

## 网页还是命令行

| 场景 | 推荐 | 原因 |
|------|------|------|
| 日常交互式排查、对照证据、连续追问 | Web | 选文件、看执行过程、证据左右对照都比终端直观，新同事也容易上手 |
| 交接给同事、贴进工单 | Web | 一键生成只读分享链接，对方不用安装；也能导出 Markdown / 工单格式 |
| SSH 到服务器上直接查 | 命令行 | 日志就在本机，`log-agent analyze -l /var/log/app.log` 一条命令出结论，不用开端口 |
| CI、定时任务、告警回调 | 命令行 | 没有浏览器；`analyze -o report.json` 输出结构化结果给下游（建工单、发群消息） |
| 批量、多时间段循环跑 | 命令行 | 方便写进脚本、用管道串起其他命令 |

两边互通：命令行跑的分析自动存成会话，网页里能打开、续问、分享；网页「设置」保存的 Key 和配置，命令行也会读取。

## 功能

**Web 界面**（`log-agent serve`）

- **设置页**：在网页里调整 API Key、接口地址、默认模型、超时重试、默认时区和长期记忆，保存即生效；可以先「测试连接」再保存
- **网页新建分析**：浏览选择日志文件 / 源码目录，填问题、时间范围、基线、模型，直接开跑
- **执行过程实时可见**：工具调用按步骤时间线展示；切走页面分析照常进行，回来自动接上
- **报告与证据左右对照**：点证据或正文里的 `文件:行号` 在右侧看原文；错误时间线点尖峰直接追问
- **Trace 页**：按模型 / 状态筛选每轮对话，看耗时、token 用量、费用（美元）和工具调用排行
- **结论反馈**：每轮报告可标「有用 / 没用 / 根因不对」并写下真正的根因；纠正会作为历史案例线索提供给之后的同类排查，也可一键导出为评测案例
- **Skills 与长期记忆管理**：在线编辑排查手册，增删改记忆、确认 agent 提议的记忆
- 只读分享链接交接给同事；亮色 / 暗色 / 跟随系统三种主题

**分析能力**

- 输入日志文件 + 源码目录，自动定位问题根因；两种模式：单次分析，或多轮对话连续追问
- 内置只读工具：日志概览（级别分布 / 高频错误 / 异常类型）、分块读日志、带上下文搜索日志、
  列源码、按行号读源码、grep 源码（装了 `rg` 会自动用 ripgrep 加速）
- 按请求追踪：给一个 traceId / requestId / 手机号，跨多份日志把同一请求的所有行按时间合并排好，连带堆栈
- 异常链聚类：解析 Java / Python / Go / Node.js / .NET 的整段堆栈，按**根因异常 + 首个业务栈帧**归类，同一根因被不同外层包装时合并；
  错误聚类保留 HTTP 状态码、业务错误码，避免把不同原因合并
- 证据回查：报告里每条证据的 `文件:行号` 与摘录都会在本地回读原文核对，编造或记错的引用会被标出来；
  行号偏了会就近校正，摘录对不上会让模型对照原文改正一次，仍对不上的证据直接移除
- 历史案例：新会话首轮按异常链签名检索过去相似的排查（优先已确认 / 被纠正过的），作为待验证线索交给模型
- 关联代码变更：源码目录是 git 仓库时，agent 能查问题开始前的提交、报错行的 blame 和可疑提交的 diff
- 日志格式：内置 nginx / Apache、syslog、glog、Tomcat、logfmt、Docker / CRI 容器日志、pino / Serilog / ECS JSON 等；也可在配置里自定义格式
- 日志输入：可传多份、支持通配符与 `.gz`；自动识别 UTF-8 / GBK / UTF-16 编码；跨日志时间比较保留 `Z` / `+08:00` 偏移
- 大日志友好：稀疏行索引让跳读 GB 级日志的第 N 行近乎瞬时，超长单行自动截断
- 默认脱敏：token、密码、手机号、身份证、邮箱、IP 在发给模型前打码
- 利用 deepagents 的子代理委派与上下文压缩；使用 OpenAI 兼容接口（可切换其他 provider）

**命令行**

- 引导配置：`init` 检查 Key、选模型、抽样日志识别格式，生成 `.log-agent.toml`
- 本地预检查：`inspect` 查看编码、识别覆盖率和窗口样例；`doctor` 检查有效配置和依赖，`doctor --ping` 再真正发一次最小请求验证 Key 和网关
- 支持管道 `-l -`；报告可导出为 Markdown / JSON，方便贴进工单或接入自动化；`watch` 追踪日志，出现新错误时自动分析
- 跨平台：macOS / Linux / Windows 行为一致（搜索为纯 Python 实现，不依赖系统 `grep`）

## 常用命令

| 命令 | 用途 | 要不要 Key |
|------|------|-----------|
| `log-agent serve` | Web 界面：设置、新建分析、网页续问、证据对照、Trace、Skills / 记忆管理、分享链接（需 `log-agent[web]`） | 新建分析 / 续问时要（可在设置页填） |
| `log-agent init` | 引导配置，生成 `.log-agent.toml` | 可选（有 Key 时能列模型、验证） |
| `log-agent analyze -l app.log -c ./src -q "…"` | 单次分析，出一份报告 | 要 |
| `log-agent chat -l app.log` | 多轮对话，连续追问 | 要 |
| `log-agent watch -l app.log` | 追踪日志，出现新错误时自动分析 | 要 |
| `log-agent inspect -l app.log` | 本地检查格式、编码、时间窗口 | 不要 |
| `log-agent doctor [--ping]` | 检查有效配置和依赖；`--ping` 真正连一次模型服务 | `--ping` 时要 |
| `log-agent config` | 查看加载了哪些配置文件、各项取值 | 不要 |
| `log-agent eval run -m 模型A -m 模型B` | 用 `evals/cases/` 的固定案例对比模型 / 结构化方式的准确率、证据一致率、耗时和费用 | 要 |
| `log-agent eval export 会话名` | 把一轮真实排查导出为评测案例（`case.toml`），补全期望后加入回归 | 不要 |

## 文档

| 文档 | 内容 |
|------|------|
| [安装](docs/installation.md) | uv tool install / uvx 临时运行 / 本地开发 |
| [Web 界面](docs/web.md) | `serve`：设置页、新建分析、执行过程、报告证据对照、Trace、Skills / 记忆管理、分享链接与访问控制 |
| [配置](docs/configuration.md) | `init` 引导、API Key（网页设置 / macOS / Linux / Windows）、自定义网关、`.log-agent.toml` |
| [使用](docs/usage.md) | `inspect` / `doctor` / `analyze` / `watch` / `chat` 的用法和示例 |
| [参数参考](docs/options.md) | 全部命令行参数、超时重试、时区规则 |
| [长期记忆与 Skills](docs/memory-and-skills.md) | 跨会话记忆、排查手册 |
| [分析能力](docs/analysis.md) | 报告导出、证据回查、关联 git 变更、异常链聚类 |
| [日志格式](docs/log-formats.md) | 内置格式列表与 `[[log_formats]]` 自定义格式 |
| [终端显示](docs/terminal.md) | 流式输出、超链接、Windows 兼容 |
| [安全说明](docs/security.md) | 只读工具、脱敏、git 沙箱、API Key 存放 |
| [开发](docs/development.md) | 测试、升级依赖、找回历史分析 |
