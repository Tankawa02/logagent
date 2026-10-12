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
    experience: bool = typer.Option(
        False, "--experience/--no-experience", help="注入经验库里已生效的经验（默认不用，衡量模型本身）",
    ),
) -> None:
    from .evals import RunSpec, case_table, load_cases, markdown_table, run_eval, summarize
    from .experience import default_experience_path
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

    experience_db = str(default_experience_path()) if experience else None
    specs = [RunSpec(case=c, model=m, structured=s, base_url=base_url, max_steps=max_steps, budget=budget,
                     timeout=timeout, experience_db=experience_db)
             for _ in range(repeat) for m in models for s in modes for c in loaded]
    console.print(Text(f"共 {len(specs)} 次运行：{len(loaded)} 个案例 × {len(models)} 个模型 × {len(modes)} 种结构化方式"
                       + (f" × {repeat} 次" if repeat > 1 else "") + f"，并行 {jobs}", style="muted"))
    target = out or Path(".log-agent-eval") / datetime.now().strftime("%Y%m%d-%H%M%S")
    target.mkdir(parents=True, exist_ok=True)
    partial = target / "runs.jsonl"  # 逐条追加：中途中断时已完成的结果不丢
    partial.write_text("", encoding="utf-8")

    results = run_eval(specs, jobs=jobs, on_result=_progress_printer(len(specs), partial))
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


def _progress_printer(total: int, partial: Path):
    done = 0

    def progress(record: dict) -> None:
        nonlocal done
        done += 1
        with partial.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        if "score" in record:
            line = (f"{glyphs.ok} [{done}/{total}] {record['case']} · {record['model']} · {record['structured']}"
                    f"  得分 {record['score']:.0f}  {record.get('seconds') or 0:.0f}s")
            console.print(Text(line, style="ok" if record["score"] >= 60 else "warn"))
        else:
            console.print(Text(f"{glyphs.fail} [{done}/{total}] {record['case']} · {record['model']} · "
                               f"{record['structured']}  {record.get('error', '')[:120]}", style="err"))

    return progress


@eval_app.command("learn")
def learn(
    cases: Path = typer.Option(_DEFAULT_CASES, "--cases", "-c", help="案例目录（含多个案例子目录）"),
    model: str = typer.Option(None, "--model", "-m", help="被评测 / 学习的模型；默认读 LOG_AGENT_MODEL"),
    holdout: list[str] = typer.Option(
        None, "--holdout", help="留出集案例名，可多次指定；只用来检验，不参与提炼。默认按名字每 3 个取 1 个",
    ),
    repeat: int = typer.Option(1, "--repeat", "-r", min=1, max=5, help="每个案例重复次数，次数越多门控越不受随机性影响"),
    tolerance: float = typer.Option(0.0, "--tolerance", min=0.0, max=20.0, help="允许的平均分波动（分）"),
    with_pending: bool = typer.Option(
        False, "--with-pending", help="把经验库里已有的全局候选（例如在线复盘提炼的）也放进这一批一起检验",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="只跑流程、出报告，不改变任何经验的状态"),
    jobs: int = typer.Option(4, "--jobs", "-j", min=1, max=32, help="并行进程数"),
    max_steps: int = typer.Option(80, "--max-steps", min=10, help="单个案例的最大推理步数"),
    timeout: int = typer.Option(900, "--timeout", min=0, help="单个案例的墙钟上限（秒）；0 表示不限"),
    budget: str = typer.Option(None, "--budget", help="单个案例的 tokens 预算，例如 200k"),
    db: Path = typer.Option(None, "--db", help="经验库路径（默认 ~/.log-agent/experience.db）"),
    out: Path = typer.Option(None, "--out", "-o", help="结果目录；默认 .log-agent-eval/learn-<时间>"),
    base_url: str = typer.Option(None, "--base-url", envvar="OPENAI_BASE_URL", help="OpenAI 兼容接口地址"),
) -> None:
    """离线经验学习：跑基线 → 复盘训练集提炼候选经验 → 带上候选重跑 → 留出集和全体都不退化才整批生效。"""
    from .agent import resolve_post_models
    from .evals import RunSpec, gate_decision, gate_table, load_cases, reflect_on_eval, run_eval, split_cases
    from .experience import ExperienceStore, default_experience_path

    name = model or os.environ.get("LOG_AGENT_MODEL") or "openai:gpt-4.1"
    try:
        loaded = load_cases(cases)
        train, held = split_cases(loaded, holdout)
    except (OSError, ValueError) as exc:
        console.print(Text(f"{glyphs.fail} 读取案例失败：{exc}", style="err"))
        raise typer.Exit(2) from exc
    if not train or not held:
        console.print(Text(f"{glyphs.fail} 训练集和留出集都至少需要 1 个案例（共 {len(loaded)} 个）", style="err"))
        raise typer.Exit(2)
    store_path = db or default_experience_path()
    store = ExperienceStore(store_path)
    target = out or Path(".log-agent-eval") / f"learn-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    target.mkdir(parents=True, exist_ok=True)
    batch = target.name
    train_names, held_names = [c.name for c in train], [c.name for c in held]
    console.print(Text(f"训练集 {len(train)} 个：{', '.join(train_names)}；留出集 {len(held)} 个：{', '.join(held_names)}",
                       style="muted"))

    def specs(include: list[int]) -> list[RunSpec]:
        return [RunSpec(case=c, model=name, structured="auto", base_url=base_url, max_steps=max_steps, budget=budget,
                        timeout=timeout, experience_db=str(store_path), experience_include=include)
                for _ in range(repeat) for c in [*train, *held]]

    try:
        console.print(Text("\n[1/3] 基线：只用已生效的经验", style="accent"))
        first = specs([])
        baseline = run_eval(first, jobs=jobs, on_result=_progress_printer(len(first), target / "baseline.jsonl"))

        console.print(Text("\n[2/3] 复盘训练集，提炼候选经验", style="accent"))
        models = resolve_post_models(name, base_url)
        train_runs = [r for r in baseline if r["case"] in set(train_names)]
        reflected = reflect_on_eval(models, store, train, train_runs, batch)
        include = [e.id for e in store.batch(batch) if e.status == "candidate"]
        if with_pending:
            include += [e.id for e in store.list(None, status="candidate") if e.project is None and e.id not in include]
        console.print(Text(
            f"复盘 {reflected['runs']} 次运行：新增候选 {len(reflected['added'])} 条，合并 {len(reflected['merged'])} 条，"
            f"安全检查拒绝 {len(reflected['rejected'])} 条；投票 有用 {reflected['votes']['helpful']} / "
            f"有害 {reflected['votes']['harmful']}" + (f"；复盘失败 {reflected['errors']} 次" if reflected["errors"] else ""),
            style="muted",
        ))
        if not include:
            console.print(Text("这一轮没有需要检验的候选经验，结束。", style="muted"))
            _write_learn(target, {"batch": batch, "reflection": reflected, "baseline": baseline, "decision": None})
            return

        console.print(Text(f"\n[3/3] 带上 {len(include)} 条候选重跑，检验是否退化", style="accent"))
        second = specs(include)
        gated = run_eval(second, jobs=jobs, on_result=_progress_printer(len(second), target / "gated.jsonl"))
        decision = gate_decision(baseline, gated, train_names, held_names, tolerance)
        decision["candidates"] = include
        if dry_run:
            verdict = "（--dry-run：不改变经验状态）"
        elif decision["accepted"]:
            for experience_id in include:
                store.set_status(experience_id, "active")
            verdict = f"{glyphs.ok} 通过门控：{len(include)} 条候选已生效"
        else:
            # 这一批新提炼的没通过就删掉；--with-pending 带进来的候选保持原状，留给用户自己决定
            for entry in store.batch(batch):
                if entry.status == "candidate":
                    store.remove(entry.id)
            verdict = f"{glyphs.fail} 未通过门控：本批新提炼的候选已丢弃"
        table = gate_table(decision)
        _write_learn(target, {"batch": batch, "reflection": reflected, "decision": decision,
                              "baseline": baseline, "gated": gated}, table, verdict)
        from rich.markdown import Markdown

        console.print()
        console.print(Markdown(table))
        console.print(Text(verdict, style="ok" if decision["accepted"] and not dry_run else "warn"))
        console.print(Text(f"完整结果：{target / 'learn.md'}、{target / 'learn.json'}", style="muted"))
    finally:
        store.close()


def _write_learn(target: Path, data: dict, table: str = "", verdict: str = "") -> None:
    data = {"generated_at": datetime.now().astimezone().isoformat(timespec="seconds"), **data}
    (target / "learn.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    body = f"# log-agent 离线经验学习\n\n{verdict}\n\n{table}\n" if table else "# log-agent 离线经验学习\n\n没有候选经验。\n"
    (target / "learn.md").write_text(body, encoding="utf-8")


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
