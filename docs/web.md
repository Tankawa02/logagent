# Web 界面

[← 返回 README](../README.md) · [文档目录](README.md)

`log-agent serve` 在浏览器里打开 `analyze` 和 `chat` 保存的会话（`analyze` 默认每次都会存档，见[分析结果存为会话](usage.md#分析结果存为会话)）。它读的是同一个 SQLite 会话库（默认 `~/.log-agent/sessions.db`），
报告用的是同一份 `schema_version: 2` 结构化数据，所以终端和网页看到的结论、证据核对结果完全一致。

```bash
uv tool install --reinstall 'log-agent[web] @ git+https://github.com/yourorg/log-agent.git'   # 需要 web 额外依赖
log-agent analyze -l app.log -c ./src     # 照常分析，结果自动存为会话（chat 也一样）
log-agent serve                           # 打开终端里打印的链接（带 ?token=）
```

## 能做什么

- **错误时间线**：按时间分桶画出 ERROR / WARN / 全部日志量，自动标出错误尖峰（▲）。
  点一根柱子看该时段的错误数、最常见的错误签名和首次出现的行；有 API Key 时点「追问这个时段」，
  会把时段、错误数和主要签名整理成问题，直接在这个会话里续问一轮。
- **报告与证据左右对照**：左边是结构化报告（结论、影响、每个问题的证据 / 根因假设 / 建议 / 验证步骤），
  每条证据带核对状态（✓ 已核对原文、△ 行号偏移、✗ 与原文不符、? 无法核对）。点证据或正文里的 `app.log:42`，
  右边打开日志 / 源码对应行和上下文，可以继续向上、向下加载。行号偏移的证据直接跳到核对出的真实位置。
- **网页续问**：沿用会话的日志、源码、时间窗口、时区、基线和预算，过程实时显示工具调用，
  结束后和终端一样存成新的一轮（`log-agent chat -s <会话>` 回到终端也能接着问）。`analyze` 的存档也能续问，agent 带着那次分析的完整上下文。关页面或点「停止」按中断处理，已输出部分照常保存。
- **分享链接**：会话页点「分享」生成只读链接交给同事。同事不需要安装、不需要令牌，能看报告、时间线、证据原文并导出 Markdown / 工单 / JSON；
  不能续问，也看不到你的其他会话。
- **导出**：每轮报告都能下载 Markdown（详细 / 工单视图）或 JSON，内容与 `/save`、`/save-ticket` 相同。

## 启动参数

| 参数 | 说明 |
|------|------|
| `--host` / `--port` | 默认 `127.0.0.1:8765`。要让同事打开分享链接，用 `--host 0.0.0.0` 或放在反向代理后面 |
| `--db` | 会话库路径，默认与 `chat` 相同（也读配置文件里的 `db`） |
| `--public-url` | 生成分享链接用的外部地址，如 `https://logs.example.com`（经反向代理访问时设置） |
| `--token` | 指定本人访问令牌（也可用环境变量 `LOG_AGENT_WEB_TOKEN`），默认每次启动随机生成 |
| `--no-token` | 不校验令牌，只允许和本机地址一起使用 |
| `--read-only` | 不提供网页续问，不需要 API Key |
| `--no-redact` | 本人视图显示未脱敏的原文；分享链接始终脱敏 |
| `--open` | 启动后自动打开浏览器 |

网页续问需要和 `chat` 一样的 `OPENAI_API_KEY`（以及可选的 `OPENAI_BASE_URL` / `--base-url`）。没有 Key 时页面照常可看，只是没有「追问」页签。
同一个 `serve` 进程同时只跑一轮分析，另一轮进行中时再提问会提示稍后再试。

## 访问控制与安全

- **本人**：启动时打印 `http://127.0.0.1:8765/?token=…`。首次打开后令牌换成 HttpOnly、SameSite=Strict 的 Cookie 并从地址栏去掉；
  写操作（续问、创建 / 撤销分享）额外要求自定义请求头，防跨站请求。令牌相当于密码，不要转发。
- **分享链接**：形如 `/s/<随机串>`，有效期可选 1 小时 / 1 天 / 7 天 / 30 天 / 永久。库里只存链接的 SHA-256，
  完整链接只在创建时显示一次；在分享面板里随时撤销，`log-agent sessions rm` 删除会话时它的链接一起失效。
- 分享视图只能访问该会话登记过的日志文件和源码目录，路径穿越（`../`、目录外的绝对路径）一律拒绝；内容固定按默认规则脱敏。
- 页面设置 `Referrer-Policy: no-referrer` 和严格的 CSP，链接里的令牌不会经 Referer 泄露。
- 监听 `0.0.0.0` 时，同一网络里的人都能访问到端口（但没有令牌只能打开有效的分享链接）。对外暴露请放在 HTTPS 反向代理后面。

## 开发

前端在 `web/`（React + TanStack Router / Query / AI + Tailwind），构建产物提交在 `src/log_agent/web/static/`，
所以安装时不需要 Node。改了前端要重新构建并提交产物，CI 会检查两者一致。

```bash
uv sync --extra web
uv run python -m tests.web_preview /tmp/preview 8765   # 造一份带错误尖峰的示例会话，脚本化模型，不需要 Key
cd web && npm ci && npm run dev                        # http://localhost:5173，/api 代理到 8765
npm run build                                          # 写入 src/log_agent/web/static/
```

后端在 `src/log_agent/web/`：`app.py`（路由、鉴权）、`timeline.py`（按秒聚合再分桶、稳健 z 分数找尖峰）、
`sources.py`（证据原文，复用 `evidence.SourceResolver` 做路径约束）、`shares.py`（分享链接）、
`runner.py`（续问：继承 `StreamRenderer`，把流式过程翻译成 AG-UI 事件，前端用 TanStack AI 的 `useChat` 消费）。
