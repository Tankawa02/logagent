# log-agent

基于 [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 的命令行日志分析智能体。
它会结合**日志文件**和**源代码目录**，自动规划排查步骤、检索异常、关联代码，最终输出根因分析与修复建议。

## 功能

- 输入日志文件路径 + 源码目录路径，自动定位问题根因
- 两种模式：`analyze` 单次分析，`chat` 多轮对话（连续追问，记住上下文）
- 内置只读工具：分块读日志、搜索日志、列源码、读源码、grep 源码
- 利用 deepagents 的 `write_todos` 规划与上下文压缩，能处理大日志
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

# 保留每一步工具调用与计划变化的完整记录
log-agent analyze -l app.log -c ./repo --verbose

# 切换模型
log-agent analyze -l app.log -m openai:gpt-4.1-mini
```

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
```

## 参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--log` | `-l` | 日志文件路径（必填） |
| `--code` | `-c` | 源码目录路径（可选） |
| `--question` | `-q` | 想让 agent 回答的具体问题 |
| `--model` | `-m` | 模型，`provider:model` 格式，默认 `openai:gpt-4.1` |
| `--verbose` | `-v` | 保留每一步工具调用（含结果摘要、耗时）与计划变化的完整记录 |

## 终端显示

- 两种模式都是**流式输出**：报告按 Markdown 块边写边落到屏幕上，底部常驻状态栏实时显示
  当前阶段（思考中 / 正在查看日志 / 正在阅读源码 / 正在撰写）、耗时、token 与工具次数。
- 运行中的工具带 spinner 和计时，完成后收敛成一行：`✓ ≡ 搜索日志  app.log  "ERROR"  命中 23 行 · 0.3s`。
- 不加 `-v` 时过程信息只在底部状态栏滚动、结束即消失，屏幕上只留报告；加 `-v` 会把每步都保留下来。

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
- 日志/源码内容会发送给 OpenAI，敏感数据请先脱敏，或改用本地模型（如 `ollama:...`）。
