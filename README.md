# log-agent

基于 [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 的命令行日志分析智能体。
它会结合**日志文件**和**源代码目录**，自动检索异常、关联代码，最终输出根因分析与修复建议。

## 功能

- 输入日志文件路径 + 源码目录路径，自动定位问题根因
- 两种模式：`analyze` 单次分析，`chat` 多轮对话（连续追问，记住上下文）
- 本地预检查：`inspect` 查看编码、识别覆盖率和窗口样例；`doctor` 检查有效配置和依赖，不调用模型
- 内置只读工具：日志概览（级别分布 / 高频错误 / 异常类型）、分块读日志、带上下文搜索日志、
  列源码、按行号读源码、grep 源码（装了 `rg` 会自动用 ripgrep 加速）
- 按请求追踪：给一个 traceId / requestId / 手机号，跨多份日志把同一请求的所有行按时间合并排好，连带堆栈
- 日志级别支持大小写与 JSON 字段；错误聚类保留 HTTP 状态码、业务错误码，避免把不同原因合并
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
timezone = "+08:00"         # 无偏移日志和时间边界使用的时区；默认 UTC

[analyze]                   # 只对 analyze 生效
verbose = true
```

配好以后命令可以缩成 `log-agent analyze -l app.log -q "为什么降级到 nexmo"`。

- 查找顺序：用户级 `~/.log-agent/config.toml` → 项目级 `.log-agent.toml`（从当前目录逐级向上找最近的一个，覆盖用户级）；
  也可以用环境变量 `LOG_AGENT_CONFIG` 直接指定文件。
- 优先级：命令行参数 > 环境变量（`LOG_AGENT_MODEL`、`OPENAI_BASE_URL` 等）> 配置文件 > 内置默认值。
- 支持的键：`model`、`base_url`、`code`、`encoding`、`timezone`、`no_redact`、`max_steps`、`verbose`、`timeout`、`max_retries`，
  以及 chat 专用的 `db`。写错的键会给出提示并忽略。
- **API key 不支持写进配置文件**，仍然用 `OPENAI_API_KEY` 环境变量，避免 key 跟着项目文件被提交。

## 使用

### 分析前的本地检查

```bash
# 无需 API Key，不调用模型；支持多文件、通配符、gzip 和管道
log-agent inspect -l app.log --timezone +08:00 --since 14:00 --until 14:30

# 查看实际配置及来源、检查依赖版本和 API Key 是否已设置
log-agent doctor
log-agent doctor --command chat
log-agent doctor --command inspect
```

`inspect` 会完整扫描日志，展示文件大小、编码、首末时间、时间戳和级别的识别比例、窗口覆盖行数、
高频错误和最多 3 条样例。样例默认脱敏；堆栈续行没有独立时间戳或级别属于常见情况，识别比例不等于解析准确率。
空文件、无法识别时间、窗口没有数据都会给出提示。成功读取退出码为 0，读取失败为 1，参数错误为 2。

`doctor` 展示命令的有效配置，区分内置默认、配置文件和环境变量；网关 URL 仅显示协议和主机，API Key 不显示原文。
它不连接模型服务，所以“Key 已设置”不表示 Key 有效或网关可访问。检查发现问题退出码为 1，参数或配置解析错误为 2。
依赖缺失或版本不匹配时，可运行 `uv sync` 按项目锁文件同步。

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

# 日志混用 UTC 和北京时间时：无偏移时间按 +08:00 解释，已有偏移保留
log-agent analyze -l gateway.log -l order.log --timezone +08:00 --since "2026-09-28T16:00:00+08:00"

# 保留每一步工具调用（含结果摘要、耗时）的完整记录
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

# 和正常时段对比："为什么 14 点后突然变多"——agent 会先拿到两段的级别分布与新出现 / 明显增多的错误
log-agent analyze -l app.log -c ./repo --since 14:00 --until 14:30 --baseline "13:00~13:30"

# 限制单轮 tokens：用到 80% 时 agent 停止取证、基于已有证据收尾出报告（证据不足处会标"待确认"）
log-agent analyze -l huge.log -c ./repo --budget 200k

# 接入 CI / 定时巡检：发现问题且可信度不低于 medium 时退出码为 3
log-agent analyze -l app.log -c ./repo --fail-on medium -o result.json
```

退出码：`0` 成功，`1` 失败（如达到 `--max-steps` 上限），`2` 参数错误，`130` 被 Ctrl+C 中断。
加了 `--fail-on` 时另有：`3` 发现问题且可信度达到门槛，`4` 结构化报告缺失、校验失败或判定为 `unknown`。
是否发现问题由校验后的 `analysis.assessment` 决定，不再依赖结论措辞；
JSON 导出里对应 `finding`（`true` / `false` / `null`）、`confidence` 和 `budget_hit` 字段。

基线对比按错误次数占窗口日志行数的比例判断升降，并同时展示次数与比例；
“明显增多”要求出现率至少翻倍、目标窗口至少出现 5 次，避免将极少量样本直接判成激增。
这里的比例是**日志行出现率**，并非请求失败率（堆栈行、日志级别配置也会影响分母）。

JSON 日志优先读取 `level` / `severity` / `levelname` / `severityText` 级别、`message` / `msg` 消息，
以及 `timestamp` / `@timestamp` / `time` / `ts` 字符串时间戳，字段顺序不影响识别。
聚类时保留 `status`、`status_code`、`statusCode`、`http_status`、`code`、`error_code`、`errorCode` 字段。
当前支持单行 JSON 对象（不超过 65,536 个字符）；没有级别字段的 JSON 不从消息正文猜测级别。

### 追踪模式（watch）

像 `tail -F` 一样盯着日志，出现新的 ERROR / FATAL（或 `--pattern` 匹配的行）时，把这一波攒齐后自动分析一次：

```bash
# 复现问题时开着，看到报告就知道刚才那一下发生了什么
log-agent watch -l app.log -c ./repo

# 只关心某类报错；分析一次就退出
log-agent watch -l app.log -c ./repo --pattern "Timeout|Refused" --once

# 错误持续刷屏时拉长攒批和间隔，避免反复消耗 tokens
log-agent watch -l app.log --debounce 30 --cooldown 600 --budget 150k
```

只分析启动之后新写入的行；日志被截断或轮转时会自动从头继续跟随。`--debounce`（默认 10 秒）是新错误停止出现多久后开始分析，
一直在刷的话最多攒 60 秒；`--cooldown`（默认 120 秒）是两次分析的最小间隔。分析中按 `Ctrl+C` 只中断这一次，空闲时按才退出。

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

新会话开场时会先在本地扫一遍日志（不调用模型、不耗 tokens），列出出现次数最多的几类错误作为候选问题，
输入编号即可直接提问，也可以忽略它们自己输入问题。日志里没有 ERROR / FATAL 时不显示。

输入 `exit` / `quit` / `退出` / `结束` 即可结束对话。回答过程中按 `Ctrl+C` 只中断当前这一轮，
已经输出的内容会保留，可以接着追问；在输入提示符处按 `Ctrl+C` 才会退出。

**会话持久化**：对话历史保存在本地 SQLite（默认 `~/.log-agent/sessions.db`），关掉终端后还能续上。用 `--session` 给会话命名，不同名称互相隔离；用相同名称即可恢复之前的对话：

```bash
# 开一个名为 payment-bug 的会话
log-agent chat -l app.log -c ./repo --session payment-bug

# 关掉终端后，只写会话名就能续上：自动沿用上次的日志与源码
log-agent chat --session payment-bug

# 续上最近一次会话
log-agent chat --resume

# 续会话时换一份日志（传了 -l / -c 就以新传入的为准）
log-agent chat -l app-new.log --session payment-bug

# 自定义数据库文件位置
log-agent chat -l app.log --session payment-bug --db ./my-sessions.db

# 查看 / 筛选 / 删除会话
log-agent sessions list
log-agent sessions list --search gateway   # 按会话名、首个问题或日志路径筛选
log-agent sessions rm payment-bug
```

续会话时如果换了日志或源码，agent 会在下一条消息里被告知新路径，不会继续引用旧文件。上次的日志已被删除或移走时会提示你用 `-l` 重新指定。

会话同时保存模型、时间窗口、时区、基线、编码、预算和最大推理步数。恢复时这些设置的优先级是：
**显式命令行参数 / 环境变量 > 会话中保存的设置 > 配置文件 > 内置默认值**。
连接地址、凭据和脱敏开关仍使用当前运行配置，不从历史会话恢复。
启动时展示上次的问题与结果摘要，可立即 `/save`、`/copy` 或 `/retry`。

每轮报告都保存当时的日志/源码列表、分析设置、生成时间和用量。追加或移除日志、调整范围后再 `/save`，
导出的仍是该回答生成时的快照。`/new` 会清空可复制、保存和重试的上一轮结果，并沿用当前来源与分析设置。
旧版数据库自动兼容：若没有逐轮快照，会尝试从历史恢复回答，并标注“来源未知”，不补造当时的来源和设置。

输入框支持方向键翻历史（跨会话保存在 `~/.log-agent/history`）、`Ctrl+R` 反向搜索，以及斜杠命令（输入 `/` 自动补全）；
`/add-log`、`/add-code`、`/remove-log`、`/save`、`/save-brief` 和 `/save-ticket` 的路径参数支持 Tab 补全。

| 命令 | 说明 |
|------|------|
| `/history [关键词]` | 按原始轮次列出问题、结论、生成时间和状态；可搜索问题和回答 |
| `/show <轮次>` | 查看该轮完整报告及原始来源、时间范围 |
| `/save [--turn 轮次] [路径]` | 保存上一条回答的详细分析（`.json` 结尾则存 JSON） |
| `/save-brief [--turn 轮次] [路径]` | 保存速览：结论、影响、下一步 |
| `/save-ticket [--turn 轮次] [路径]` | 保存工单：现象、复现条件、证据、建议、验证方法 |
| `/copy` | 把上一条回答（Markdown 原文）复制到剪贴板，方便贴进工单或群聊 |
| `/retry [补充]` | 重新回答上一个问题，比如回答被中断或答偏了；可以附一句补充，如 `/retry 重点看 14:02 之后` |
| `/add-log <路径>` | 排查中途给当前会话追加日志（支持通配符），不用退出重开，前面的对话都保留 |
| `/add-code <目录>` | 追加源码目录，比如问题牵涉到另一个服务 |
| `/remove-log <路径或唯一文件名>` | 从会话移除日志，不删除文件；至少保留一份日志，同名文件可用完整路径区分 |
| `/window <起点~终点>` | 调整时间窗口，如 `/window 14:00~14:30`；`/window off` 取消限制 |
| `/baseline <起点~终点>` | 设置正常时段，如 `/baseline 13:00~13:30`；`/baseline off` 取消 |
| `/settings` | 查看有效模型、范围、时区、基线、编码、预算、最大步数及脱敏状态 |
| `/new` | 开一个新会话 |
| `/sources` | 查看当前日志与源码 |
| `/stats` | 查看本次运行累计的轮次、耗时、工具次数与 tokens |
| `/remember [-g] <内容>` | 记住一条偏好或项目知识，`-g` 存为全局，见下方「长期记忆」 |
| `/memory` | 查看记忆；`/memory review` 处理待确认的建议，`/memory edit <编号> <新内容>` 修改 |
| `/forget <编号…>` | 删除记忆 |
| `/help` | 显示命令列表 |

范围或来源变更立即保存，并在下一次提问时同步给 agent；不会自动发起新的分析。

## 参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--log` | `-l` | 日志文件（必填，可重复、支持通配符 / `.gz` / `-`） |
| `--code` | `-c` | 源码目录（可选，可重复） |
| `--skills` | | 额外的 skill 目录（可重复），见下方「Skills（排查手册）」 |
| `--question` | `-q` | 想让 agent 回答的具体问题（analyze） |
| `--view` | | 导出用途：`brief` 速览、`detailed` 详细分析（默认）、`ticket` 工单（analyze） |
| `--output` / `--format` | `-o` / `-f` | 导出报告到文件，`markdown` 或 `json`（analyze） |
| `--since` / `--until` | | 只分析该时间窗口内的日志，支持 `2026-06-09 14:00`、`2026-06-09T14:00:30`、`2026-06-09`、`14:00`；agent 需要对比时仍可显式查窗口外 |
| `--model` | `-m` | 模型，`provider:model` 格式，默认读 `LOG_AGENT_MODEL`，否则 `openai:gpt-4.1` |
| `--encoding` | | 强制日志编码，默认自动探测（也可设 `LOG_AGENT_ENCODING`） |
| `--timezone` | | 无偏移日志及时间边界的时区，默认 `UTC`；支持 `+08:00` 等固定偏移及系统提供的 IANA 名称，也可设 `LOG_AGENT_TIMEZONE` 或配置文件的 `timezone` |
| `--no-redact` | | 关闭敏感信息脱敏 |
| `--max-steps` | | 单轮最大推理步数，默认 120 |
| `--budget` | | 单轮 tokens 上限，如 `200k`、`1.5m`；用到 80% 时自动收尾出报告（也可写进配置文件） |
| `--baseline` | | 正常时段，如 `13:00~13:30`（带日期时用 `~` 分隔），让 agent 先做前后对比（analyze / chat） |
| `--fail-on` | | `high` / `medium` / `low`：发现问题且可信度不低于该级别时退出码为 3（analyze） |
| `--verbose` | `-v` | 保留每一步工具调用（含结果摘要、耗时）的完整记录 |
| `--memory` | | 长期记忆：`suggest`（默认）/ `explicit` / `off`，见下方「长期记忆」 |

模型接口默认单次请求超时 120 秒、失败自动重试 3 次（连接失败、超时、429、5xx），可用环境变量调整：
`LOG_AGENT_TIMEOUT=300`、`LOG_AGENT_MAX_RETRIES=5`。重试时状态栏会提示"接口波动，自动重试第 N 次"；
重试用尽仍失败时给出中文原因（401 / 404 / 超时等），已经输出的部分报告照常保存到 `-o`。

`--timezone` 对 `analyze`、`chat`、`watch` 都生效，优先级为命令行 > 环境变量 > 配置文件 > UTC。
带完整日期和偏移的时间按实际时刻比较，纯时刻边界（如 `16:00`）按配置时区比较。
只有时刻、没有日期的日志仍按一天中的时刻排序，不能据此还原跨天先后关系。
Windows 等环境若没有 IANA 时区数据，可使用 `+08:00` 这样的固定偏移；固定偏移不包含夏令时规则。

## 长期记忆

agent 会跨会话记住三类信息：**表达偏好**（"以后先写结论""报告别超过一屏"）、**术语**（"老通道指 Nexmo"）
和**项目事实**（"prod 的短信都走 gateway-b"）。每轮开始时把它们附在系统提示词末尾，删改下一轮就生效。

- **只有你确认过的才会被记住。** 你说"记住……""以后都……"或输入 `/remember <内容>` 时直接保存；
  agent 在对话中发现值得记的内容（比如你纠正了它）只能**提议**，本轮结束后由你选择
  `[y] 保存 / [n] 不保存 / [e] 编辑 / [x] 不再提示 / [s] 稍后`。
- **不会动不动就问。** 只出现一次的普通提及先攒着，在两个不同会话里都出现过才在会话结束时问一次；
  30 天没再出现的候选自动作废。拒绝过的内容 90 天内不再提（选 `x` 则永久不提）。
- **相似内容不会自动覆盖。** 完全相同的内容去重；保存相似内容时选择 `a` 新增或 `r 编号` 替换指定记忆，默认稍后处理。
  `memory add` 未保存（包括输入结束、取消或无效选择）时返回退出码 1。
  候选只在类型、项目范围相同且差异仅为常见解释措辞时合并；已保存的全局和当前项目记忆也参与重复建议过滤。
  保存或修改记忆会清理可见范围内的同类型等价候选；确认旧候选时会再次检查，避免重复保存。
  当前候选经编辑后，遵循你明确选择的新增或替换；全局清理通过索引预筛选等价候选，旧记忆库首次打开时自动迁移。
- **范围**：表达偏好默认全局；术语和项目事实归属于当前项目（第一个 `-c` 源码目录所在的 git 仓库），
  换项目不会互相干扰。没传 `-c` 时只用全局记忆；通过 `/add-code` 加入首个源码目录后，后续读写切换到该项目，原有全局记忆不迁移。
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


### 报告交接与结构化导出

```bash
# 速览：结论、影响范围、下一步
log-agent analyze -l app.log -o brief.md --view brief
# 工单：现象、复现条件、证据、根因假设、建议与验证方法
log-agent analyze -l app.log -o ticket.md --view ticket
# 完整结构化数据 + 详细分析，接入自动化
log-agent analyze -l app.log -o result.json --view detailed --fail-on medium
```

`--format` 选择文件类型（Markdown / JSON），`--view` 选择导出用途；终端仍流式显示分析正文。
三种用途从同一份结果本地生成，不额外调用模型。chat 中使用 `/save`、`/save-brief`、`/save-ticket`，
路径以 `.json` 结尾即导出 JSON。恢复会话后也可直接导出，无需重新分析。

JSON `schema_version: 2` 保留原有 `report`、`logs`、`code`、`settings`、用量和工具记录，新增：

| 字段 | 含义 |
| --- | --- |
| `analysis.assessment` | `finding` 发现问题 / `clear` 检查后未发现问题 / `unknown` 无法判定 |
| `analysis.confidence` | `high` / `medium` / `low` |
| `analysis.conclusion`、`impact`、`next_steps` | 结论、影响范围、下一步 |
| `analysis.issues[]` | 每个问题的 `title`、`symptoms`、`impact` |
| `issues[].evidence[]` | `source`、`line_start`、`line_end`、`excerpt`；保留脱敏原文 |
| `issues[].root_cause_hypotheses[]` | `explanation`、`confidence`、`reasoning`；区分假设与证据 |
| `issues[].open_questions`、`recommendations` | 待确认项、处理建议（字符串列表） |
| `issues[].reproduction_conditions`、`verification_steps` | 复现条件与验证方法（字符串列表） |
| `analysis.open_questions` | 整轮分析的待确认项 |
| `structured_status` | `valid` / `missing` / `invalid`，结构化字段校验结果 |
| `evidence_check` | 证据回查结果：`status`、各状态计数与逐条 `items`，见下方「证据回查」 |
| `view`、`rendered_report` | 所选用途及其 Markdown 正文；JSON 始终保留全部结构化数据 |

`settings` 记录分析当时的 `since`、`until`、`timezone`、`baseline` 等设置；`logs`、`code` 记录当时的来源路径，
`generated_at` 记录原生成时间。以后修改会话范围或来源不会改写已保存的报告快照。
来源路径不是文件内容快照；原文件修改或删除后，引用内容不保证仍可复查。

模型在正文末尾提供专用 JSON 附录，本地严格校验字段类型、枚举、行号范围和判定与问题列表的一致性。
这属于格式校验，不代表证据与根因已经人工核实。缺失或无效时 `analysis: null`、`finding: null`，
保留原始回答并提示无法判定；旧版纯文本报告仍可导出，但不会从文字猜造问题列表。
中断或失败时即使已有结构化数据，顶层 `finding` 仍为 `null`，应先检查 `status`。
已有自动化若依赖“未发现异常”措辞，需要改为读取显式判定；`--fail-on` 遇到缺失数据会返回 `4`，不会当作通过。

### 证据回查

每轮分析结束后，程序会把结构化报告里的每条证据按 `source` 和行号**在本地重新读取原文**，
以工具当时给模型看的样子（同样的脱敏）核对 `excerpt`，不额外调用模型：

| 状态 | 含义 |
| --- | --- |
| `verified` | 摘录出现在所引行内 |
| `shifted` | 摘录真实存在，但落在所引行附近 5 行内（行号记偏），会给出实际行号 |
| `mismatch` | 所引行找不到摘录，或行号超出文件范围——引用很可能是编造或记错的 |
| `unresolved` | 来源对应不到本次的日志 / 源码，或读取失败，无法核对 |

比对只为抓编造，不挑措辞：会去掉 `42: `、`42 | ` 这类行号前缀，按 `…` 和截断提示分段匹配，
忽略空白与全半角差异，仍不命中时允许极小的字符差异。

- 终端里在报告后给出一行结果；有问题的证据逐条列出（最多 3 条）。
- Markdown 导出在每条证据后标注 `✓ 已核对原文` / `△ 行号偏移` / `✗ 与原文不符` / `? 无法核对`，元信息里有汇总。
- JSON 导出新增 `evidence_check`，`items[]` 含 `issue`、`index`、`status`、`note`、`actual_start` / `actual_end`。
- `--fail-on`：所有证据都未通过核对时退出码为 `4`（无法判定）；部分不符时按**降一级**的可信度判断门槛。
  报告里模型给出的 `confidence` 原样保留，不被改写。

### 关联代码变更（git）

`-c` 指向的源码目录是 git 仓库（或仓库的子目录）时，agent 多了三个只读工具：

| 工具 | 作用 |
| --- | --- |
| `recent_changes` | 某段时间内的提交（`git log`），附改动文件与增删行数，可按路径过滤 |
| `blame_lines` | 源码某几行最后一次由哪个提交修改（`git blame`），会标出未提交的本地修改 |
| `show_commit` | 某个提交的说明与 diff（`git show`），合并提交显示相对第一父提交的改动 |

典型链路：日志确定问题从 14:02 开始 → `recent_changes` 查之前 1～3 天的提交 → 对栈帧指向的行 `blame_lines`
→ 可疑提交 `show_commit` 看改动。提交时间按 `--timezone` 显示，便于和日志直接对比；
提交时间不等于上线时间，agent 在把提交列为根因时会说明这一点。

```bash
# 问题里说清时间，agent 会自己去查这段时间前后的提交
log-agent analyze -l app.log -c ./repo --timezone +08:00 -q "14:02 之后开始大量 KeyError，是不是最近的改动引起的？"
```

需要本机装有 git；不是 git 仓库时这些工具只返回提示，不影响其它分析。提交说明按日志规则脱敏，diff 按源码规则脱敏。

## 终端显示

- 两种模式都是**流式输出**：报告按 Markdown 块边写边落到屏幕上，底部常驻状态栏实时显示
  当前阶段（思考中 / 正在查看日志 / 正在阅读源码 / 正在撰写）、耗时、token 与工具次数。
- 运行中的工具带 spinner 和计时，完成后收敛成一行：`✓ ≡ 搜索日志  app.log  "ERROR"  命中 23 行 · 0.3s`。
- 不加 `-v` 时过程信息只在底部状态栏滚动、结束即消失，屏幕上只留报告；加 `-v` 会把每步都保留下来。
- 子代理的每一步缩进显示在对应的"委派子任务"下面（运行中只滚动显示最近 3 步），
  并行的多个子代理各自计时；token 与工具次数统计包含子代理，JSON 报告里子代理的调用带 `subagent` 字段。
- 默认模式下工具过程只在运行时显示，结束后折叠成一行，例如 `查了 6 步：日志概览 → 搜索日志 ×3 → 委派子任务`；
  加 `-v` 会保留每一步的完整记录。
- 完整报告的第一行是**一句话结论 + 可信度**（高 / 中 / 低），终端里显示成高亮摘要；
  导出的 JSON 里对应 `summary` 与 `confidence` 字段，有有效结构化报告时以结构化结论为准。
- 报告里的 `app.log:42`、`app/order.py:88` 这类引用可以直接点击（终端超链接 OSC 8）：
  VS Code / Cursor 内置终端里跳到对应行，其它终端（iTerm2、Windows Terminal、GNOME Terminal 等）打开对应文件。
  用 `LOG_AGENT_LINKS` 调整：`vscode` 总是生成 `vscode://` 链接，`file` 总是生成 `file://`，`off` 关闭。
  只影响终端显示，导出的报告保持原文，不写入本机绝对路径。
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
- git 工具只执行 `log` / `show` / `blame` / `status` / `rev-parse`，关闭分页器、外部 diff、textconv 与交互提示，不会改动仓库。
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
