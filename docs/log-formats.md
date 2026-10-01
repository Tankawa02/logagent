# 日志格式

[← 返回 README](../README.md) · [文档目录](README.md)


不用配置即可识别（`log-agent inspect` 会显示抽样识别到的格式和占比）：

| 格式 | 示例 | 级别来源 |
| --- | --- | --- |
| 常见文本 | `2026-06-09 14:02:03,123 ERROR ...`、`2026/06/09 14:02:03 [error] ...` | 级别单词（含 crit / emerg / alert / notice） |
| nginx / Apache 访问日志 | `1.2.3.4 - - [30/Sep/2026:14:00:00 +0800] "GET /api HTTP/1.1" 502 ...` | 状态码：5xx=ERROR、4xx=WARN；按 `status + 方法 + 路径` 聚类 |
| syslog | `<11>Sep 30 14:00:00 host app[1]: ...` | 级别单词，没有时用 `<PRI>` |
| Tomcat / JUL | `30-Sep-2026 14:00:00.123 SEVERE ...` | 级别单词 |
| glog / klog | `E0930 14:00:00.123456 1 file.go:42] ...` | 首字母 I / W / E / F |
| logfmt | `time=... level=error msg="..." status=504` | `level` / `lvl` / `severity` |
| JSON | pino / bunyan（数字级别）、Serilog CLEF（`@t` / `@l` / `@m`）、ECS（`log.level`）等 | 级别字段 |
| 容器日志 | Docker json-file `{"log": ...}`、CRI / containerd `...Z stderr F ...` | 先拆出应用原始行再识别 |
| Unix 时间戳 | JSON / logfmt 的秒、毫秒、微秒、纳秒；行首 `1790740800.123` | — |

syslog、glog 不带年份时按当前日期推断（落在明天之后的算作去年）。

**自定义格式**：内置规则认不出时，在 `.log-agent.toml` 里用正则声明，按顺序优先于内置规则：

```toml
[[log_formats]]
name = "legacy-gateway"
# 命名分组：time / ts / timestamp（时间）、level（级别）、message / msg（正文）；至少要有时间或级别
pattern = '^(?P<time>\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}) \|(?P<level>\w)\| (?P<message>.*)$'
time_format = "%d.%m.%Y %H:%M:%S"               # 可选，strptime 写法；不写时按内置规则识别时间
levels = { E = "ERROR", W = "WARN", I = "INFO" } # 可选，把私有级别映射到标准级别
sample = "30.09.2026 14:00:05 |E| upstream reset" # 可选，启动时用它校验 pattern / time_format / levels
```

写错（正则无效、缺命名分组、级别映射非法、`sample` 对不上）时命令直接报错退出（退出码 2），不会带着错误的格式跑分析。
用 `log-agent inspect -l app.log` 可以确认识别比例（例如 `识别格式：自定义：legacy-gateway 100%`）。
