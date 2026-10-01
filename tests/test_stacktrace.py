from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from log_agent import cli, stacktrace, tools
from log_agent.logformat import level_and_body
from log_agent.render import _summarize_meta
from log_agent.stacktrace import parse_chain

runner = CliRunner()

JAVA_A = """2026-06-09 14:02:03.123 ERROR [exec-3] c.a.o.OrderController - 下单失败 id=1001
org.springframework.dao.QueryTimeoutException: PreparedStatementCallback; SQL [select * from orders where id=?]
\tat org.springframework.jdbc.support.SQLStateSQLExceptionTranslator.doTranslate(SQLStateSQLExceptionTranslator.java:99)
\tat com.acme.order.repo.OrderRepo.findById(OrderRepo.java:88)
Caused by: java.sql.SQLTimeoutException: Query timed out after 3000ms
\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeQuery(ClientPreparedStatement.java:1003)
\tat com.zaxxer.hikari.pool.HikariProxyPreparedStatement.executeQuery(HikariProxyPreparedStatement.java)
\tat com.acme.order.repo.OrderRepo.findById(OrderRepo.java:86)
\t... 12 more
\tSuppressed: java.lang.IllegalStateException: close failed
\t\tat com.acme.x.Closer.close(Closer.java:1)"""

# 同一个根因，外层换了一种包装、超时数字和行号也不同：应归为同一类
JAVA_B = """2026-06-09 14:05:00.001 ERROR [exec-7] c.a.o.RefundController - 退款失败
com.acme.common.ServiceException: refund failed
\tat com.acme.order.service.RefundService.refund(RefundService.java:31)
Caused by: java.sql.SQLTimeoutException: Query timed out after 5000ms
\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeQuery(ClientPreparedStatement.java:1003)
\tat com.acme.order.repo.OrderRepo.findById(OrderRepo.java:90)
\t... 8 more"""

# 外层与 JAVA_A 相同，但根因完全不同：应单独成类
JAVA_C = """2026-06-09 14:06:00.000 ERROR [exec-1] c.a.o.OrderController - 下单失败 id=1002
org.springframework.dao.QueryTimeoutException: PreparedStatementCallback; SQL [select * from orders where id=?]
\tat com.acme.order.repo.OrderRepo.findById(OrderRepo.java:88)
Caused by: java.net.SocketTimeoutException: Read timed out
\tat java.net.SocketInputStream.socketRead0(Native Method)
\tat com.acme.pay.GatewayClient.call(GatewayClient.java:57)"""

PYTHON = """2026-06-09 10:00:03 ERROR [order] payment failed order=1001
Traceback (most recent call last):
  File "/app/order.py", line 42, in pay
    return order['order_id']
KeyError: 'order_id'

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/lib/python3.12/site-packages/flask/app.py", line 880, in full_dispatch_request
    rv = self.dispatch_request()
  File "/app/api.py", line 12, in create
    raise PaymentError("pay failed") from None
app.errors.PaymentError: pay failed"""

GO = """panic: runtime error: invalid memory address or nil pointer dereference
[signal SIGSEGV: segmentation violation code=0x1 addr=0x0 pc=0x4a1b2c]

goroutine 42 [running]:
main.(*OrderService).Pay(0x0, {0xc000123, 0x5})
\t/app/order/service.go:57 +0x2c
net/http.HandlerFunc.ServeHTTP(0xc000010, {0x7f, 0xc0000})
\t/usr/local/go/src/net/http/server.go:2136 +0x29"""

NODE = """2026-06-09T10:00:00.000Z ERROR unhandled
TypeError: Cannot read properties of undefined (reading 'id')
    at async Router.handle (/app/node_modules/express/lib/router/index.js:284:9)
    at OrderService.pay (/app/src/order.js:57:23)
    at process.processTicksAndRejections (node:internal/process/task_queues:95:5)"""

DOTNET = """System.InvalidOperationException: Order failed ---> System.NullReferenceException: Object reference not set.
   at Acme.Orders.OrderService.Pay(Order order) in C:\\src\\Acme\\OrderService.cs:line 57
   --- End of inner exception stack trace ---
   at Acme.Api.OrdersController.Post() in C:\\src\\Acme\\OrdersController.cs:line 21
   at Microsoft.AspNetCore.Mvc.Infrastructure.ActionMethodExecutor.Execute()"""


@pytest.mark.parametrize(("text", "language", "root", "wrappers", "frame"), [
    (JAVA_A, "java", "java.sql.SQLTimeoutException", ["org.springframework.dao.QueryTimeoutException"],
     "com.acme.order.repo.OrderRepo.findById (OrderRepo.java:86)"),
    (PYTHON, "python", "KeyError", ["app.errors.PaymentError"], "pay (/app/order.py:42)"),
    (GO, "go", "runtime error", [], "main.(*OrderService).Pay (/app/order/service.go:57)"),
    (NODE, "node", "TypeError", [], "OrderService.pay (/app/src/order.js:57)"),
    (DOTNET, "dotnet", "System.NullReferenceException", ["System.InvalidOperationException"],
     "Acme.Orders.OrderService.Pay (C:\\src\\Acme\\OrderService.cs:57)"),
])
def test_parse_chain_finds_root_and_business_frame(text, language, root, wrappers, frame) -> None:
    chain = parse_chain(text.splitlines())
    assert chain is not None
    assert (chain.language, chain.root.type, chain.wrappers) == (language, root, wrappers)
    assert chain.app_frame.describe() == frame


def test_suppressed_and_mentions_are_not_chains() -> None:
    chain = parse_chain(JAVA_A.splitlines())
    assert "java.lang.IllegalStateException" not in [link.type for link in chain.links]
    assert parse_chain(["2026-06-09 ERROR timeout calling gateway, see IOException docs"]) is None


def test_trailing_exception_on_log_line() -> None:
    chain = parse_chain(["2026-06-09 14:02:03 ERROR c.a.Foo - 处理失败 java.lang.IllegalStateException: boom",
                         "\tat com.acme.Foo.run(Foo.java:10)"])
    assert chain.root.type == "java.lang.IllegalStateException" and chain.root.message == "boom"


def test_app_packages_override_business_frame() -> None:
    stacktrace.set_app_packages(["com.acme.order.service"])
    chain = parse_chain(JAVA_B.splitlines())
    assert chain.app_frame.func == "com.acme.order.service.RefundService.refund"


def test_frame_lines_are_never_log_levels() -> None:
    assert level_and_body("\tat com.acme.error.Handler.handle(Handler.java:10)") is None
    assert level_and_body("Caused by: java.lang.Error: out of memory") is None
    assert level_and_body("2026-06-09 ERROR real error") == ("ERROR", "real error")


def _write(tmp_path: Path, *blocks: str, name: str = "app.log") -> Path:
    path = tmp_path / name
    path.write_text("\n".join(blocks) + "\n2026-06-09 15:00:00 INFO done\n", encoding="utf-8")
    return path


def test_overview_clusters_by_root_cause(tmp_path: Path) -> None:
    log = _write(tmp_path, JAVA_A, JAVA_B, JAVA_C, JAVA_A.replace("id=1001", "id=1003"))
    out = tools.log_overview(str(log))
    assert out.meta["chains"] == 2
    top = out.meta["top_chains"][0]
    assert top["root"] == "java.sql.SQLTimeoutException" and top["count"] == 3
    assert top["frame"].startswith("com.acme.order.repo.OrderRepo.findById")
    assert "异常链（按根因异常 + 首个业务栈帧聚类，共 2 类" in out
    assert "外层：org.springframework.dao.QueryTimeoutException" in out
    assert "java.net.SocketTimeoutException: Read timed out" in out
    # 栈帧里的 error 包名不算 ERROR 行：只有 4 条真正的 ERROR
    assert out.meta["errors"] == 4
    assert "异常链 2 类" in _summarize_meta("log_overview", out.meta)


def test_overview_chains_python_go_and_stderr_tracebacks(tmp_path: Path) -> None:
    bare = "Traceback (most recent call last):\n  File \"/app/job.py\", line 3, in run\n    x()\nValueError: bad"
    log = _write(tmp_path, PYTHON, "2026-06-09 11:00:00 INFO next", GO, "2026-06-09 11:00:01 INFO next", bare)
    roots = {c["root"] for c in tools.log_overview(str(log)).meta["top_chains"]}
    assert roots == {"KeyError", "runtime error", "ValueError"}


def test_json_stack_fields_and_docker_wrapped_stacks(tmp_path: Path) -> None:
    record = {"@timestamp": "2026-06-09T06:00:00Z", "level": "ERROR", "message": "下单失败",
              "stack_trace": JAVA_B.split("\n", 1)[1]}
    docker = [json.dumps({"log": line + "\n", "stream": "stderr", "time": "2026-06-09T06:00:01.000Z"})
              for line in JAVA_C.splitlines()]
    log = _write(tmp_path, json.dumps(record, ensure_ascii=False), *docker)
    roots = sorted(c["root"] for c in tools.log_overview(str(log)).meta["top_chains"])
    assert roots == ["java.net.SocketTimeoutException", "java.sql.SQLTimeoutException"]


def test_window_only_counts_chains_that_start_inside(tmp_path: Path) -> None:
    log = _write(tmp_path, JAVA_A, JAVA_C)
    out = tools.log_overview(str(log), since="2026-06-09 14:06", until="2026-06-09 14:07")
    assert [c["root"] for c in out.meta["top_chains"]] == ["java.net.SocketTimeoutException"]


def test_compare_windows_reports_new_root_causes(tmp_path: Path) -> None:
    log = _write(tmp_path, JAVA_A, JAVA_C)
    out = tools.compare_windows(str(log), "2026-06-09 14:00", "2026-06-09 14:03", "2026-06-09 14:04", "2026-06-09 14:07")
    assert "新出现的根因异常" in out and "java.net.SocketTimeoutException" in out


def test_inspect_shows_chains(tmp_path: Path) -> None:
    log = _write(tmp_path, JAVA_A, JAVA_B)
    out = runner.invoke(cli.app, ["inspect", "-l", str(log)])
    assert out.exit_code == 0, out.output
    assert "异常链：共 1 类" in out.output and "识别格式" in out.output


# ---------------------------------------------------------------------------
# review findings (#43 / #44)
# ---------------------------------------------------------------------------

CLOCK_CAUSE = """2026-06-09 14:02:03.123 ERROR [exec-3] c.a.o.OrderController - query failed
org.springframework.dao.QueryTimeoutException: PreparedStatementCallback
\tat com.acme.order.repo.OrderRepo.findById(OrderRepo.java:88)
Caused by: java.sql.SQLTimeoutException: Query timed out after 00:00:30
\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeQuery(ClientPreparedStatement.java:1003)
\tat com.acme.order.repo.OrderRepo.findById(OrderRepo.java:86)"""


def _roots(log: Path) -> list[str]:
    return sorted(c["root"] for c in tools.log_overview(str(log)).meta["top_chains"])


def test_clock_text_inside_stack_does_not_split_chain(tmp_path: Path) -> None:
    out = tools.log_overview(str(_write(tmp_path, CLOCK_CAUSE)))
    [top] = out.meta["top_chains"]
    assert top["root"] == "java.sql.SQLTimeoutException" and "OrderRepo.java:86" in top["frame"]
    docker = [json.dumps({"log": line + "\n", "stream": "stderr", "time": "2026-06-09T06:02:03.000Z"})
              for line in CLOCK_CAUSE.splitlines()]
    assert _roots(_write(tmp_path, *docker, name="docker.log")) == ["java.sql.SQLTimeoutException"]


def test_stderr_python_traceback_with_level_words_stays_together(tmp_path: Path) -> None:
    tb = """Traceback (most recent call last):
  File "/app/job.py", line 3, in run
    logger.error(x)
  File "/app/parse.py", line 9, in parse
    raise ValueError("parse error at 12:00:01")
ValueError: parse error at 12:00:01

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/app/job.py", line 5, in run
    raise RuntimeError("job failed") from None
RuntimeError: job failed"""
    out = tools.log_overview(str(_write(tmp_path, "2026-06-09 10:00:00 INFO start", tb)))
    [top] = out.meta["top_chains"]
    assert top["root"] == "ValueError" and top["wrappers"] == ["RuntimeError"]
    assert "parse.py:9" in top["frame"]


def test_python_traceback_header_on_error_log_line(tmp_path: Path) -> None:
    tb = """2026-06-09 10:00:00,123 ERROR [worker] job crashed Traceback (most recent call last):
  File "/app/job.py", line 3, in run
    handle(order)
KeyError: 'order_id'"""
    assert _roots(_write(tmp_path, tb)) == ["KeyError"]


def test_node_generic_error_on_log_line_keeps_frames(tmp_path: Path) -> None:
    block = """2026-06-09T10:00:00Z ERROR Error: failed to load order
    at loadOrder (/app/src/order.js:42:11)
    at process.processTicksAndRejections (node:internal/process/task_queues:95:5)"""
    out = tools.log_overview(str(_write(tmp_path, block)))
    [top] = out.meta["top_chains"]
    assert top["root"] == "Error" and top["language"] == "node" and "order.js:42" in top["frame"]
    # 只是提到 Error: 但后面没有堆栈帧的行不算异常链
    assert _roots(_write(tmp_path, "2026-06-09T10:00:00Z ERROR Error: nope", name="b.log")) == []


def test_serilog_exception_field_is_clustered(tmp_path: Path) -> None:
    record = {"@t": "2026-06-09T06:00:00Z", "@l": "Error", "@m": "Order failed",
              "@x": "System.InvalidOperationException: bad state\n   at Acme.Orders.Service.Pay() in /src/Service.cs:line 42"}
    assert _roots(_write(tmp_path, json.dumps(record))) == ["System.InvalidOperationException"]


def test_compare_reports_rare_new_root_behind_many_old_ones(tmp_path: Path) -> None:
    def block(stamp: str, i: int) -> str:
        return (f"2026-06-09 {stamp} ERROR [w] failed\ncom.acme.Old{i}Exception: boom\n"
                f"\tat com.acme.svc.S{i}.run(S{i}.java:{i + 1})")

    base = [block("14:00:00", i) for i in range(55)]
    target = [block("14:05:00", i) for i in range(55) for _ in range(2)]
    target.append("2026-06-09 14:06:00 ERROR [w] failed\ncom.acme.BrandNewException: rare\n"
                  "\tat com.acme.svc.New.run(New.java:7)")
    log = _write(tmp_path, *base, *target)
    out = tools.compare_windows(str(log), "2026-06-09 14:00", "2026-06-09 14:01", "2026-06-09 14:05", "2026-06-09 14:07")
    section = str(out).split("新出现的根因异常", 1)
    assert len(section) == 2 and "com.acme.BrandNewException: rare" in section[1]


def test_json_lines_are_decoded_once_per_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from log_agent import logformat

    lines = [json.dumps({"time": f"2026-06-09T06:00:{i:02d}Z", "level": "info", "msg": f"ok {i}"}) for i in range(50)]
    log = tmp_path / "j.log"
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    calls = 0
    real = logformat.json.loads

    def counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(logformat.json, "loads", counting)
    tools._scan_overview(logformat_log(log), tools.TimeWindow())
    assert calls <= len(lines), calls


def logformat_log(path: Path):
    from log_agent.logfile import open_log

    return open_log(str(path))


@pytest.mark.parametrize(("header", "frame", "language"), [
    ("java.lang.IllegalStateException: boom", "    at com.acme.Service.run(Service.java:42)", "java"),
    ("System.InvalidOperationException: boom", "   at Acme.Service.Run() in /src/Service.cs:line 42", "dotnet"),
])
def test_log_message_error_does_not_replace_real_header(header, frame, language) -> None:
    chain = parse_chain(["2026-06-09 10:00:00 ERROR Validation Error: bad input", "", header, frame])
    assert chain is not None
    assert chain.language == language
    assert [link.type for link in chain.links] == [header.split(":")[0]]
    assert chain.root.message == "boom" and chain.root.frames[0].line == 42


def test_node_trailing_header_allows_blank_before_frame() -> None:
    chain = parse_chain(["2026-06-09 10:00:00 ERROR Error: boom", "", "    at run (/app/main.js:42:1)"])
    assert chain is not None and chain.root.type == "Error"
    assert chain.root.frames[0].line == 42


@pytest.mark.parametrize("indent", ["  ", "\t"])
def test_incomplete_traceback_ends_at_indented_log_record(tmp_path: Path, indent: str) -> None:
    block = ('Traceback (most recent call last):\n  File "/app/job.py", line 3, in run\n'
             '    handle(order)\n'
             f'{indent}2026-06-09 10:00:01 ERROR Validation Error: bad input\n'
             'java.lang.IllegalStateException: boom\n    at com.acme.Service.run(Service.java:42)')
    out = tools.log_overview(str(_write(tmp_path, block)))
    [top] = out.meta["top_chains"]
    assert top["root"] == "java.lang.IllegalStateException"
    assert "Service.java:42" in top["frame"]
    assert top["first_line"] == 4


@pytest.mark.parametrize("unlocated", ["    at new Promise (<anonymous>)", "    at Array.forEach (<anonymous>)"])
def test_node_generic_header_with_unlocated_first_frame(tmp_path: Path, unlocated: str) -> None:
    block = ("2026-06-09 10:00:00 ERROR Error: boom\n" + unlocated
             + "\n\n    at run (/app/main.js:42:1)")
    chain = parse_chain(block.splitlines())
    assert chain is not None and chain.language == "node"
    assert chain.root.type == "Error" and chain.root.message == "boom"
    assert chain.root.frames[-1].file == "/app/main.js" and chain.root.frames[-1].line == 42
    [top] = tools.log_overview(str(_write(tmp_path, block))).meta["top_chains"]
    assert top["root"] == "Error" and top["language"] == "node"


def test_node_frame_lookahead_does_not_cross_real_header() -> None:
    chain = parse_chain(["2026-06-09 10:00:00 ERROR Validation Error: bad input",
                         "TypeError: actual failure", "    at run (/app/main.js:42:1)"])
    assert chain is not None and chain.root.type == "TypeError"
