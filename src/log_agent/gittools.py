"""git 变更关联工具：让 agent 能把报错和“最近改了什么”对上。

三个工具都只读：
- `recent_changes`  某段时间内的提交（git log），附改动文件与增删行数
- `show_commit`     某个提交的说明与 diff（git show）
- `blame_lines`     源码某几行最后一次由哪个提交修改（git blame）

约定：
- 时间按日志默认时区（`--timezone`）显示，方便和日志时间直接比较。
- 路径一律相对 `code_dir`（与 read_code_file 一致），code_dir 是仓库子目录时也成立。
- 提交说明按日志规则脱敏，diff 与源码行按源码规则脱敏。
- 调 git 时关闭分页器、外部 diff、textconv、交互提示与可选锁，不会修改仓库。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .logfile import clip_line
from .redact import redact_code, redact_log
from .timefilter import BOUND_EXAMPLES, default_timezone, parse_bound, utc_stamp
from .tooloutput import ToolOutput, _err, _hint, _ok

GIT_TIMEOUT = 20
MAX_COMMITS = 100
DEFAULT_COMMITS = 20
MAX_FILES_PER_COMMIT = 15
DEFAULT_DIFF_LINES = 300
MAX_DIFF_LINES = 1000
MAX_BLAME_LINES = 200
_DIFF_LINE_CHARS = 400

_REF = re.compile(r"^[\w./~^@{}+-]{1,200}$")
_RELATIVE = re.compile(r"^\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago$", re.IGNORECASE)
_RS, _US = "\x1e", "\x1f"


class _GitError(Exception):
    pass


@dataclass
class _Repo:
    base: Path
    top: Path

    @property
    def label(self) -> str:
        if self.base == self.top:
            return self.top.name
        return f"{self.top.name}/{self.base.relative_to(self.top).as_posix()}"


def _run(base: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "LC_ALL": "C",
    }
    cmd = ["git", "-c", "core.quotepath=off", "-c", "color.ui=never", "--no-pager", "-C", str(base), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=GIT_TIMEOUT, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise _GitError(f"git 执行超时（{GIT_TIMEOUT}s）") from exc
    except OSError as exc:
        raise _GitError(f"无法执行 git：{exc}") from exc
    if proc.returncode != 0:
        message = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise _GitError(message[-1] if message else f"git 退出码 {proc.returncode}")
    return proc.stdout.decode("utf-8", errors="replace")


def _open_repo(code_dir: str) -> tuple[_Repo | None, ToolOutput | None]:
    base = Path(code_dir).expanduser().resolve()
    if not base.is_dir():
        return None, _err(f"源码目录不存在: {code_dir}")
    if shutil.which("git") is None:
        return None, _hint("本机未安装 git，无法查看提交历史；可改用 read_code_file / grep_code。", "no_git")
    try:
        top = Path(_run(base, "rev-parse", "--show-toplevel").strip()).resolve()
    except _GitError as exc:
        if "dubious ownership" in str(exc):
            return None, _hint(
                f"git 认为该仓库属主不安全，拒绝读取（{exc}）。可让用户执行 "
                f"`git config --global --add safe.directory {base}` 后重试。", "unsafe_repo",
            )
        return None, _hint(f"{code_dir} 不在 git 仓库内，无法查看提交历史。", "no_repo")
    return _Repo(base, top), None


def _stamp(epoch: str | int) -> str:
    """按日志默认时区格式化提交时间：`2026-06-09 13:55:02+08:00`。"""
    value = datetime.fromtimestamp(int(epoch), tz=default_timezone())
    offset = value.strftime("%z")
    offset = "Z" if offset in {"", "+0000"} else f"{offset[:3]}:{offset[3:]}"
    return value.strftime("%Y-%m-%d %H:%M:%S") + offset


def _tz_label() -> str:
    offset = datetime.now(default_timezone()).strftime("%z")
    return "UTC" if offset in {"", "+0000"} else f"{offset[:3]}:{offset[3:]}"


def _git_time(text: str, *, upper: bool) -> str | None:
    """把时间边界转成 git 能精确理解的写法；无偏移时间按日志默认时区解释。"""
    raw = (text or "").strip()
    if not raw:
        return None
    if _RELATIVE.match(raw):
        return raw
    bound = parse_bound(raw, upper=upper)
    if bound.time_only:
        raise ValueError(f"查提交历史需要完整日期，例如 2026-06-09 14:00（收到 '{raw}'）")
    return f"@{int(utc_stamp(bound.value).timestamp())}"


def _pathspec(repo: _Repo, path: str) -> list[str]:
    path = (path or "").strip()
    if not path:
        # --relative 只裁剪输出，不能代替对子目录的查询范围限制。
        return ["--", "."] if repo.base != repo.top else []
    # 不接受 Git 的 top/exclude 等魔法路径，也不允许通配路径借 .. 跳出目录。
    if (path.startswith(":") or Path(path).is_absolute() or ".." in Path(path).parts
            or not (repo.base / path).resolve().is_relative_to(repo.base)):
        raise ValueError(f"非法路径（必须位于 code_dir 内）: {path}")
    # `**` 等通配需要 glob 魔法前缀；普通路径原样传入
    return ["--", f":(glob){path}" if any(ch in path for ch in "*?[") else path]


def _check_ref(repo: _Repo, ref: str) -> str:
    ref = (ref or "").strip()
    if not ref or ref.startswith("-") or not _REF.match(ref):
        raise ValueError(f"非法的提交号: {ref!r}")
    try:
        return _run(repo.base, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()
    except _GitError as exc:
        raise LookupError(f"仓库里找不到提交 {ref}") from exc


def _head_line(repo: _Repo) -> str:
    try:
        branch = _run(repo.base, "rev-parse", "--abbrev-ref", "HEAD").strip()
        head = _run(repo.base, "rev-parse", "--short", "HEAD").strip()
    except _GitError:
        return "当前没有提交"
    return f"当前 {branch} @ {head}" if branch != "HEAD" else f"当前处于游离 HEAD @ {head}"


def _dirty_note(repo: _Repo, spec: list[str]) -> str:
    try:
        changed = [ln for ln in _run(repo.base, "status", "--porcelain=v1", "-uno", *spec).splitlines() if ln.strip()]
    except _GitError:
        return ""
    if not changed:
        return ""
    return f"注意：工作区另有 {len(changed)} 个已跟踪文件存在未提交的修改，线上运行的代码可能与工作区不同。"


# ---------------------------------------------------------------------------
# recent_changes
# ---------------------------------------------------------------------------


@dataclass
class _Commit:
    sha: str
    short: str
    when: str
    author: str
    merge: bool
    subject: str
    files: list[tuple[str, str, str]] = field(default_factory=list)


def _parse_log(text: str) -> list[_Commit]:
    commits: list[_Commit] = []
    for record in text.split(_RS):
        if not record.strip():
            continue
        head, _, rest = record.partition("\n")
        parts = head.split(_US)
        if len(parts) != 6:
            continue
        sha, short, epoch, author, parents, subject = parts
        commit = _Commit(sha, short, _stamp(epoch), author, len(parents.split()) > 1, subject)
        for line in rest.splitlines():
            cols = line.split("\t")
            if len(cols) == 3:
                commit.files.append((cols[0], cols[1], cols[2]))
        commits.append(commit)
    return commits


def recent_changes(code_dir: str, since: str = "", until: str = "", path: str = "", max_commits: int = 20) -> ToolOutput:
    """查看源码仓库在某段时间内的提交记录（git log），每个提交附改动文件与增删行数。

    排查“是不是最近改出来的问题”时使用：先用日志确定问题开始的时间，再查这之前 1~3 天的提交，
    重点关注改动了报错栈帧所在文件的提交，然后用 show_commit 看具体改动。
    时间按日志默认时区显示。提交时间不等于上线时间，据此下结论时要说明这一点。

    Args:
        code_dir: 源码目录（需在 git 仓库内，可以是仓库的子目录）。
        since: 起始时间，如 `2026-06-09 14:00`、`2026-06-09`，或 `3 days ago`；空字符串表示不限。
        until: 结束时间，写法同 since；空字符串表示不限。
        path: 只看某个文件或目录的提交（相对 code_dir，支持通配，如 `src/**/*.java`）；空字符串表示 code_dir 内全部。
        max_commits: 最多返回多少个提交，默认 20，上限 100。
    """
    repo, error = _open_repo(code_dir)
    if error:
        return error
    try:
        lower, upper = _git_time(since, upper=False), _git_time(until, upper=True)
    except ValueError as exc:
        return _err(f"{exc}。支持的写法如：{BOUND_EXAMPLES}（需带日期）或 `3 days ago`")
    limit = max(1, min(int(max_commits or DEFAULT_COMMITS), MAX_COMMITS))
    try:
        spec = _pathspec(repo, path)
    except ValueError as exc:
        return _err(str(exc))
    args = [
        "log", f"-n{limit + 1}", "--relative", "--numstat", "--no-ext-diff", "--no-textconv",
        f"--format={_RS}%H{_US}%h{_US}%ct{_US}%an{_US}%P{_US}%s",
    ]
    args += [f"--since={lower}"] if lower else []
    args += [f"--until={upper}"] if upper else []
    try:
        commits = _parse_log(_run(repo.base, *args, *spec))
    except _GitError as exc:
        if "does not have any commits" in str(exc):
            return _hint("仓库还没有任何提交。", "empty_repo")
        return _err(f"读取提交历史失败：{exc}")

    truncated = len(commits) > limit
    commits = commits[:limit]
    scope = f"{since or '最早'} → {until or '现在'}" if since or until else "最近"
    target = f" · {path}" if path else ""
    header = f"--- {repo.label}{target} · {scope} · {_head_line(repo)} · 时区 {_tz_label()} ---"
    if not commits:
        dirty = _dirty_note(repo, spec)
        return _hint(
            f"{header}\n该范围内没有提交。可以放宽 since / until 或去掉 path 再查。" + (f"\n{dirty}" if dirty else ""),
            "no_commits", commits=0,
        )

    out = [header]
    total_files = 0
    for commit in commits:
        mark = "（合并提交）" if commit.merge else ""
        out.append(f"{commit.short}  {commit.when}  {commit.author}  {redact_log(commit.subject)}{mark}")
        total_files += len(commit.files)
        for added, deleted, name in commit.files[:MAX_FILES_PER_COMMIT]:
            delta = "二进制" if added == "-" else f"+{added} -{deleted}"
            out.append(f"    {name}  {delta}")
        if len(commit.files) > MAX_FILES_PER_COMMIT:
            out.append(f"    … 另有 {len(commit.files) - MAX_FILES_PER_COMMIT} 个文件")
    if truncated:
        out.append(f"... 还有更早的提交未显示，可缩小时间范围或调大 max_commits（上限 {MAX_COMMITS}）。")
    dirty = _dirty_note(repo, spec)
    if dirty:
        out.append(dirty)
    return _ok("\n".join(out), commits=len(commits), truncated=truncated, files=total_files,
               newest=commits[0].when, oldest=commits[-1].when)


# ---------------------------------------------------------------------------
# show_commit
# ---------------------------------------------------------------------------


def show_commit(code_dir: str, commit: str, path: str = "", max_lines: int = 300) -> ToolOutput:
    """查看某个提交的说明与代码改动（git show），用来确认可疑提交具体改了什么。

    合并提交展示相对第一父提交的改动。diff 较长时用 path 只看关心的文件。

    Args:
        code_dir: 源码目录（需在 git 仓库内）。
        commit: 提交号（recent_changes / blame_lines 返回的短 SHA 即可），也可以是分支名或 tag。
        path: 只看该提交里某个文件或目录的改动（相对 code_dir，支持通配）；空字符串表示 code_dir 内全部。
        max_lines: diff 最多返回的行数，默认 300，上限 1000。
    """
    repo, error = _open_repo(code_dir)
    if error:
        return error
    try:
        spec = _pathspec(repo, path)
        sha = _check_ref(repo, commit)
    except ValueError as exc:
        return _err(str(exc))
    except LookupError as exc:
        return _hint(f"{exc}；可能是短 SHA 写错，或该提交不在本地（需要先 git fetch）。", "no_commit")

    try:
        info = _run(repo.base, "show", "-s", f"--format=%h{_US}%ct{_US}%an{_US}%P{_US}%B", sha)
    except _GitError as exc:
        return _err(f"读取提交失败：{exc}")
    short, epoch, author, parents, body = (info.split(_US, 4) + [""] * 5)[:5]
    base_args = ["show", "--format=", "--relative", "--no-ext-diff", "--no-textconv", "--stat=120", "--patch"]
    try:
        diff = _run(repo.base, *base_args, "--diff-merges=first-parent", sha, *spec)
    except _GitError:
        try:  # 旧版 git 不认识 --diff-merges
            diff = _run(repo.base, *base_args, "-m", "--first-parent", sha, *spec)
        except _GitError as exc:
            return _err(f"读取提交改动失败：{exc}")

    limit = max(20, min(int(max_lines or DEFAULT_DIFF_LINES), MAX_DIFF_LINES))
    lines = diff.rstrip("\n").splitlines()
    added = sum(1 for ln in lines if ln.startswith("+") and not ln.startswith("+++"))
    deleted = sum(1 for ln in lines if ln.startswith("-") and not ln.startswith("---"))
    files = sum(1 for ln in lines if ln.startswith("diff --git "))
    shown = [clip_line(ln, _DIFF_LINE_CHARS) for ln in lines[:limit]]

    merge = "（合并提交，显示相对第一父提交的改动）" if len(parents.split()) > 1 else ""
    header = [
        f"--- 提交 {short} · {_stamp(epoch)} · {author}{merge} ---",
        redact_log(body.strip()) or "（无提交说明）",
        "",
    ]
    if not lines:
        scope = f"在 {path} 下" if path else ""
        return _hint("\n".join(header) + f"该提交{scope}没有文本改动。", "empty_diff", files=0)
    tail = []
    if len(lines) > limit:
        tail.append(f"... diff 共 {len(lines):,} 行，已显示前 {limit} 行；可用 path 只看某个文件，或调大 max_lines。")
    text = "\n".join(header) + redact_code("\n".join(shown)) + ("\n" + "\n".join(tail) if tail else "")
    return _ok(text, commit=short, files=files, additions=added, deletions=deleted, truncated=len(lines) > limit)


# ---------------------------------------------------------------------------
# blame_lines
# ---------------------------------------------------------------------------


@dataclass
class _BlameLine:
    number: int
    sha: str
    text: str


def _parse_blame(text: str) -> tuple[list[_BlameLine], dict[str, dict[str, str]]]:
    lines: list[_BlameLine] = []
    info: dict[str, dict[str, str]] = {}
    current: tuple[str, int] | None = None
    for raw in text.splitlines():
        if raw.startswith("\t"):
            if current:
                lines.append(_BlameLine(current[1], current[0], raw[1:]))
            current = None
            continue
        parts = raw.split(" ")
        if len(parts) >= 3 and len(parts[0]) >= 40 and all(c in "0123456789abcdef" for c in parts[0]):
            current = (parts[0], int(parts[2]))
            info.setdefault(parts[0], {})
        elif current and " " in raw:
            key, value = raw.split(" ", 1)
            info[current[0]].setdefault(key, value)
    return lines, info


def blame_lines(code_dir: str, rel_path: str, start_line: int, end_line: int = 0) -> ToolOutput:
    """查看源码某几行最后一次是被哪个提交修改的（git blame），判断报错位置是不是最近改过。

    典型用法：日志堆栈指向 `app/order.py:88`，先 blame 这附近几行，看最后修改时间是否紧挨着
    问题开始时间；是的话再用 show_commit 看那次改动。

    Args:
        code_dir: 源码目录（需在 git 仓库内）。
        rel_path: 相对 code_dir 的文件路径（与 read_code_file 相同）。
        start_line: 起始行号（从 1 开始）。
        end_line: 结束行号（含），0 表示只看 start_line 这一行；一次最多 200 行。
    """
    repo, error = _open_repo(code_dir)
    if error:
        return error
    target = (repo.base / rel_path).resolve()
    if not target.is_relative_to(repo.base):
        return _err(f"非法路径（越界）: {rel_path}")
    if not target.is_file():
        return _err(f"文件不存在: {rel_path}")
    start = max(1, int(start_line))
    end = int(end_line or 0)
    end = start if end < start else min(end, start + MAX_BLAME_LINES - 1)
    rel = target.relative_to(repo.base).as_posix()
    try:
        text = _run(repo.base, "blame", "--porcelain", f"-L{start},{end}", "--", rel)
    except _GitError as exc:
        message = str(exc)
        if "no such path" in message or "no such ref" in message:
            return _hint(f"{rel_path} 没有被 git 跟踪（可能是新文件或被忽略），无法追溯。", "untracked")
        if "has only" in message:
            return _hint(f"第 {start} 行超出文件范围（{message}）。", "eof")
        return _err(f"git blame 失败：{message}")

    lines, info = _parse_blame(text)
    if not lines:
        return _hint(f"{rel_path} 第 {start}-{end} 行没有可追溯的内容。", "empty")

    # 先整段脱敏，防止跨行私钥被行边界或提交分组拆开；保留行号与提交归属。
    scrubbed = redact_code("\n".join(ln.text for ln in lines), preserve_lines=True).split("\n")
    for line, text in zip(lines, scrubbed, strict=True):
        line.text = text

    out = [f"--- {rel} 第 {start}-{lines[-1].number} 行的最近修改（时区 {_tz_label()}）---"]
    newest: tuple[int, str] | None = None
    group: list[_BlameLine] = []

    def flush() -> None:
        if not group:
            return
        meta = info.get(group[0].sha, {})
        span = f"L{group[0].number}" + (f"-{group[-1].number}" if len(group) > 1 else "")
        if set(group[0].sha) == {"0"}:
            out.append(f"{span}  （未提交的本地修改）")
        else:
            when = _stamp(meta.get("committer-time", "0"))
            summary = redact_log(meta.get("summary", ""))
            out.append(f"{span}  {group[0].sha[:8]}  {when}  {meta.get('author', '?')}  {summary}")
        width = len(str(lines[-1].number))
        out.extend(f"  {ln.number:>{width}} | {clip_line(ln.text, 200)}" for ln in group)

    for line in lines:
        if group and line.sha != group[-1].sha:
            flush()
            group = []
        group.append(line)
        if set(line.sha) != {"0"}:
            epoch = int(info.get(line.sha, {}).get("committer-time", "0"))
            if newest is None or epoch > newest[0]:
                newest = (epoch, line.sha[:8])
    flush()

    commits = {ln.sha for ln in lines if set(ln.sha) != {"0"}}
    uncommitted = any(set(ln.sha) == {"0"} for ln in lines)
    summary = f"涉及 {len(commits)} 个提交"
    if newest:
        summary += f"，最近一次修改 {_stamp(newest[0])}（{newest[1]}）"
    if uncommitted:
        summary += "；含未提交的本地修改"
    out.append(summary)
    return _ok("\n".join(out), start=start, end=lines[-1].number, commits=len(commits),
               newest=_stamp(newest[0]) if newest else "", uncommitted=uncommitted)


GIT_TOOLS = [recent_changes, show_commit, blame_lines]
