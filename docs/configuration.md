# 配置

[← 返回 README](../README.md) · [文档目录](README.md)

## 引导式配置（init）

第一次用推荐直接跑 `log-agent init`，它会依次：

1. **检查 API Key**：没设置时给出当前平台的设置命令；也可以当场粘贴一次 Key 只用于本次验证。**Key 不会写进配置文件。**
2. **选模型**：先问网关地址（回车用官方接口），有 Key 时从接口拉取可用模型列表供选择（拉不到就用常用模型列表），可选地发一次最小请求验证 Key 和模型名。
   带用户名密码或查询参数的网关地址不会写进文件，会提示你放到 `OPENAI_BASE_URL`。
3. **抽样日志识别格式**：默认取当前目录和 `logs/`、`log/` 下最近改动的 `*.log` / `*.log.gz`，只看每份的前 2000 行，
   报告编码、格式分布和级别分布。日志时间不带时区时询问 `timezone`；未识别的行超过 30% 时，
   在配置里附上一份注释掉的 `[[log_formats]]` 模板，`sample` 填的是一行未识别的真实日志（已脱敏）。
4. **源码目录**：当前目录像代码仓库（有 `.git`、`pyproject.toml`、`pom.xml`、`package.json`、`go.mod` 等）时，询问是否写入 `code = ["."]`。
5. **生成 `.log-agent.toml`**：先用真实的配置加载逻辑校验一遍、展示预览，确认后写入。

```bash
log-agent init                                   # 交互式
log-agent init --yes                             # 不提问，全部用检测到的默认值（脚本 / CI）
log-agent init -y -l 'logs/*.log' -m openai:qwen-max --base-url https://gw.example.com/v1 --timezone +08:00 --ping
```

| 参数 | 说明 |
|------|------|
| `--log` / `-l` | 抽样的日志，可重复、支持通配符；不传时自动发现 |
| `--model` / `-m`、`--base-url`、`--code` / `-c`、`--timezone` | 直接指定，对应步骤不再提问 |
| `--ping` / `--no-ping` | 是否发一次最小请求验证 Key；默认交互模式下询问、`--yes` 时不验证 |
| `--yes` / `-y` | 不提问 |
| `--force` | 覆盖已有的 `.log-agent.toml`（否则拒绝覆盖） |

退出码：0 已生成；1 已生成但显式要求的 `--ping` 失败；2 没有生成（取消、文件已存在等）。
几点细节：

- 设置了 `LOG_AGENT_CONFIG` 时，运行时只读它指向的文件（加上用户级配置），所以 `init` 会写到那个文件而不是项目里的
  `.log-agent.toml`；`code` 等相对路径也按那个文件所在目录计算。不想这样的话先 `unset LOG_AGENT_CONFIG`。
- 验证（ping）和拉取模型列表用的是**运行时实际会用的**模型和网关：环境变量 `LOG_AGENT_MODEL` / `OPENAI_BASE_URL`
  会覆盖配置文件，init 会明确提示覆盖关系。显式给出的网关地址因为带凭据被拒绝时，直接跳过验证和模型列表，
  不会把 Key 发到其它地址。
- 带用户名密码、查询参数或 `#` 片段的网关地址不会写进文件，也不会作为提示的默认值显示出来。
- 提示里的路径一律用 `/`（Windows 上也能直接用），含空格时 Windows 用双引号、macOS / Linux 用 shell 引号。

只想要一份全是注释的空模板，用 `log-agent config --init`。

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
log-agent init           # 引导式生成（见上文「引导式配置」）
log-agent config --init   # 只生成带注释的空模板
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
