"""可选的 Web 界面：`log-agent serve` 在浏览器里查看 chat 会话、错误时间线、报告与证据对照。

依赖 FastAPI / uvicorn，安装方式：`uv tool install 'log-agent[web] @ git+...'`。
这里只放与 Web 相关的代码；日志解析、证据核对、会话存储全部复用 CLI 已有模块。
"""
