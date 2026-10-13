from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".css", ".md", ".toml", ".json", ".html"}


def _tracked_text_files() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        paths = [ROOT / line for line in out.splitlines()]
    except (OSError, subprocess.CalledProcessError):
        paths = [p for base in ("src", "web/src", "docs", "tests") for p in (ROOT / base).rglob("*")]
    return [p for p in paths if p.suffix in TEXT_SUFFIXES and "static/assets" not in p.as_posix() and p.is_file()]


def test_source_files_have_no_replacement_characters() -> None:
    # U+FFFD 说明某次写入把多字节中文截断了：界面文案会显示成乱码，提示词里的会直接喂给模型
    bad = []
    for path in _tracked_text_files():
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "\ufffd" in line:
                bad.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:80]}")
    assert not bad, "源码里有损坏的字符：\n" + "\n".join(bad)
