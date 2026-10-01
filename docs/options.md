# 参数参考

[← 返回 README](../README.md) · [文档目录](README.md)

| 参数 | 简写 | 说明 |
|------|------|------|
| `--log` | `-l` | 日志文件（必填，可重复、支持通配符 / `.gz` / `-`） |
| `--code` | `-c` | 源码目录（可选，可重复） |
| `--skills` | | 额外的 skill 目录（可重复），见 [Skills（排查手册）](memory-and-skills.md#skills排查手册) |
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
| `--session` | `-s` | 会话名：analyze 存档用的名称（默认自动生成），chat 续上已有会话 |
| `--no-save` | | analyze 不保存为会话；也可用 `LOG_AGENT_NO_SAVE=1` 或配置 `no_save = true` |
| `--db` | | 会话库路径，默认 `~/.log-agent/sessions.db`（analyze / chat / serve） |
| `--fail-on` | | `high` / `medium` / `low`：发现问题且可信度不低于该级别时退出码为 3（analyze） |
| `--verbose` | `-v` | 保留每一步工具调用（含结果摘要、耗时）的完整记录 |
| `--memory` | | 长期记忆：`suggest`（默认）/ `explicit` / `off`，见[长期记忆](memory-and-skills.md#长期记忆) |

模型接口默认单次请求超时 120 秒、失败自动重试 3 次（连接失败、超时、429、5xx），可用环境变量调整：
`LOG_AGENT_TIMEOUT=300`、`LOG_AGENT_MAX_RETRIES=5`。重试时状态栏会提示"接口波动，自动重试第 N 次"；
重试用尽仍失败时给出中文原因（401 / 404 / 超时等），已经输出的部分报告照常保存到 `-o`。

`--timezone` 对 `analyze`、`chat`、`watch` 都生效，优先级为命令行 > 环境变量 > 配置文件 > UTC。
带完整日期和偏移的时间按实际时刻比较，纯时刻边界（如 `16:00`）按配置时区比较。
只有时刻、没有日期的日志仍按一天中的时刻排序，不能据此还原跨天先后关系。
Windows 等环境若没有 IANA 时区数据，可使用 `+08:00` 这样的固定偏移；固定偏移不包含夏令时规则。
