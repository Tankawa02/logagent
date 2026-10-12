"""访问范围、结果上限、trace_request 容错、统计缓存、重复调用去重、收尾模型兜底。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from log_agent import scope, tools
from log_agent.dedupe import REPEAT_FULL_CHARS, DuplicateCallMiddleware
from log_agent.postprocess import invoke_structured
from log_agent.report import Analysis


def _write(path: Path, lines: list[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 访问范围
# ---------------------------------------------------------------------------


def test_scope_blocks_unregistered_log(tmp_path: Path) -> None:
    allowed = _write(tmp_path / "logs" / "app.log", ["2026-06-09 10:00:00 ERROR boom"])
    secret = _write(tmp_path / "secret.txt", ["password=hunter2"])
    scope.set_scope([str(allowed)], [])

    assert tools.read_log_chunk(str(allowed)).status == "ok"
    # 相对写法 / `..` 绕路也要按真实路径比较
    detour = str(tmp_path / "logs" / ".." / "logs" / "app.log")
    assert tools.read_log_chunk(detour).status == "ok"
    denied = tools.read_log_chunk(str(secret))
    assert denied.status == "error" and "拒绝访问" in denied
    assert "hunter2" not in denied
    assert tools.search_logs(str(secret), "password").status == "error"
    assert tools.log_overview(str(secret)).status == "error"


def test_scope_blocks_code_outside_roots(tmp_path: Path) -> None:
    code = tmp_path / "repo"
    _write(code / "src" / "app.py", ["print('ok')"])
    outside = tmp_path / "other"
    _write(outside / "keys.py", ["TOKEN = 'abc'"])
    scope.set_scope([], [str(code)])

    assert tools.read_code_file(str(code), "src/app.py").status == "ok"
    # 子目录也算在登记范围内
    assert tools.list_code_files(str(code / "src")).status == "ok"
    assert tools.read_code_file(str(outside), "keys.py").status == "error"
    assert tools.grep_code(str(outside), "TOKEN").status == "error"
    assert tools.list_code_files("/").status == "error"


def test_no_scope_means_unrestricted(tmp_path: Path) -> None:
    log = _write(tmp_path / "x.log", ["hello"])
    scope.clear_scope()
    assert tools.read_log_chunk(str(log)).status == "ok"


# ---------------------------------------------------------------------------
# 上限 / 容错 / 缓存
# ---------------------------------------------------------------------------


def test_result_caps(tmp_path: Path) -> None:
    log = _write(tmp_path / "big.log", [f"2026-06-09 10:00:00 ERROR hit {i}" for i in range(500)])
    out = tools.search_logs(str(log), "hit", max_results=5000)
    assert out.meta["hits"] == tools.MAX_SEARCH_RESULTS and out.meta["truncated"]

    code = tmp_path / "code"
    _write(code / "a.py", [f"x = {i}  # needle" for i in range(500)])
    grep = tools.grep_code(str(code), "needle", max_results=10_000)
    assert grep.meta["hits"] == tools.MAX_GREP_RESULTS


def test_trace_request_skips_missing_file(tmp_path: Path) -> None:
    log = _write(tmp_path / "a.log", ["2026-06-09 10:00:00 INFO req=abc start", "2026-06-09 10:00:01 INFO req=abc done"])
    out = tools.trace_request([str(log), str(tmp_path / "gone.log")], "req=abc")
    assert out.status == "ok"
    assert out.meta["hits"] == 2 and out.meta["skipped_files"] == 1
    assert "已跳过无法打开的日志" in out and "gone.log" in out

    all_missing = tools.trace_request([str(tmp_path / "x.log")], "abc")
    assert all_missing.status == "error"


def test_compare_windows_reuses_overview_scan(tmp_path: Path, monkeypatch) -> None:
    lines = [f"2026-06-09 13:{m:02d}:00 INFO ok" for m in range(30)]
    lines += [f"2026-06-09 14:{m:02d}:00 ERROR timeout calling db" for m in range(30)]
    log = _write(tmp_path / "app.log", lines)

    calls = []
    original = tools._scan_overview

    def counting(log_file, window):
        calls.append(window)
        return original(log_file, window)

    monkeypatch.setattr(tools, "_scan_overview", counting)
    tools.log_overview(str(log), since="14:00", until="14:29")
    assert len(calls) == 1
    first = tools.compare_windows(str(log), "13:00", "13:29", since="14:00", until="14:29")
    # 目标时段沿用 log_overview 的扫描，只多扫一次基线
    assert len(calls) == 2
    second = tools.compare_windows(str(log), "13:00", "13:29", since="14:00", until="14:29")
    assert len(calls) == 2
    assert str(first) == str(second)


# ---------------------------------------------------------------------------
# 重复调用去重
# ---------------------------------------------------------------------------


def _request(name: str, args: dict, human_id: str, call_id: str):
    state = {"messages": [HumanMessage(content="q", id=human_id), AIMessage(content="")]}
    return SimpleNamespace(tool_call={"name": name, "args": args, "id": call_id}, state=state)


def test_duplicate_calls_are_short_circuited() -> None:
    middleware = DuplicateCallMiddleware(["search_logs"])
    executed = []

    def handler(request):
        executed.append(request.tool_call["id"])
        return ToolMessage(content="1: hit", tool_call_id=request.tool_call["id"], name="search_logs",
                           artifact={"status": "ok", "hits": 1})

    args = {"path": "a.log", "pattern": "ERROR"}
    middleware.wrap_tool_call(_request("search_logs", args, "h1", "c1"), handler)
    repeat = middleware.wrap_tool_call(_request("search_logs", dict(reversed(args.items())), "h1", "c2"), handler)
    assert executed == ["c1"]
    assert repeat.tool_call_id == "c2" and repeat.artifact["kind"] == "duplicate"
    assert "未重复执行" in repeat.content and "1: hit" in repeat.content

    # 新的一轮（新的用户消息）重新执行；参数不同也照常执行
    middleware.wrap_tool_call(_request("search_logs", args, "h2", "c3"), handler)
    middleware.wrap_tool_call(_request("search_logs", {**args, "context": 3}, "h2", "c4"), handler)
    assert executed == ["c1", "c3", "c4"]


def test_duplicate_long_result_only_previews_and_errors_are_not_cached() -> None:
    middleware = DuplicateCallMiddleware(["read_log_chunk"])
    long_text = "x" * (REPEAT_FULL_CHARS + 100)
    results = iter([
        ToolMessage(content="[错误] 临时失败", tool_call_id="c1", name="read_log_chunk", artifact={"status": "error"}),
        ToolMessage(content=long_text, tool_call_id="c2", name="read_log_chunk", artifact={"status": "ok"}),
    ])

    def handler(request):
        return next(results)

    args = {"path": "a.log", "start_line": 1}
    middleware.wrap_tool_call(_request("read_log_chunk", args, "h1", "c1"), handler)
    second = middleware.wrap_tool_call(_request("read_log_chunk", args, "h1", "c2"), handler)
    assert second.content == long_text
    third = middleware.wrap_tool_call(_request("read_log_chunk", args, "h1", "c3"), handler)
    assert third.artifact["kind"] == "duplicate" and len(third.content) < len(long_text)


def test_untracked_tools_pass_through() -> None:
    middleware = DuplicateCallMiddleware(["search_logs"])
    count = 0

    def handler(request):
        nonlocal count
        count += 1
        return ToolMessage(content="ok", tool_call_id=request.tool_call["id"], name="task")

    for i in range(2):
        middleware.wrap_tool_call(_request("task", {"description": "x"}, "h1", f"c{i}"), handler)
    assert count == 2


# ---------------------------------------------------------------------------
# 收尾模型兜底
# ---------------------------------------------------------------------------


class _Failing:
    model_name = "cheap"

    def with_structured_output(self, schema, method, include_raw):
        raise RuntimeError("429 rate limited")


class _Working:
    model_name = "main"

    def with_structured_output(self, schema, method, include_raw):
        parsed = Analysis.model_validate({"conclusion": "未发现异常", "assessment": "clear", "confidence": "high",
                                          "impact": "无", "next_steps": [], "issues": [], "open_questions": []})

        class _Runnable:
            def invoke(self, messages):
                return {"raw": AIMessage(content=""), "parsed": parsed}

        return _Runnable()


def test_code_redaction_masks_prefixed_secret_literals() -> None:
    from log_agent import redact

    assert redact.redact_code("DB_PASSWORD=Tr0ub4dor-prod") == "DB_PASSWORD=[已脱敏]"
    assert redact.redact_code('API_KEY = "abcd1234efgh"') == 'API_KEY = "[已脱敏]"'
    assert "Pa55" not in redact.redact_code("REDIS_URL=redis://cache:Pa55-word@redis-1:6379/0")
    assert redact.redact_log("db_password=hunter22 user=x") == "db_password=[已脱敏] user=x"
    # 代码里的引用、普通名字保持原样
    for text in ('password = os.environ["DB_PASSWORD"]', "token: ${GITHUB_TOKEN}", 'tokenizer = "bert-base"',
                 'password_policy = "strict"', "conninfo=f\"postgresql://app:{os.environ['PW']}@db:5432/shop\""):
        assert redact.redact_code(text) == text


def test_eval_cases_load_and_forbidden_zeroes_score() -> None:
    from log_agent.evals import load_cases, score_case

    root = Path(__file__).resolve().parents[1] / "evals" / "cases"
    cases = {c.name: c for c in load_cases(root)}
    assert {"multi-service-trace", "deploy-regression", "no-timestamp-batch", "prompt-injection"} <= set(cases)
    repo = Path(cases["deploy-regression"].code[0])
    assert (repo / ".git").is_dir() and (repo / "pricing.py").is_file()

    case = cases["prompt-injection"]
    payload = {
        "report": "一句话结论：连接泄漏（可信度：高）\nexport.py:12 putconn 不在 finally；DB_PASSWORD=Tr0ub4dor-prod-7781",
        "analysis": {"assessment": "finding"}, "structured_status": "valid",
        "tool_calls": [{"summary": "拒绝访问：/etc/passwd 不是本次排查登记的日志。"}],
    }
    scored = score_case(case, payload)
    assert scored["score"] == 0 and scored["forbidden_hits"] == ["Tr0ub4dor-prod-7781"]
    assert scored["denied_calls"] == 1


def test_invoke_structured_falls_back_to_next_model() -> None:
    parsed, calls = invoke_structured([_Failing(), _Working()], Analysis, [{"role": "user", "content": "x"}], "structure")
    assert parsed is not None and parsed.assessment == "clear"
    assert [c["model"] for c in calls] == ["cheap", "cheap", "main"]
    assert calls[-1]["fallback"] is True and calls[0]["fallback"] is False


def test_invoke_structured_records_input_and_output_for_trace() -> None:
    class _ToolCalling:
        model_name = "flash"

        def with_structured_output(self, schema, method, include_raw):
            raw = AIMessage(content="", tool_calls=[{"name": "Analysis", "args": {"conclusion": "ok"}, "id": "c1"}],
                            additional_kwargs={"reasoning_content": "先看结论"})
            parsed = _Working().with_structured_output(schema, method, include_raw).invoke([])["parsed"]

            class _Runnable:
                def invoke(self, messages):
                    return {"raw": raw, "parsed": parsed}

            return _Runnable()

    messages = [{"role": "system", "content": "你是结构化助手"}, {"role": "user", "content": "报告正文"}]
    _, failed = invoke_structured([_Failing()], Analysis, messages, "structure")
    assert [m["text"] for m in failed[0]["input_messages"]] == ["你是结构化助手", "报告正文"]
    assert failed[0]["input_full"] is True and "output_text" not in failed[0]

    _, calls = invoke_structured([_ToolCalling()], Analysis, messages, "structure")
    call = calls[0]
    assert [m["role"] for m in call["input_messages"]] == ["system", "user"]
    assert call["tool_requests"][0]["args"] == {"conclusion": "ok"}
    assert call["reasoning"] == {"text": "先看结论", "chars": 4}
    assert "reasoning_tokens" in call
