from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from log_agent import tools


def test_log_overview(sample_log: Path) -> None:
    out = tools.log_overview(str(sample_log))
    assert "共 10 行" in out
    assert "编码 utf-8" in out
    assert "时间范围：2026-06-09 10:00:00 → 2026-06-09 10:00:06" in out
    assert "ERROR 2" in out and "WARN 1" in out
    # 两条 payment failed 只是订单号不同，应归一化成同一个签名
    assert "x2  首次 L4" in out
    assert "KeyError  x1  首次 L7" in out


def test_log_overview_is_cached_until_file_changes(sample_log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import threading

    scans = []
    real_scan = tools._scan_overview
    monkeypatch.setattr(tools, "_scan_overview", lambda log, window: scans.append(1) or real_scan(log, window))

    # 模拟两个子代理同时要同一份概览：只应扫描一次
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(tools.log_overview(str(sample_log)))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(scans) == 1 and results[0] == results[1]
    assert any(r.meta.get("cached") for r in results)

    # 不同时间窗口是不同的缓存项
    tools.log_overview(str(sample_log), since="10:00:03")
    assert len(scans) == 2

    # 文件被追加后缓存失效
    with sample_log.open("a", encoding="utf-8") as f:
        f.write("2026-06-09 10:00:07 ERROR [order] payment failed order=1003\n")
    stat = sample_log.stat()
    os.utime(sample_log, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    out = tools.log_overview(str(sample_log))
    assert len(scans) == 3 and "共 11 行" in out and not out.meta.get("cached")


def test_log_overview_missing_file(tmp_path: Path) -> None:
    assert tools.log_overview(str(tmp_path / "nope.log")).startswith("[错误]")


def test_read_log_chunk(sample_log: Path) -> None:
    out = tools.read_log_chunk(str(sample_log), 4, 2)
    lines = out.splitlines()
    assert lines[0].startswith("--- ") and "第 4-5 行" in lines[0]
    assert lines[1].startswith("4: ") and "payment failed" in lines[1]
    assert lines[2] == "5: Traceback (most recent call last):"


def test_read_log_chunk_past_end(sample_log: Path) -> None:
    assert tools.read_log_chunk(str(sample_log), 999).startswith("[提示]")


def test_read_log_chunk_caps_lines(tmp_path: Path) -> None:
    path = tmp_path / "many.log"
    path.write_text("x\n" * 3000, encoding="utf-8")
    out = tools.read_log_chunk(str(path), 1, 99999)
    assert len(out.splitlines()) == tools.MAX_CHUNK_LINES + 1


def test_search_with_context_groups_hits(sample_log: Path) -> None:
    out = tools.search_logs(str(sample_log), "ERROR", context=1)
    assert out.splitlines() == [
        "3- 2026-06-09 10:00:02 WARN  slow query took 1200ms",
        "4: 2026-06-09 10:00:03 ERROR [order] payment failed order=1001",
        "5- Traceback (most recent call last):",
        "--",
        "8- 2026-06-09 10:00:04 INFO  handling request id=2",
        "9: 2026-06-09 10:00:05 ERROR [order] payment failed order=1002",
        "10- 2026-06-09 10:00:06 INFO  done",
    ]


def test_search_literal_brackets(sample_log: Path) -> None:
    literal = tools.search_logs(str(sample_log), "[order]", regex=False)
    assert literal.count("\n") == 1  # 精确命中 2 行
    as_regex = tools.search_logs(str(sample_log), "[order]")
    assert as_regex.count("\n") > 1  # 字符类会匹配到大量行，这正是要提示模型用 regex=False 的原因


def test_search_invalid_regex_falls_back(sample_log: Path) -> None:
    out = tools.search_logs(str(sample_log), "failed order=(")
    assert out.startswith("（正则无效") or "[提示]" in out


def test_search_range_and_limit(sample_log: Path) -> None:
    ranged = tools.search_logs(str(sample_log), "ERROR", start_line=5, end_line=9)
    assert ranged.splitlines()[0].startswith("9: ")
    limited = tools.search_logs(str(sample_log), "INFO", max_results=1)
    assert "命中超过 1 条" in limited


def test_search_ignore_case(sample_log: Path) -> None:
    assert tools.search_logs(str(sample_log), "error").startswith("[提示]")
    assert tools.search_logs(str(sample_log), "error", ignore_case=True).startswith("4: ")


def test_search_output_is_redacted(tmp_path: Path) -> None:
    path = tmp_path / "s.log"
    path.write_text("ERROR login failed password=hunter2 phone=13812345678\n", encoding="utf-8")
    out = tools.search_logs(str(path), "ERROR")
    assert "hunter2" not in out and "13812345678" not in out


def test_list_code_files_whitelist_and_skip_dirs(code_repo: Path) -> None:
    out = tools.list_code_files(str(code_repo))
    files = {line.replace("\\", "/") for line in out.splitlines()}
    assert files == {"app/legacy.java", "app/order.py"}
    assert tools.list_code_files(str(code_repo), "*.py").replace("\\", "/") == "app/order.py"


def test_read_code_file_ranges_and_numbers(code_repo: Path) -> None:
    out = tools.read_code_file(str(code_repo), "app/order.py", 2, 3)
    lines = out.splitlines()
    assert "第 2-3 行，共 3 行" in lines[0]
    assert lines[1] == "2 |     # 支付入口"
    assert lines[2] == "3 |     return order['order_id']"


def test_read_code_file_detects_gbk(code_repo: Path) -> None:
    assert "旧版订单服务" in tools.read_code_file(str(code_repo), "app/legacy.java")


def test_read_code_file_blocks_traversal(code_repo: Path) -> None:
    assert tools.read_code_file(str(code_repo), "../outside.py").startswith("[错误] 非法路径")


def test_read_code_file_long_file_hint(tmp_path: Path) -> None:
    (tmp_path / "long.py").write_text("x = 1\n" * 1000, encoding="utf-8")
    out = tools.read_code_file(str(tmp_path), "long.py")
    assert f"第 1-{tools.MAX_CODE_LINES} 行" in out
    assert f"start_line={tools.MAX_CODE_LINES + 1}" in out


@pytest.mark.parametrize("use_rg", [False, True])
def test_grep_code(code_repo: Path, monkeypatch: pytest.MonkeyPatch, use_rg: bool) -> None:
    if use_rg and not shutil.which("rg"):
        pytest.skip("ripgrep 未安装")
    if not use_rg:
        monkeypatch.setenv("LOG_AGENT_NO_RG", "1")
    out = tools.grep_code(str(code_repo), "pay").replace("\\", "/")
    assert "app/order.py:1: def pay(order):" in out
    assert "app/legacy.java:2:" in out
    assert "node_modules" not in out
    only_py = tools.grep_code(str(code_repo), "pay", path_glob="*.py").replace("\\", "/")
    assert "legacy.java" not in only_py
    literal = tools.grep_code(str(code_repo), "order['order_id']", regex=False)
    assert "order.py:3:" in literal.replace("\\", "/")


def test_grep_code_no_match(code_repo: Path) -> None:
    assert tools.grep_code(str(code_repo), "definitely_absent_symbol").startswith("[提示]")
