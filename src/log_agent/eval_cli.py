"""`log-agent eval`：跑标准案例给模型回答打分，以及把会话里的反馈导出为案例。"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import typer
from rich.text import Text

from .term import console, glyphs

eval_app = typer.Typer(help="用标准案例评测模型回答质量（根因命中、判定、证据核对、耗时与费用）。", no_args_is_help=True)

_DEFAULT_CASES = Path("evals/cases")


@eval_app.command("run")
def run(
    cases: Path = typer.Option(_DEFAULT_CASES, "--cases", "-c", help="案例目录（含多个案例子目录），或单个案例目录"),
    model: list[str] = typer.Option(None, "--model", "-m", help="要评测的模型，可多次指定；默认读 LOG_AGENT_MODEL"),
    structured: list[str] = typer.Option(
        None, "--structured", "-s", help="结构化报告方式 inline / extract / auto，可多次指定做对比；默认 auto",
    ),
    only: list[str] = typer.Option(None, "--case", help="只跑指定名字的案例，可多次指定"),
    repeat: int = typer.Option(1, "--repeat", "-r", min=1, max=10, help="每个组合重复次数（模型输出有随机性）"),
    jobs: int = typer.Option(4, "--jobs", "-j", min=1, max=32, help="并行进程数"),
    max_steps: int = typer.Option(80, "--max-steps", min=10, help="单个案例的最大推理步数"),
    timeout: int = typer.Option(900, "--timeout", min=0, help="单个案例的墙钟上限（秒），超时记为失败；0 表示不限"),
    budget: str = typer.Option(None, "--budget", help="单个案例的 tokens 预算，例如 200k"),
    out: Path = typer.Option(None, "--out", "-o", help="结果目录；默认 .log-agent-eval/<时间>"),
    base_url: str = typer.Option(None, "--base-url", envvar="OPENAI_BASE_URL", help="OpenAI 兼容接口地址"),
) -> None:
    from .evals import RunSpec, case_table, load_cases, markdown_table, run_eval, summarize
    from .postprocess import STRUCTURED_MODES

    models = model or [os.environ.get("LOG_AGENT_MODEL") or "openai:gpt-4.1"]
    modes = structured or ["auto"]
    bad = [m for m in modes if m not in STRUCTURED_MODES]
    if bad:
        raise typer.BadParameter(f"--structured 只能是 {' / '.join(STRUCTURED_MODES)}：{', '.join(bad)}")
    try:
        loaded = load_cases(cases, only)
    except (OSError, ValueError) as exc:
        console.print(Text(f"{glyphs.fail} 读取案例失败：{exc}", style="err"))
        raise typer.Exit(2) from exc
    if not loaded:
        console.print(Text(f"{glyphs.fail} {cases} 下没有找到案例（每个案例目录需要一个 case.toml）", style="err"))
        raise typer.Exit(2)

    specs = [RunSpec(case=c, model=m, structured=s, base_url=base_url, max_steps=max_steps, budget=budget,
                     timeout=timeout)
             for _ in range(repeat) for m in models for s in modes for c in loaded]
    console.print(Text(f"共 {len(specs)} 次运行：{len(loaded)} 个案例 × {len(models)} 个模型 × {len(modes)} 种结构化方式"
                       + (f" × {repeat} 次" if repeat > 1 else "") + f"，并行 {jobs}", style="muted"))
    done = 0
    target = out or Path(".log-agent-eval") / datetime.now().strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    partial = target / "runs.jsonl"  # 逐条追加：中途中断时已完成的结果不丢
    partial.write_text("", encoding="utf-8")

    def progress(record: dict) -> None:
        nonlocal done
        done += 1
        with partial.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if "score" in record:
            line = (f"{glyphs.ok} [{done}/{len(specs)}] {record['case']} · {record['model']} · {record['structured']}"
                    f"  得分 {record['score']:.0f}  {record.get('seconds') or 0:.0f}s")
            console.print(Text(line, style="ok" if record["score"] >= 60 else "warn"))
        else:
            console.print(Text(f"{glyphs.fail} [{done}/{len(specs)}] {record['case']} · {record['model']} · "
                               f"{record['structured']}  {record.get('error', '')[:120]}", style="err"))

    results = run_eval(specs, jobs=jobs, on_result=progress)
    rows = summarize(results)
    (target / "results.json").write_text(
        json.dumps({"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    "summary": rows, "runs": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    report = f"# log-agent 评测结果\n\n## 汇总\n\n{markdown_table(rows)}\n\n## 逐案例\n\n{case_table(results)}\n"
    (target / "report.md").write_text(report, encoding="utf-8")
    console.print()
    from rich.markdown import Markdown

    console.print(Markdown(markdown_table(rows)))
    console.print(Text(f"\n完整结果：{target / 'report.md'}、{target / 'results.json'}", style="muted"))


@eval_app.command("export")
def export(
    session: str = typer.Argument(..., help="会话名"),
    turn: int = typer.Option(None, "--turn", "-t", help="第几轮；默认最近一轮"),
    out: Path = typer.Option(None, "--out", "-o", help="写入的案例目录；默认 evals/cases/<会话名>-<轮次>"),
    db: Path = typer.Option(None, "--db", help="会话数据库路径"),
) -> None:
    """把会话里的一轮导出为评测案例；带上这一轮的反馈（有用 → 回归用例，根因不对 → 待补全的标准答案）。"""
    from .evals import case_from_turn
    from .sessions import SessionStore, default_db_path

    path = db or default_db_path()
    if not path.exists():
        console.print(Text(f"{glyphs.fail} 会话数据库不存在：{path}", style="err"))
        raise typer.Exit(2)
    conn = sqlite3.connect(str(path))
    try:
        store = SessionStore(conn)
        number = turn or (store.get(session).turns if store.get(session) else 0)
        payload = store.turn(session, number) if number else None
        if payload is None:
            console.print(Text(f"{glyphs.fail} 会话 {session} 没有第 {number} 轮的报告", style="err"))
            raise typer.Exit(2)
        feedback = store.feedback(session).get(number)
    finally:
        conn.close()
    target = out or _DEFAULT_CASES / f"{session}-{number}"
    target.mkdir(parents=True, exist_ok=True)
    (target / "case.toml").write_text(case_from_turn(payload, feedback), encoding="utf-8")
    hint = "" if feedback and feedback.get("rating") == "up" else "（请补全 [expected] 里的判定与根因位置）"
    console.print(Text(f"{glyphs.ok} 已导出 {target / 'case.toml'}{hint}", style="ok"))
