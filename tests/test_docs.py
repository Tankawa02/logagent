"""README 开头保持一段能直接照抄的快速开始；拆到 docs/ 的文档链接不能断。"""

from __future__ import annotations

import re
from pathlib import Path

from typer.main import get_command

from log_agent import cli

ROOT = Path(__file__).resolve().parents[1]
DOCS = sorted((ROOT / "docs").glob("*.md"))


def _code_block(lines: list[str], start: int) -> tuple[list[str], int]:
    begin = lines.index("```bash", start) + 1
    end = lines.index("```", begin)
    return lines[begin:end], end + 1


def test_readme_starts_with_web_then_cli_quick_start() -> None:
    lines = (ROOT / "README.md").read_text(encoding="utf-8").splitlines()
    start = lines.index("## 快速开始")
    assert start < 10, "快速开始要放在 README 开头"
    web, after = _code_block(lines, start)
    assert len(web) == 2, "推荐上手方式：安装（含 web 依赖）+ serve 两行"
    assert web[0].startswith("uv tool install") and "[web]" in web[0]
    assert web[1].startswith("log-agent serve")
    cli_block, _ = _code_block(lines, after)
    assert len(cli_block) == 3
    assert cli_block[0].startswith("uv tool install") and cli_block[1].startswith("log-agent init")
    assert cli_block[2].startswith("log-agent analyze")


def test_readme_stays_short() -> None:
    assert len((ROOT / "README.md").read_text(encoding="utf-8").splitlines()) < 120


def test_relative_links_resolve() -> None:
    for page in [ROOT / "README.md", *DOCS]:
        text = page.read_text(encoding="utf-8")
        assert text.count("```") % 2 == 0, page
        for target in re.findall(r"\]\(([^)#:]+\.md)(?:#[^)]*)?\)", text):
            assert (page.parent / target).resolve().is_file(), f"{page.name} -> {target}"


def test_documented_commands_exist() -> None:
    commands = set(get_command(cli.app).commands)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in re.findall(r"`log-agent (\w+)", readme):
        assert name in commands, name
    params = {p.name for p in get_command(cli.app).commands["doctor"].params}
    assert {"ping", "ping_timeout"} <= params
