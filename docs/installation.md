# 安装

[← 返回 README](../README.md) · [文档目录](README.md)

## 方式一：从私有 Git 仓库安装（推荐）

```bash
uv tool install git+https://github.com/Tankawa02/logagent
```

安装后全局可用：

```bash
log-agent --help
```

需要 Web 界面（`log-agent serve`）时带上 `web` 额外依赖：

```bash
uv tool install 'log-agent[web] @ git+https://github.com/Tankawa02/logagent'
```

## 方式二：临时运行（不常驻安装）

```bash
uvx --from git+https://github.com/Tankawa02/logagent log-agent analyze --log app.log
```

## 方式三：本地开发

```bash
git clone https://github.com/Tankawa02/logagent.git
cd logagent
uv sync                 # 需要 Web 界面时：uv sync --extra web
uv run log-agent --help
```
