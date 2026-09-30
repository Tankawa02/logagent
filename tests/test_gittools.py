from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from log_agent import timefilter, tools
from log_agent.gittools import blame_lines, recent_changes, show_commit
from log_agent.render import _summarize_meta, _tool_parts

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="需要 git")


def _git(root: Path, *args: str, when: str | None = None) -> str:
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    if when:
        env.update(GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
    out = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env)
    return out.stdout.decode().strip()


def _commit(root: Path, files: dict[str, str], message: str, when: str, author: str = "张三") -> str:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "-c", f"user.name={author}", "-c", "user.email=dev@example.com", "commit", "-q", "-m", message, when=when)
    return _git(root, "rev-parse", "--short", "HEAD")


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "payment-svc"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _commit(root, {"app/order.py": "def pay(order):\n    return order['id']\n", "README.md": "svc\n"},
            "初始化订单服务", "2026-06-01T10:00:00+08:00")
    _commit(root, {"app/order.py": "def pay(order):\n    # 改用新字段\n    return order['order_id']\n"},
            "支付改读 order_id 字段", "2026-06-09T13:55:00+08:00", author="李四")
    _commit(root, {"docs/notes.md": "key = sk-abcdefghijklmnopqrstu\n"}, "补充文档", "2026-06-09T16:00:00+08:00")
    return root


# ---------------------------------------------------------------------------
# recent_changes
# ---------------------------------------------------------------------------


def test_recent_changes_lists_commits_with_files(git_repo: Path) -> None:
    timefilter.set_default_timezone("+08:00")
    out = recent_changes(str(git_repo))
    assert out.status == "ok" and out.meta["commits"] == 3
    assert "支付改读 order_id 字段" in out and "李四" in out
    assert "2026-06-09 13:55:00+08:00" in out
    assert "app/order.py  +2 -1" in out
    assert "当前 main @" in out


def test_recent_changes_time_window_uses_log_timezone(git_repo: Path) -> None:
    timefilter.set_default_timezone("+08:00")
    out = recent_changes(str(git_repo), since="2026-06-09 12:00", until="2026-06-09 14:00")
    assert out.meta["commits"] == 1 and "支付改读" in out and "补充文档" not in out
    # 同样的墙上时间按 UTC 解释，窗口整体后移 8 小时
    timefilter.set_default_timezone("UTC")
    assert "补充文档" in recent_changes(str(git_repo), since="2026-06-09 07:00", until="2026-06-09 09:00")


def test_recent_changes_path_filter_and_subdirectory(git_repo: Path) -> None:
    out = recent_changes(str(git_repo), path="app/*.py")
    assert out.meta["commits"] == 2 and "补充文档" not in out
    sub = recent_changes(str(git_repo / "app"))
    # code_dir 是子目录时，路径相对子目录显示，与 read_code_file 一致
    assert sub.meta["commits"] == 2 and "    order.py  +2 -1" in sub


def test_recent_changes_limits_and_reports_truncation(git_repo: Path) -> None:
    out = recent_changes(str(git_repo), max_commits=1)
    assert out.meta["commits"] == 1 and out.meta["truncated"] and "还有更早的提交" in out


def test_recent_changes_empty_range_and_dirty_worktree(git_repo: Path) -> None:
    (git_repo / "app" / "order.py").write_text("changed\n", encoding="utf-8")
    out = recent_changes(str(git_repo), since="2030-01-01")
    assert out.status == "hint" and "没有提交" in out and "未提交的修改" in out


@pytest.mark.parametrize("since", ["14:00", "bogus"])
def test_recent_changes_rejects_bad_time(git_repo: Path, since: str) -> None:
    assert recent_changes(str(git_repo), since=since).status == "error"


def test_recent_changes_accepts_relative_time(git_repo: Path) -> None:
    assert recent_changes(str(git_repo), since="3 days ago").status in {"ok", "hint"}


def test_non_repo_is_a_hint(tmp_path: Path) -> None:
    out = recent_changes(str(tmp_path))
    assert out.status == "hint" and "不在 git 仓库内" in out


# ---------------------------------------------------------------------------
# show_commit
# ---------------------------------------------------------------------------


def test_show_commit_returns_message_and_diff(git_repo: Path) -> None:
    sha = _git(git_repo, "log", "--format=%h", "--grep=order_id")
    out = show_commit(str(git_repo), sha)
    assert out.status == "ok"
    assert "支付改读 order_id 字段" in out and "+    return order['order_id']" in out
    assert out.meta["files"] == 1 and out.meta["additions"] == 2 and out.meta["deletions"] == 1


def test_show_commit_redacts_secrets_and_filters_path(git_repo: Path) -> None:
    out = show_commit(str(git_repo), "HEAD")
    assert "sk-abcdefghijklmnopqrstu" not in out and "sk-[已脱敏]" in out
    assert show_commit(str(git_repo), "HEAD", path="app").status == "hint"


@pytest.mark.parametrize(("ref", "status"), [("--output=/tmp/x", "error"), ("a b", "error"), ("deadbeef", "hint")])
def test_show_commit_validates_refs(git_repo: Path, ref: str, status: str) -> None:
    assert show_commit(str(git_repo), ref).status == status


def test_show_commit_truncates_long_diffs(git_repo: Path) -> None:
    _commit(git_repo, {"big.txt": "".join(f"line {i}\n" for i in range(500))}, "大文件", "2026-06-10T10:00:00+08:00")
    out = show_commit(str(git_repo), "HEAD", max_lines=50)
    assert out.meta["truncated"] and "已显示前 50 行" in out


# ---------------------------------------------------------------------------
# blame_lines
# ---------------------------------------------------------------------------


def test_blame_groups_lines_by_commit(git_repo: Path) -> None:
    timefilter.set_default_timezone("+08:00")
    out = blame_lines(str(git_repo), "app/order.py", 1, 3)
    assert out.status == "ok" and out.meta["commits"] == 2
    assert "L2-3" in out and "李四" in out and "支付改读 order_id 字段" in out
    assert "3 | " in out and "return order['order_id']" in out
    assert out.meta["newest"] == "2026-06-09 13:55:00+08:00"


def test_blame_marks_uncommitted_changes(git_repo: Path) -> None:
    (git_repo / "app" / "order.py").write_text("def pay(order):\n    return None\n", encoding="utf-8")
    out = blame_lines(str(git_repo), "app/order.py", 2)
    assert "未提交的本地修改" in out and out.meta["uncommitted"]


def test_blame_rejects_traversal_and_untracked(git_repo: Path) -> None:
    assert blame_lines(str(git_repo / "app"), "../README.md", 1).status == "error"
    (git_repo / "new.py").write_text("x = 1\n", encoding="utf-8")
    assert blame_lines(str(git_repo), "new.py", 1).status == "hint"
    assert blame_lines(str(git_repo), "app/order.py", 99).status == "hint"


# ---------------------------------------------------------------------------
# 注册与展示
# ---------------------------------------------------------------------------


def test_git_tools_are_registered_and_rendered(git_repo: Path) -> None:
    names = {t.name for t in tools.as_langchain_tools()}
    assert {"recent_changes", "show_commit", "blame_lines"} <= names
    out = recent_changes(str(git_repo))
    assert _summarize_meta("recent_changes", out.meta).startswith("3 个提交")
    assert "+2 -1" in _summarize_meta("show_commit", show_commit(str(git_repo), "HEAD~1").meta)
    parts = [text for text, _ in _tool_parts("blame_lines", {"rel_path": "app/order.py", "start_line": 2,
                                                              "end_line": 5, "code_dir": str(git_repo)})]
    assert parts[:2] == ["app/order.py", "L2-5"]
