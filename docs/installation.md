# 安装

[← 返回 README](../README.md) · [文档目录](README.md)

## 方式一：从私有 Git 仓库安装（推荐）

```bash
uv tool install git+https://github.com/yourorg/log-agent.git
```

安装后全局可用：

```bash
log-agent --help
```

## 方式二：临时运行（不常驻安装）

```bash
uvx --from git+https://github.com/yourorg/log-agent.git log-agent analyze --log app.log
```

## 方式三：本地开发

```bash
git clone https://github.com/yourorg/log-agent.git
cd log-agent
uv sync
uv run log-agent --help
```
