# 终端显示

[← 返回 README](../README.md) · [文档目录](README.md)


- 两种模式都是**流式输出**：报告按 Markdown 块边写边落到屏幕上，底部常驻状态栏实时显示
  当前阶段（思考中 / 正在查看日志 / 正在阅读源码 / 正在撰写）、耗时、token 与工具次数。
- 运行中的工具带 spinner 和计时，完成后收敛成一行：`✓ ≡ 搜索日志  app.log  "ERROR"  命中 23 行 · 0.3s`。
- 不加 `-v` 时过程信息只在底部状态栏滚动、结束即消失，屏幕上只留报告；加 `-v` 会把每步都保留下来。
- 子代理的每一步缩进显示在对应的"委派子任务"下面（运行中只滚动显示最近 3 步），
  并行的多个子代理各自计时；token 与工具次数统计包含子代理，JSON 报告里子代理的调用带 `subagent` 字段。
- 默认模式下工具过程只在运行时显示，结束后折叠成一行，例如 `查了 6 步：日志概览 → 搜索日志 ×3 → 委派子任务`；
  加 `-v` 会保留每一步的完整记录。
- 完整报告的第一行是**一句话结论 + 可信度**（高 / 中 / 低），终端里显示成高亮摘要；
  导出的 JSON 里对应 `summary` 与 `confidence` 字段，有有效结构化报告时以结构化结论为准。
- 报告里的 `app.log:42`、`app/order.py:88` 这类引用可以直接点击（终端超链接 OSC 8）：
  VS Code / Cursor 内置终端里跳到对应行，其它终端（iTerm2、Windows Terminal、GNOME Terminal 等）打开对应文件。
  用 `LOG_AGENT_LINKS` 调整：`vscode` 总是生成 `vscode://` 链接，`file` 总是生成 `file://`，`off` 关闭。
  只影响终端显示，导出的报告保持原文，不写入本机绝对路径。
- 同一次运行里对同一份日志、同一时间窗口重复调用"日志概览"会直接命中缓存（摘要里标"缓存"），
  日志文件被改写后自动失效。

## Windows 兼容

- 自动开启控制台 VT 模式，cmd / PowerShell / Windows Terminal / VS Code 终端 / Git Bash 表现一致，刷新不闪烁。
- 中文 Windows 的经典控制台（非 UTF-8 代码页）会把 `─ ○ ✓` 等符号画成双宽，导致边框错位、残影叠行，
  这种环境会**自动切换为纯 ASCII 字形**。也可以手动指定：

  ```powershell
  $env:LOG_AGENT_GLYPHS="ascii"    # 强制 ASCII
  $env:LOG_AGENT_GLYPHS="unicode"  # 强制 Unicode（例如已 chcp 65001）
  ```

- 输出重定向到文件（`log-agent analyze ... > report.txt`）时统一写 UTF-8，不会因编码报错中断。
- 遵循 `NO_COLOR` 环境变量关闭颜色。
