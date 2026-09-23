from __future__ import annotations

from pathlib import Path

from rich.cells import cell_len

from log_agent.render import display_path
from log_agent.term import glyphs

LONG = "/tmp/pytest-of-runner/pytest-0/test_chat_resume_reuses_source0/app.log"


def test_short_path_unchanged() -> None:
    assert display_path("/var/log/app.log", 60) == "/var/log/app.log"


def test_long_path_keeps_file_name_and_fits() -> None:
    shown = display_path(LONG, 50)
    assert shown.endswith("/test_chat_resume_reuses_source0/app.log")
    assert shown.startswith("/" + glyphs.ellipsis)
    assert cell_len(shown) <= 50


def test_very_narrow_keeps_only_file_name() -> None:
    shown = display_path(LONG, 20)
    assert shown == f"/{glyphs.ellipsis}/app.log"


def test_windows_path_uses_backslash() -> None:
    path = r"C:\Users\runneradmin\AppData\Local\Temp\pytest-of-runneradmin\pytest-0\case0\app.log"
    shown = display_path(path, 40)
    assert shown.startswith("C:\\" + glyphs.ellipsis) and shown.endswith("\\case0\\app.log")
    assert cell_len(shown) <= 40


def test_home_is_abbreviated() -> None:
    home = str(Path.home())
    inside = str(Path.home() / "work" / "app.log")
    assert display_path(inside, 200) == "~" + inside[len(home):]
    sibling = home + "-other" + inside[len(home):]
    assert display_path(sibling, 200) == sibling
