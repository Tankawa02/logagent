"""以真实子进程跑完整 analyze 流程，覆盖 CliRunner 覆盖不到的东西：平台默认控制台编码、Live 渲染、跨盘符路径。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

LOG_TEXT = (
    "2026-06-09 14:00:01.100 INFO  收到短信请求 trace=T42 手机号=13800138000\n"
    "2026-06-09 14:00:01.500 WARN  twilio failed trace=T42 code=30008 配额耗尽\n"
    "java.lang.IllegalStateException: quota exceeded\n"
    "\tat com.acme.sms.TwilioSender.send(TwilioSender.java:88)\n"
    "2026-06-09 14:00:02.000 INFO  降级到 nexmo trace=T42\n"
    "2026-06-09 14:00:03.000 ERROR 其他请求失败 trace=T99\n"
)


def test_analyze_end_to_end_in_real_process(tmp_path: Path) -> None:
    # 目录名带空格和中文、日志用 GBK 编码：Windows 用户最常见的组合
    log_dir = tmp_path / "日志 目录"
    log_dir.mkdir()
    log_path = log_dir / "sms-gateway.log"
    log_path.write_bytes(LOG_TEXT.encode("gbk"))
    out_file = tmp_path / "report.json"
    code_dir = ROOT / "src"

    env = {k: v for k, v in os.environ.items() if k not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
    env.update(OPENAI_API_KEY="smoke", HOME=str(tmp_path / "home"), USERPROFILE=str(tmp_path / "home"))
    env.pop("LOG_AGENT_CONFIG", None)

    proc = subprocess.run(
        [sys.executable, "-m", "tests.smoke_driver", str(log_path), str(code_dir), str(out_file)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=180,
    )
    # 故意不指定 text/encoding 解码：输出本身不能让进程崩，内容用 JSON 报告校验
    stderr = proc.stderr.decode("utf-8", errors="replace")
    assert proc.returncode == 0, stderr[-3000:]
    assert b"Traceback" not in proc.stdout and "Traceback" not in stderr

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["status"] == "ok"
    assert "降级到 nexmo" in data["report"] and "twilio 配额告警" in data["report"]

    calls = {(c["name"], c["subagent"]): c for c in data["tool_calls"]}
    overview = calls[("log_overview", "")]
    assert not overview["failed"] and "6 行" in overview["summary"]
    assert calls[("search_logs", "")]["summary"] == "命中 1 行"
    trace = calls[("trace_request", "")]
    assert not trace["failed"] and "命中 3 行" in trace["summary"]
    # 子代理在跨目录（Windows CI 上是跨盘符）的源码里检索与读文件都要成功
    assert not calls[("grep_code", "code-investigator")]["failed"]
    assert not calls[("read_code_file", "code-investigator")]["failed"]
    assert data["usage"]["total"] > 0
