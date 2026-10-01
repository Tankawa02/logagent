# 使用

[← 返回 README](../README.md) · [文档目录](README.md)

## 分析前的本地检查

```bash
# 无需 API Key，不调用模型；支持多文件、通配符、gzip 和管道
log-agent inspect -l app.log --timezone +08:00 --since 14:00 --until 14:30

# 查看实际配置及来源、检查依赖版本和 API Key 是否已设置
log-agent doctor
log-agent doctor --command chat
log-agent doctor --command inspect

# 再真正连一次模型服务：验证 Key、网关和模型名是否可用
log-agent doctor --ping
log-agent doctor --ping --ping-timeout 60
```

`inspect` 会完整扫描日志，展示文件大小、编码、首末时间、时间戳和级别的识别比例、窗口覆盖行数、
高频错误和最多 3 条样例。样例默认脱敏；堆栈续行没有独立时间戳或级别属于常见情况，识别比例不等于解析准确率。
空文件、无法识别时间、窗口没有数据都会给出提示。成功读取退出码为 0，读取失败为 1，参数错误为 2。

`doctor` 展示命令的有效配置，区分内置默认、配置文件和环境变量；网关 URL 仅显示协议和主机，API Key 不显示原文。
默认不连接模型服务，所以“Key 已设置”不表示 Key 有效或网关可访问；加 `--ping` 会用有效配置里的模型和网关，
走和 `analyze` 完全相同的客户端路径发一次最小请求（一句 ping、输出上限 16 tokens、不重试、默认 20 秒超时），
报告延迟和网关实际返回的模型名；provider 不支持设置超时 / 重试时直接报告不支持，而不是退回到不受控的默认重试；
客户端卡住时也最多等 `--ping-timeout` + 2 秒；失败时把 401 / 403 / 404 / 429 / 超时 / 连不上等翻译成可操作的提示。
`--command inspect` 不调用模型，`--ping` 会跳过。检查发现问题（含 `--ping` 失败）退出码为 1，参数或配置解析错误为 2。
依赖缺失或版本不匹配时，可运行 `uv sync` 按项目锁文件同步。

工具提供两种模式：

- **`analyze`** — 单次一问一答，跑完出一份报告就结束。适合快速排查；结果默认存为会话，之后能在网页里看、继续追问。
- **`chat`** — 多轮对话，agent 记住整段对话和已读过的日志，可以连续追问。适合深入排查。

## 单次分析（analyze）

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
log-agent analyze -l app.log -c ./repo --fail-on medium -o result.json --no-save

# 给这次分析起个会话名，方便之后找回 / 续问
log-agent analyze -l app.log -c ./repo -s incident-0609
```

### 分析结果存为会话

每次 `analyze` 默认都会存进和 `chat` 相同的会话库（`~/.log-agent/sessions.db`），结束时会提示会话名：

```text
✓ 已保存为会话 analyze-20260609-165130  ·  log-agent serve 在网页里查看  ·  log-agent chat -s analyze-20260609-165130 继续追问
```

- `log-agent serve` 打开网页：错误时间线、报告与证据左右对照、分享链接，见 [Web 界面](web.md)。
- 想接着问：网页里点「追问」，或在终端 `log-agent chat -s <会话名>`。会话保存了本轮完整对话，
  续问时 agent 知道刚才查过什么、得出了什么结论，沿用同一批日志、源码和时间窗口。
- 管道输入（`-l -`）的日志落盘在 `~/.log-agent/stdin/`，存档和续问都能正常读到。
- 不想留存档（CI、定时巡检）：加 `--no-save`，或设环境变量 `LOG_AGENT_NO_SAVE=1`，或在配置的 `[analyze]` 段写 `no_save = true`。
  会话库打不开（只读目录等）时只提示、不影响分析结果与退出码。
- 清理：`log-agent sessions list` 查看，`log-agent sessions rm <会话名>` 删除（分享链接随之失效）。

退出码：`0` 成功，`1` 失败（如达到 `--max-steps` 上限），`2` 参数错误，`130` 被 Ctrl+C 中断。
加了 `--fail-on` 时另有：`3` 发现问题且可信度达到门槛，`4` 结构化报告缺失、校验失败或判定为 `unknown`。
是否发现问题由校验后的 `analysis.assessment` 决定，不再依赖结论措辞；
JSON 导出里对应 `finding`（`true` / `false` / `null`）、`confidence` 和 `budget_hit` 字段。

基线对比按错误次数占窗口日志行数的比例判断升降，并同时展示次数与比例；
“明显增多”要求出现率至少翻倍、目标窗口至少出现 5 次，避免将极少量样本直接判成激增。
这里的比例是**日志行出现率**，并非请求失败率（堆栈行、日志级别配置也会影响分母）。

JSON 日志的级别、消息、时间字段及其它内置格式见[日志格式](log-formats.md)。聚类时保留 `status`、`status_code`、`statusCode`、`http_status`、`code`、`error_code`、`errorCode` 字段；
当前支持单行 JSON 对象（不超过 65,536 个字符），没有级别字段的 JSON 不从消息正文猜测级别。

## 追踪模式（watch）

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

## 多轮对话（chat）

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
| `/remember [-g] <内容>` | 记住一条偏好或项目知识，`-g` 存为全局，见[长期记忆](memory-and-skills.md#长期记忆) |
| `/memory` | 查看记忆；`/memory review` 处理待确认的建议，`/memory edit <编号> <新内容>` 修改 |
| `/forget <编号…>` | 删除记忆 |
| `/help` | 显示命令列表 |

范围或来源变更立即保存，并在下一次提问时同步给 agent；不会自动发起新的分析。
