# log-agent

基于 [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 的命令行日志分析智能体。
它会结合**日志文件**和**源代码目录**，自动检索异常、关联代码，最终输出根因分析与修复建议。

## 功能

- 输入日志文件路径 + 源码目录路径，自动定位问题根因
- 两种模式：`analyze` 单次分析，`chat` 多轮对话（连续追问，记住上下文）
- 内置只读工具：日志概览（级别分布 / 高频错误 / 异常类型）、分块读日志、带上下文搜索日志、
  列源码、按行号读源码、grep 源码（装了 `rg` 会自动用 ripgrep 加速）
- 按请求追踪：给一个 traceId / requestId / 手机号，跨多份日志把同一请求的所有行按时间合并排好，连带堆栈
- 配置文件：模型、接口地址、默认源码目录写进 `.log-agent.toml`，命令行只写日志和问题
- 日志输入：可传多份、支持通配符与 `.gz`、支持管道 `-l -`；自动识别 UTF-8 / GBK / UTF-16 编码
- 大日志友好：稀疏行索引让跳读 GB 级日志的第 N 行近乎瞬时，超长单行自动截断
- 默认脱敏：token、密码、手机号、身份证、邮箱、IP 在发给模型前打码
- 报告可导出为 Markdown / JSON，方便贴进工单或接入自动化
- 利用 deepagents 的子代理委派与上下文压缩
- 使用 OpenAI 模型（可切换其他 provider）
- 跨平台：macOS / Linux / Windows 行为一致（搜索为纯 Python 实现，不依赖系统 `grep`）

## 团队安装（uv）

### 方式一：从私有 Git 仓库安装（推荐）

```bash
uv tool install git+https://github.com/yourorg/log-agent.git
```

安装后全局可用：

```bash
log-agent --help
```

### 方式二：临时运行（不常驻安装）

```bash
uvx --from git+https://github.com/yourorg/log-agent.git log-agent analyze --log app.log
```

### 方式三：本地开发

```bash
git clone https://github.com/yourorg/log-agent.git
cd log-agent
uv sync
uv run log-agent --help
```

## 配置 API Key

每个团队成员各自设置自己的 OpenAI key（不要写进代码或仓库）。不同系统设置方式不同：

### macOS / Linux

```bash
export OPENAI_API_KEY="sk-..."
```

写进 `~/.zshrc` 或 `~/.bashrc` 可永久生效。

### Windows（PowerShell）

```powershell
# 仅当前会话生效
$env:OPENAI_API_KEY="sk-..."

# 永久生效（写入用户环境变量，需重开终端）
setx OPENAI_API_KEY "sk-..."
```

### Windows（CMD）

```cmd
:: 仅当前会话生效
set OPENAI_API_KEY=sk-...

:: 永久生效（需重开终端）
setx OPENAI_API_KEY "sk-..."
```

> 提示：用 `setx` 设置后，需要**重新打开终端**才能读到新变量。

### 自定义接口地址（可选）

如果你用的是自建网关、代理或第三方 OpenAI 兼容服务，可以指定 base URL。两种方式任选其一：

```bash
# 方式一：环境变量（推荐，团队统一配置）
export OPENAI_BASE_URL="https://your-gateway.com/v1"

# 方式二：命令行参数（临时覆盖）
log-agent analyze -l app.log --base-url https://your-gateway.com/v1
```

命令行参数 `--base-url` 优先级高于环境变量。`analyze` 和 `chat` 两个命令都支持。

### 配置文件（可选）

不想每次都写 `-m`、`--base-url`、`-c`，可以放进配置文件：

```bash
log-agent config --init   # 在当前目录生成带注释的 .log-agent.toml 模板
log-agent config          # 查看加载了哪些配置文件、各项最终取值
```

```toml
# .log-agent.toml（放在项目根目录，子目录里运行也能找到）
model = "openai:qwen-max"
base_url = "https://your-gateway.com/v1"
code = ["../sms-service"]   # 相对路径以本文件所在目录为准
timeout = 180               # 单次模型请求超时（秒）
max_retries = 5

[analyze]                   # 只对 analyze 生效
verbose = true
```

配好以后命令可以缩成 `log-agent analyze -l app.log -q "为什么降级到 nexmo"`。

- 查找顺序：用户级 `~/.log-agent/config.toml` → 项目级 `.log-agent.toml`（从当前目录逐级向上找最近的一个，覆盖用户级）；
  也可以用环境变量 `LOG_AGENT_CONFIG` 直接指定文件。
- 优先级：命令行参数 > 环境变量（`LOG_AGENT_MODEL`、`OPENAI_BASE_URL` 等）> 配置文件 > 内置默认值。
- 支持的键：`model`、`base_url`、`code`、`encoding`、`no_redact`、`max_steps`、`verbose`、`timeout`、`max_retries`，
  以及 chat 专用的 `db`。写错的键会给出提示并忽略。
- **API key 不支持写进配置文件**，仍然用 `OPENAI_API_KEY` 环境变量，避免 key 跟着项目文件被提交。

## 使用

工具提供两种模式：

- **`analyze`** — 单次一问一答，跑完出一份报告就结束。适合快速排查。
- **`chat`** — 多轮对话，agent 记住整段对话和已读过的日志，可以连续追问。适合深入排查。

### 单次分析（analyze）

```bash
# 只分析日志
log-agent analyze --log /path/to/app.log

# 日志 + 源码，定位根因
log-agent analyze --log /path/to/app.log --code /path/to/your/repo

# 指定问题
log-agent analyze -l app.log -c ./repo -q "为什么 14:00 之后接口大量 500？"

# 只看某个时间窗口（堆栈等无时间戳的行跟随上一条日志；'14:05' 包含到 14:05:59）
log-agent analyze -l app.log -c ./repo --since "2026-06-09 14:00" --until "2026-06-09 14:05"
log-agent analyze -l app.log --since 14:00

# 保留每一步工具调用与计划变化的完整记录
log-agent analyze -l app.log -c ./repo --verbose

# 切换模型（也可以设环境变量 LOG_AGENT_MODEL 作为团队默认）
log-agent analyze -l app.log -m openai:gpt-4.1-mini

# 多份日志 / 通配符 / 压缩日志（通配符请加引号，Windows 下也由程序自己展开）
log-agent analyze -l gateway.log -l order.log -c ./repo
log-agent analyze -l "logs/app-*.log.gz"

# 追一条请求：问题里带上 traceId / 手机号，agent 会用 trace_request 把它在各份日志里的完整链路按时间拉出来
log-agent analyze -l gateway.log -l router.log -c ./repo -q "traceId=8f3a2c 这条短信为什么降级到 nexmo"

# 从管道读取
kubectl logs deploy/order --since=1h | log-agent analyze -l - -c ./repo

# 导出报告（按扩展名推断格式，也可用 -f 指定）
log-agent analyze -l app.log -c ./repo -o report.md
log-agent analyze -l app.log -o result.json

# GBK 等编码自动识别失败时手动指定；必要时关闭脱敏
log-agent analyze -l app.log --encoding gbk --no-redact
```

退出码：`0` 成功，`1` 失败（如达到 `--max-steps` 上限），`2` 参数错误，`130` 被 Ctrl+C 中断。

### 多轮对话（chat）

```bash
log-agent chat --log /path/to/app.log --code /path/to/your/repo
```

进入交互界面后可以连续追问，例如：

```
❯ 先分析一下整体有哪些异常
❯ 那 14:02 那个 NullPointer 具体是哪段代码引起的？
❯ 这个问题和前面的超时有关联吗？
❯ 退出
```

输入 `exit` / `quit` / `退出` / `结束` 即可结束对话。回答过程中按 `Ctrl+C` 只中断当前这一轮，
已经输出的内容会保留，可以接着追问；在输入提示符处按 `Ctrl+C` 才会退出。

**会话持久化**：对话历史保存在本地 SQLite（默认 `~/.log-agent/sessions.db`），关掉终端后还能续上。用 `--session` 给会话命名，不同名称互相隔离；用相同名称即可恢复之前的对话：

```bash
# 开一个名为 payment-bug 的会话
log-agent chat -l app.log -c ./repo --session payment-bug

# 关掉终端后，再次用同名会话继续之前的对话
log-agent chat -l app.log -c ./repo --session payment-bug

# 自定义数据库文件位置
log-agent chat -l app.log --session payment-bug --db ./my-sessions.db

# 查看 / 删除会话
log-agent sessions list
log-agent sessions rm payment-bug
```

续会话时如果换了日志或源码，agent 会在下一条消息里被告知新路径，不会继续引用旧文件。

输入框支持方向键翻历史（跨会话保存在 `~/.log-agent/history`）、`Ctrl+R` 反向搜索，以及斜杠命令（输入 `/` 自动补全）：

| 命令 | 说明 |
|------|------|
| `/save [路径]` | 保存上一条回答为 Markdown（`.json` 结尾则存 JSON） |
| `/new` | 开一个新会话 |
| `/sources` | 查看当前日志与源码 |
| `/stats` | 查看本次运行累计的轮次、耗时、工具次数与 tokens |
| `/remember [-g] <内容>` | 记住一条偏好或项目知识，`-g` 存为全局，见下方「长期记忆」 |
| `/memory` | 查看记忆；`/memory review` 处理待确认的建议，`/memory edit <编号> <新内容>` 修改 |
| `/forget <编号…>` | 删除记忆 |
| `/help` | 显示命令列表 |

## 参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--log` | `-l` | 日志文件（必填，可重复、支持通配符 / `.gz` / `-`） |
| `--code` | `-c` | 源码目录（可选，可重复） |
| `--skills` | | 额外的 skill 目录（可重复），见下方「Skills（排查手册）」 |
| `--question` | `-q` | 想让 agent 回答的具体问题（analyze） |
| `--output` / `--format` | `-o` / `-f` | 导出报告到文件，`markdown` 或 `json`（analyze） |
| `--since` / `--until` | | 只分析该时间窗口内的日志，支持 `2026-06-09 14:00`、`2026-06-09T14:00:30`、`2026-06-09`、`14:00`；agent 需要对比时仍可显式查窗口外 |
| `--model` | `-m` | 模型，`provider:model` 格式，默认读 `LOG_AGENT_MODEL`，否则 `openai:gpt-4.1` |
| `--encoding` | | 强制日志编码，默认自动探测（也可设 `LOG_AGENT_ENCODING`） |
| `--no-redact` | | 关闭敏感信息脱敏 |
| `--max-steps` | | 单轮最大推理步数，默认 120 |
| `--verbose` | `-v` | 保留每一步工具调用（含结果摘要、耗时）与计划变化的完整记录 |
| `--memory` | | 长期记忆：`suggest`（默认）/ `explicit` / `off`，见下方「长期记忆」 |

模型接口默认单次请求超时 120 秒、失败自动重试 3 次（连接失败、超时、429、5xx），可用环境变量调整：
`LOG_AGENT_TIMEOUT=300`、`LOG_AGENT_MAX_RETRIES=5`。重试时状态栏会提示"接口波动，自动重试第 N 次"；
重试用尽仍失败时给出中文原因（401 / 404 / 超时等），已经输出的部分报告照常保存到 `-o`。

## 长期记忆

agent 会跨会话记住三类信息：**表达偏好**（"以后先写结论""报告别超过一屏"）、**术语**（"老通道指 Nexmo"）
和**项目事实**（"prod 的短信都走 gateway-b"）。每轮开始时把它们附在系统提示词末尾，删改下一轮就生效。

- **只有你确认过的才会被记住。** 你说"记住……""以后都……"或输入 `/remember <内容>` 时直接保存；
  agent 在对话中发现值得记的内容（比如你纠正了它）只能**提议**，本轮结束后由你选择
  `[y] 保存 / [n] 不保存 / [e] 编辑 / [N] 不再提示 / [s] 稍后`。
- **不会动不动就问。** 只出现一次的普通提及先攒着，在两个不同会话里都出现过才在会话结束时问一次；
  30 天没再出现的候选自动作废。拒绝过的内容 90 天内不再提（选 `N` 则永久不提）。
- **范围**：表达偏好默认全局；术语和项目事实归属于当前项目（第一个 `-c` 源码目录所在的 git 仓库），
  换项目不会互相干扰。没传 `-c` 时只用全局记忆。
- **记忆只是背景，不是证据**：与本次日志、源码冲突时以证据为准，报告里会指出这条记忆可能过时。
  表达偏好可以改变报告的顺序和篇幅，但不会放宽引用规范和证据要求。
- 保存的内容同样经过脱敏（跟随 `--no-redact`）。记忆库在 `~/.log-agent/memory.db`，与会话库分开。
- `analyze` 在终端里直接运行时会在报告后询问；通过管道或 `-o` 输出到文件时不打断，建议留到下次处理。

```bash
log-agent memory list                 # 查看全部记忆（-c ./repo 只看全局与该项目）
log-agent memory add "报告先写结论" -g  # 手动添加；--kind preference / term / fact
log-agent memory edit 3 "老通道指 Nexmo，2025 年起停用"
log-agent memory rm 3
log-agent memory review               # 逐条处理待确认的建议
```

不想要这个功能时，在配置文件里写 `memory = "explicit"`（只记你明确要求的）或 `memory = "off"`（完全关闭）。

## Skills（排查手册）

把团队的排查经验写成 skill，agent 遇到对应问题时会先读手册再按步骤查（例如某个服务的关键日志字段、
常见根因、要重点看的代码位置）。每个 skill 是一个目录，里面放一份带 frontmatter 的 `SKILL.md`：

```
.log-agent/skills/
└── sms-routing/
    ├── SKILL.md
    └── vendor-codes.md        # 可选的参考资料，SKILL.md 里提到即可
```

```markdown
---
name: sms-routing
description: 短信供应商路由、降级、切换问题的排查手册
---
# 短信路由排查
1. 先用 trace_request 按手机号拉出整条链路，关注 vendor= 与 fallback= 字段
2. 路由决策在 router/VendorSelector.java 的 select()，权重来自配置 sms.vendor.weights
...
```

加载位置（后者同名覆盖前者）：`~/.log-agent/skills/`（个人）→ 项目内最近的 `.log-agent/skills/`（随仓库共享）
→ `--skills DIR` 或配置文件里的 `skills = ["..."]`。启动面板的 "Skills" 一行会显示加载了几个。

系统提示词里只放每个 skill 的名字和描述，agent 判断用得上时才读全文，装再多也不会拖慢普通问题。
手册只作经验参考，结论仍以日志和源码证据为准；手册里执行脚本、改文件之类的步骤会被忽略（所有工具都是只读的）。

## 终端显示

- 两种模式都是**流式输出**：报告按 Markdown 块边写边落到屏幕上，底部常驻状态栏实时显示
  当前阶段（思考中 / 正在查看日志 / 正在阅读源码 / 正在撰写）、耗时、token 与工具次数。
- 运行中的工具带 spinner 和计时，完成后收敛成一行：`✓ ≡ 搜索日志  app.log  "ERROR"  命中 23 行 · 0.3s`。
- 不加 `-v` 时过程信息只在底部状态栏滚动、结束即消失，屏幕上只留报告；加 `-v` 会把每步都保留下来。
- 子代理的每一步缩进显示在对应的"委派子任务"下面（运行中只滚动显示最近 3 步），
  并行的多个子代理各自计时；token 与工具次数统计包含子代理，JSON 报告里子代理的调用带 `subagent` 字段。
- 同一次运行里对同一份日志、同一时间窗口重复调用"日志概览"会直接命中缓存（摘要里标"缓存"），
  日志文件被改写后自动失效。

### Windows 兼容

- 自动开启控制台 VT 模式，cmd / PowerShell / Windows Terminal / VS Code 终端 / Git Bash 表现一致，刷新不闪烁。
- 中文 Windows 的经典控制台（非 UTF-8 代码页）会把 `─ ○ ✓` 等符号画成双宽，导致边框错位、残影叠行，
  这种环境会**自动切换为纯 ASCII 字形**。也可以手动指定：

  ```powershell
  $env:LOG_AGENT_GLYPHS="ascii"    # 强制 ASCII
  $env:LOG_AGENT_GLYPHS="unicode"  # 强制 Unicode（例如已 chcp 65001）
  ```

- 输出重定向到文件（`log-agent analyze ... > report.txt`）时统一写 UTF-8，不会因编码报错中断。
- 遵循 `NO_COLOR` 环境变量关闭颜色。

## 安全说明

- 所有工具均为**只读**，agent 不会修改你的日志或源码。
- 日志/源码内容会发送给模型服务。工具输出默认先脱敏：日志中的 token / 密码 / 手机号 / 身份证（带校验位校验，
  不会误伤订单号）/ 邮箱 / IP（同一 IP 映射为同一代号，仍能区分机器）会被打码；源码只打明确的密钥
  （`sk-`、AccessKey、JWT、Bearer），不改动代码本身。规则无法覆盖所有业务字段，高敏数据建议改用本地模型。

## 开发

```bash
uv sync
uv run pytest          # 单元测试 + 基于剧本模型的端到端测试，不需要 API Key
uv run ruff check src tests
```

CI 在 Ubuntu / Windows / macOS × Python 3.11 / 3.13 上运行同一套测试。其中 `tests/test_smoke.py`
用真实子进程跑完整的 `analyze`（只把模型换成脚本），覆盖 Windows 默认 GBK 控制台、中文 / 带空格路径、
日志和源码不在同一盘符等场景；也可以手动跑 `uv run python -m tests.smoke_driver <日志> src <输出.json>` 看实际终端效果。

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
