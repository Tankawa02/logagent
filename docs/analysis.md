# 分析能力：报告导出、证据回查、git 关联、异常链

[← 返回 README](../README.md) · [文档目录](README.md)

## 报告交接与结构化导出

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

## 证据回查

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

## 关联代码变更（git）

`-c` 指向的源码目录是 git 仓库（或仓库的子目录）时，agent 多了三个只读工具：

| 工具 | 作用 |
| --- | --- |
| `recent_changes` | 某段时间内的提交（`git log`），附改动文件与增删行数，可按路径过滤 |
| `blame_lines` | 源码某几行最后一次由哪个提交修改（`git blame`，针对 HEAD 版本；工作区与 HEAD 不同时会提示行号可能对不上） |
| `show_commit` | 某个提交的说明与 diff（`git show`），合并提交显示相对第一父提交的改动 |

典型链路：日志确定问题从 14:02 开始 → `recent_changes` 查之前 1～3 天的提交 → 对栈帧指向的行 `blame_lines`
→ 可疑提交 `show_commit` 看改动。提交时间按 `--timezone` 显示，便于和日志直接对比；
提交时间不等于上线时间，agent 在把提交列为根因时会说明这一点。

```bash
# 问题里说清时间，agent 会自己去查这段时间前后的提交
log-agent analyze -l app.log -c ./repo --timezone +08:00 -q "14:02 之后开始大量 KeyError，是不是最近的改动引起的？"
```

需要本机装有 git；不是 git 仓库时这些工具只返回提示，不影响其它分析。提交说明按日志规则脱敏，diff 按源码规则脱敏。

## 异常链聚类

`log_overview` / `inspect` 会把一段报错连同后面的堆栈续行解析成异常链，按**根因异常 + 首个业务栈帧**聚类：

```
异常链（按根因异常 + 首个业务栈帧聚类，共 2 类；同一根因被不同外层包装时会合并）：
  1. x3  首次 L1  java.sql.SQLTimeoutException: Query timed out after 3000ms
       位置：com.acme.order.repo.OrderRepo.findById (OrderRepo.java:86)
       外层：org.springframework.dao.QueryTimeoutException
       出现在：L1、L20、L47
```

| 语言 | 识别内容 |
| --- | --- |
| Java / Kotlin / Scala | `Caused by:` 逐层深入，最后一个是根因；`Suppressed:`、`... N more` 忽略；异常挂在日志首行末尾也能识别 |
| Python | 多段 traceback 由 “During handling…” / “The above exception was the direct cause…” 连接，第一段是根因 |
| Go | `panic:` / `fatal error:` + goroutine 栈，跳过 `runtime.*` |
| Node.js | `TypeError: …` + `at fn (file:line:col)`，`[cause]:` 视为更深一层 |
| .NET | `A ---> B`，最内层是根因；按 `--- End of inner exception stack trace ---` 分配栈帧 |

- **业务栈帧**：第一个不属于标准库 / 常见框架（Spring、Netty、Hikari、site-packages、node_modules、Go runtime 等）的帧。
  也可以在配置里写 `app_packages = ["com.acme", "app/"]` 指定业务包前缀或路径片段，优先级最高。
- 聚类键不含行号和消息里的数字 / id / 引号内容，发布后行号变了也能归到同一类。
- JSON 日志的 `stack_trace` / `stack` / `exception` / `error.stack_trace` 等字段、Docker / CRI 逐行包装的堆栈同样支持。
- 栈帧里的包名（如 `com.acme.error.Handler`）不再被误识别成 ERROR 级别，堆栈也不会被拆散。
- `compare_windows` 会列出目标时段**新出现的根因异常**。
