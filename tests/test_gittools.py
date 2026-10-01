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


@pytest.mark.parametrize("path", ["../README.md", "../docs/*.md", "..", "*/../../README.md",
                                  ":(top)README.md", ":/README.md", ":(exclude)order.py"])
def test_history_tools_reject_paths_outside_code_dir(git_repo: Path, path: str) -> None:
    sub = str(git_repo / "app")
    assert recent_changes(sub, path=path).status == "error"
    assert show_commit(sub, "HEAD", path=path).status == "error"


def test_history_tools_reject_absolute_paths(git_repo: Path) -> None:
    path = str(git_repo / "README.md")
    assert recent_changes(str(git_repo / "app"), path=path).status == "error"
    assert show_commit(str(git_repo / "app"), "HEAD", path=path).status == "error"


@pytest.mark.parametrize("path", ["", ".", "order.py", "*.py", "./*.py"])
def test_history_tools_scope_subdirectory(git_repo: Path, path: str) -> None:
    _commit(git_repo, {"app/order.py": "inside_change = 1\n", "README.md": "outside_change = 1\n"},
            "mixed change", "2026-06-10T10:00:00+08:00")
    (git_repo / "README.md").write_text("outside dirty\n", encoding="utf-8")
    sub = str(git_repo / "app")
    history = recent_changes(sub, path=path)
    assert history.status == "ok" and history.meta["commits"] == 3
    assert "README.md" not in history and "补充文档" not in history
    assert "未提交的修改" not in history
    diff = show_commit(sub, "HEAD", path=path)
    assert diff.status == "ok" and diff.meta["files"] == 1
    assert "inside_change" in diff and "outside_change" not in diff and "README.md" not in diff
    assert show_commit(sub, "HEAD~1", path=path).status == "hint"


def test_recent_changes_limits_and_reports_truncation(git_repo: Path) -> None:
    out = recent_changes(str(git_repo), max_commits=1)
    assert out.meta["commits"] == 1 and out.meta["truncated"] and "还有更早的提交" in out


def test_recent_changes_empty_range(git_repo: Path) -> None:
    out = recent_changes(str(git_repo), since="2030-01-01")
    assert out.status == "hint" and "没有提交" in out


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
    assert out.meta["truncated"] and "diff 超过 50 行" in out


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


def test_blame_uses_head_and_flags_drifted_worktree(git_repo: Path) -> None:
    (git_repo / "app" / "order.py").write_text("def pay(order):\n    return None\n", encoding="utf-8")
    out = blame_lines(str(git_repo), "app/order.py", 3)
    # HEAD 版本仍有第 3 行；工作区内容不参与
    assert "return order['order_id']" in out and out.meta["drifted"]
    assert "工作区文件与 HEAD 版本不同" in out


@pytest.mark.parametrize("key_type", ["", "RSA ", "EC ", "OPENSSH "])
def test_blame_redacts_multiline_keys_across_commits(git_repo: Path, key_type: str) -> None:
    content = (f"before = 1\n-----BEGIN {key_type}PRIVATE KEY-----\n"
               f"first_private_payload\nsecond_private_payload\n-----END {key_type}PRIVATE KEY-----\n"
               "after = 2\n\nkey = sk-abcdefghijklmnopqrstu\n")
    _commit(git_repo, {"app/keys.txt": content}, "add fixture", "2026-06-10T10:00:00+08:00")
    content = content.replace("second_private_payload", "changed_private_payload")
    _commit(git_repo, {"app/keys.txt": content}, "change fixture", "2026-06-11T10:00:00+08:00")
    out = blame_lines(str(git_repo), "app/keys.txt", 1, 8)
    assert out.status == "ok" and out.meta["commits"] == 2 and out.meta["end"] == 8
    assert "private_payload" not in out and "PRIVATE KEY" not in out
    assert "sk-abcdefghijklmnopqrstu" not in out and "sk-[已脱敏]" in out
    for number in range(2, 6):
        assert f"{number} | [私钥已脱敏]" in out
    assert "1 | before = 1" in out and "6 | after = 2" in out and "7 | " in out
    assert "L1-3" in out and "L4 " in out and "L5-8" in out


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


# ---------------------------------------------------------------------------
# 审查修复的回归用例
# ---------------------------------------------------------------------------


def test_subdirectory_code_dir_never_leaks_outside(git_repo: Path) -> None:
    app = str(git_repo / "app")
    shown = show_commit(app, "HEAD")  # HEAD 只改了 docs/，对 app/ 来说没有改动
    assert shown.status == "hint" and "sk-" not in shown and "notes.md" not in shown
    first = _git(git_repo, "rev-list", "--max-parents=0", "HEAD")
    assert "README.md" not in show_commit(app, first)
    for bad in ("../README.md", "../docs", ":(top)README.md", "/etc/passwd", "a/../../README.md"):
        assert recent_changes(app, path=bad).status == "error", bad
        assert show_commit(app, "HEAD", path=bad).status == "error", bad


_KEY = ["-----BEGIN RSA PRIVATE KEY-----", *[f"MIIEow{i:02d}" + "A" * 58 for i in range(20)],
        "-----END RSA PRIVATE KEY-----"]


def _key_leaked(text: str) -> bool:
    return any(line in text for line in _KEY[1:-1])


def test_blame_masks_key_body_even_for_partial_ranges(git_repo: Path) -> None:
    _commit(git_repo, {"conf/key.pem": "\n".join(["# deploy key", *_KEY, "tail"]) + "\n"}, "加密钥",
            "2026-06-10T10:00:00+08:00")
    out = blame_lines(str(git_repo), "conf/key.pem", 5, 8)  # 只 blame 私钥正文中间几行
    assert out.status == "ok" and not _key_leaked(out) and "[私钥已脱敏]" in out


def test_truncated_diff_does_not_leak_key(git_repo: Path) -> None:
    _commit(git_repo, {"conf/key.pem": "\n".join(_KEY) + "\n"}, "加密钥", "2026-06-10T10:00:00+08:00")
    for max_lines in (20, 25, 1000):  # 截断点落在块中间 / 块外
        out = show_commit(str(git_repo), "HEAD", max_lines=max_lines)
        assert not _key_leaked(out), max_lines


def test_show_commit_streams_large_diffs(git_repo: Path) -> None:
    _commit(git_repo, {"big.txt": "x" * 200_000 + "\n" + "".join(f"l{i}\n" for i in range(50_000))}, "巨型提交",
            "2026-06-10T10:00:00+08:00")
    out = show_commit(str(git_repo), "HEAD", max_lines=40)
    assert out.meta["truncated"] and out.meta["additions"] == 50_001
    assert len(str(out)) < 40_000


def _evil(root: Path, marker: Path) -> None:
    cmd = f"touch {marker}"
    _git(root, "config", "core.fsmonitor", cmd)
    _git(root, "config", "filter.evil.clean", f"{cmd}; cat")
    _git(root, "config", "filter.evil.smudge", f"{cmd}; cat")
    _git(root, "config", "log.showSignature", "true")
    _git(root, "config", "gpg.program", str(root / "fake-gpg.sh"))
    (root / "fake-gpg.sh").write_text(f"#!/bin/sh\n{cmd}\n", encoding="utf-8")
    (root / "fake-gpg.sh").chmod(0o755)
    (root / ".gitattributes").write_text("* filter=evil\n", encoding="utf-8")
    (root / "app" / "order.py").write_text("changed\n", encoding="utf-8")


def test_untrusted_repo_config_cannot_run_commands(git_repo: Path, tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    _evil(git_repo, marker)
    recent_changes(str(git_repo))
    recent_changes(str(git_repo), path="app")
    show_commit(str(git_repo), "HEAD~1")
    blame_lines(str(git_repo), "app/order.py", 1, 2)
    assert not marker.exists()


# ---------------------------------------------------------------------------
# review findings (#42)
# ---------------------------------------------------------------------------


def test_recent_changes_flags_truncation_inside_a_huge_commit(git_repo: Path) -> None:
    # max_commits=1 的行数预算是 800 行：一个改了 900 个文件的提交会在中间被截断
    _commit(git_repo, {f"gen/f{i}.txt": "x\n" for i in range(900)}, "bulk", "2026-06-10T10:00:00+08:00")
    out = recent_changes(str(git_repo), max_commits=1)
    assert out.status == "ok"
    assert out.meta["truncated"] is True and out.meta["partial"] is True
    assert "至少" in out and "不完整" in out
    # 预算充足时不误报
    ok = recent_changes(str(git_repo), max_commits=20)
    assert ok.meta["commits"] == 4 and ok.meta["partial"] is False


def test_show_commit_totals_are_complete_for_large_commits(git_repo: Path) -> None:
    _commit(git_repo, {f"gen/f{i}.txt": "a\nb\n" for i in range(900)}, "bulk", "2026-06-10T10:00:00+08:00")
    out = show_commit(str(git_repo), "HEAD", max_lines=20)
    assert out.meta["files"] == 900 and out.meta["additions"] == 1800 and out.meta["deletions"] == 0
    assert out.meta["truncated"] is True


def test_blame_drift_check_matches_git_line_splitting(git_repo: Path) -> None:
    # 换页符、\\x1c、\\u2028、CRLF、超长行都不应该让未改动的文件被当成“工作区已变更”
    content = "a = 1\x0c\nb = '\x1c\u2028'\r\nc = '" + "x" * 70_000 + "'\nd = 4"
    path = git_repo / "app" / "odd.py"
    path.write_bytes(content.encode("utf-8"))
    _git(git_repo, "add", "-A")
    _git(git_repo, "-c", "user.name=a", "-c", "user.email=a@b", "-c", "core.autocrlf=false", "commit", "-q", "-m", "odd")
    out = blame_lines(str(git_repo), "app/odd.py", 1, 4)
    assert out.status == "ok" and out.meta["drifted"] is False
    path.write_bytes((content + "\ne = 5").encode("utf-8"))
    assert blame_lines(str(git_repo), "app/odd.py", 1, 1).meta["drifted"] is True


def test_blame_drift_check_does_not_read_huge_worktree_files(git_repo: Path, monkeypatch) -> None:
    path = git_repo / "app" / "order.py"
    path.write_bytes(b"x" * (6 * 1024 * 1024))

    def boom(self, *args, **kwargs):  # noqa: ARG001
        raise AssertionError("不应整个读入工作区文件")

    monkeypatch.setattr(Path, "read_bytes", boom)
    out = blame_lines(str(git_repo), "app/order.py", 1, 1)
    assert out.status == "ok" and out.meta["drifted"] is False


def test_git_never_lazy_fetches_from_promisor_remote(tmp_path: Path) -> None:
    # partial clone 的仓库把 promisor remote 指向 ext:: 命令，读历史不能触发它
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q", "-b", "main")
    _git(src, "config", "uploadpack.allowFilter", "true")
    _commit(src, {"app/a.py": "old = 1\n"}, "one", "2026-06-01T10:00:00+08:00")
    _commit(src, {"app/a.py": "new = 2\n"}, "two", "2026-06-02T10:00:00+08:00")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", f"file://{src}", str(clone)],
                   check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})
    marker = tmp_path / "PWNED"
    _git(clone, "config", "remote.origin.url", f"ext::sh -c touch% {marker}")
    _git(clone, "config", "protocol.ext.allow", "always")
    show_commit(str(clone), "HEAD~1")
    recent_changes(str(clone))
    assert not marker.exists()
