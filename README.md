# log-agent

基于 [deepagents](https://docs.langchain.com/oss/python/deepagents/overview) 的命令行日志分析智能体。
它会结合**日志文件**和**源代码目录**，自动规划排查步骤、检索异常、关联代码，最终输出根因分析与修复建议。

## 功能

- 输入日志文件路径 + 源码目录路径，自动定位问题根因
- 内置只读工具：分块读日志、搜索日志、列源码、读源码、grep 源码
- 利用 deepagents 的 `write_todos` 规划与上下文压缩，能处理大日志
- 使用 OpenAI 模型（可切换其他 provider）

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

每个团队成员各自设置自己的 OpenAI key（不要写进代码或仓库）：

```bash
export OPENAI_API_KEY="sk-..."
```

## 使用

```bash
# 只分析日志
log-agent analyze --log /path/to/app.log

# 日志 + 源码，定位根因
log-agent analyze --log /path/to/app.log --code /path/to/your/repo

# 指定问题
log-agent analyze -l app.log -c ./repo -q "为什么 14:00 之后接口大量 500？"

# 流式查看 agent 每一步（工具调用过程）
log-agent analyze -l app.log -c ./repo --verbose

# 切换模型
log-agent analyze -l app.log -m openai:gpt-4.1-mini
```

## 参数

| 参数 | 简写 | 说明 |
|------|------|------|
| `--log` | `-l` | 日志文件路径（必填） |
| `--code` | `-c` | 源码目录路径（可选） |
| `--question` | `-q` | 想让 agent 回答的具体问题 |
| `--model` | `-m` | 模型，`provider:model` 格式，默认 `openai:gpt-4.1` |
| `--verbose` | `-v` | 流式打印执行过程 |

## 安全说明

- 所有工具均为**只读**，agent 不会修改你的日志或源码。
- 日志/源码内容会发送给 OpenAI，敏感数据请先脱敏，或改用本地模型（如 `ollama:...`）。
