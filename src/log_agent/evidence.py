"""证据回查：把结构化报告里每条证据的摘录与本地原文逐条核对。

模型在 `analysis.issues[].evidence[]` 里给出 `source:line_start-line_end` 与 `excerpt`，
Pydantic 只能校验字段格式，校验不了行号与摘录是否真实。这里在本地重新读取对应行，
按工具当时给模型看的样子（同样的脱敏）比对，得到每条证据的状态：

- verified   摘录出现在所引行内
- shifted    摘录真实存在，但落在所引行附近几行（行号偏移），会给出实际行号
- mismatch   所引行存在，但找不到摘录；或行号超出文件范围
- unresolved 来源对应不到本次提供的日志 / 源码，或读取失败，无法核对

比对规则刻意宽松，只为抓“编造的行号或原文”，不为挑措辞：
- 去掉工具输出里的行号前缀（`42: `、`42- `、`app.log:42  `、`  42 | `、`app/x.py:42: `）
- 按 `…` / `...` / 截断提示切成片段，逐段做子串匹配
- 空白折叠、全半角归一；仍不命中时允许极小的字符差异（相似度 ≥ 0.9）
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Literal

from .file_index import FileNameLookup
from .logfile import open_log, read_text_file
from .redact import redact_code_lines, redact_log

Status = Literal["verified", "shifted", "mismatch", "unresolved"]

# 行号偏移的容忍范围：模型常把多行堆栈的起止行记偏一两行
SHIFT_TOLERANCE = 5
# 单条证据最多回查的行数，防止 line_end 写成超大值时读穿大日志。
# 超出部分没查到时标为“无法核对”，不会把后面可能存在的真实摘录误判为不符。
MAX_CHECK_LINES = 500
# 单行参与模糊比对的最大长度，避免超长 JSON 行拖慢 difflib
_FUZZY_LINE_CHARS = 2000
_FUZZY_RATIO = 0.9
_MIN_PIECE = 3
_MAX_CODE_BYTES = 5 * 1024 * 1024

# 工具输出的行号前缀。日志行本身也可能以数字开头（如 `2026-06-09 ...`），
# 所以前缀只作为“备选写法”，与原样文本二者命中其一即可，绝不强行剥掉。
_PREFIX = re.compile(r"^\s*(?:[^\s:|]+:)?\d+(?:\s*\|\s?|[:\-](?:\s|$)|\s{2,})")
# 摘录里的省略号与工具的截断提示：两侧片段分别匹配
_SPLIT = re.compile(r"\s*(?:…*\s*[(（]本行共\s*\d+\s*字[，,]已截断[^)）]*[)）]|…+|\.{3,})\s*")
_SPACES = re.compile(r"\s+")
# 工具输出的头尾说明行（`--- app.log 第 1-20 行 ---`、`... 还有 N 行未显示`），不是原文，摘录里出现时忽略
_TOOL_CHROME = re.compile(r"^\s*(?:---\s.*\s---|\.\.\.\s*(?:还有|diff\s))")
_LINE_SUFFIX = re.compile(r":(\d+)(?:\s*[-–~]\s*\d+)?$")


@dataclass
class EvidenceResult:
    issue: int
    index: int
    source: str
    line_start: int
    line_end: int
    status: Status
    note: str = ""
    actual_start: int | None = None
    actual_end: int | None = None


# ---------------------------------------------------------------------------
# 来源解析
# ---------------------------------------------------------------------------


class SourceResolver:
    """把证据里的 source 对应回本次提供的日志文件或源码文件。"""

    def __init__(self, log_paths: Sequence[str | Path], code_dirs: Sequence[str | Path]) -> None:
        self._logs: dict[str, Path] = {}
        names: dict[str, Path | None] = {}
        for raw in log_paths:
            path = Path(raw).expanduser()
            for key in {str(raw), path.as_posix(), str(path), _abs(path)}:
                self._logs[key] = path
            # 同名日志来自不同目录时无法判断指哪一份
            names[path.name] = None if path.name in names and names[path.name] != path else path
        self._names = {k: v for k, v in names.items() if v is not None}
        self._roots = [Path(d).expanduser().resolve() for d in code_dirs]
        # 跨请求复用的文件名缓存：只记查过的文件名，靠目录 mtime 发现增删的文件
        self._file_names = FileNameLookup()

    def resolve(self, source: str) -> tuple[Literal["log", "code"], Path] | None:
        ref = source.strip().strip("`\"'").strip()
        if not ref:
            return None
        # 模型偶尔把行号也写进 source：`app.log:42`
        ref = _LINE_SUFFIX.sub("", ref) if not Path(ref).exists() else ref
        path = Path(ref).expanduser()
        for key in (ref, path.as_posix(), _abs(path)):
            if key in self._logs:
                return "log", self._logs[key]
        code = self._resolve_code(ref)
        if code is not None:
            return "code", code
        # 登记过的日志按文件名引用时优先认日志，不能被源码目录里碰巧同名的文件抢走
        if "/" not in ref and "\\" not in ref and ref in self._names:
            return "log", self._names[ref]
        code = self._resolve_code_suffix(ref)
        if code is not None:
            return "code", code
        return None

    def _resolve_code(self, ref: str) -> Path | None:
        parts = Path(ref.replace("\\", "/")).parts
        candidates: list[tuple[Path, Path]] = []
        absolute = Path(ref).expanduser()
        for root in self._roots:
            if absolute.is_absolute():
                candidates.append((root, absolute))
            else:
                candidates.append((root, root.joinpath(*parts)))
                candidates.append((root, Path.cwd() / ref))
                # 模型有时会带上仓库目录名本身，例如 `payment-svc/app/order.py`
                if len(parts) > 1 and parts[0] == root.name:
                    candidates.append((root, root.joinpath(*parts[1:])))
        for root, target in candidates:
            try:
                resolved = target.resolve()
            except OSError:
                continue
            if resolved.is_relative_to(root) and resolved.is_file():
                return resolved
        return None

    def _resolve_code_suffix(self, ref: str) -> Path | None:
        """模型常只写文件名或半截路径（`CheckPasswordValidityHandler.java`、`validity/Foo.java`）：
        在登记的源码目录里按路径后缀找，唯一命中才算，同名文件不止一个时不猜。"""
        parts = Path(ref.replace("\\", "/")).parts
        if not parts or Path(ref).expanduser().is_absolute() or any(p in ("..", ".") for p in parts):
            return None
        matches: set[Path] = set()
        for root in self._roots:
            for path in self._file_names.find(root, parts[-1]):
                if path.parts[-len(parts):] != parts:
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved.is_relative_to(root) and resolved.is_file():
                    matches.add(resolved)
                    if len(matches) > 1:
                        return None
        return next(iter(matches)) if matches else None


def _abs(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


# ---------------------------------------------------------------------------
# 文本归一与匹配
# ---------------------------------------------------------------------------


def _norm(text: str) -> str:
    return _SPACES.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def _requirements(excerpt: str) -> list[list[list[str]]]:
    """摘录的每一行 -> 若干备选写法 -> 每种写法切出的片段。"""
    reqs: list[list[list[str]]] = []
    for line in excerpt.splitlines():
        if _TOOL_CHROME.match(line):
            continue
        variants: list[list[str]] = []
        forms = [line]
        stripped = _PREFIX.sub("", line, count=1)
        if stripped != line:
            forms.append(stripped)
        for form in forms:
            pieces = [p for p in (_norm(s) for s in _SPLIT.split(form)) if len(p) >= _MIN_PIECE]
            if pieces and pieces not in variants:
                variants.append(pieces)
        if variants:
            reqs.append(variants)
    return reqs


_DIGITS = re.compile(r"\d+")


def _fuzzy_contains(piece: str, line: str) -> bool:
    if len(piece) < 12:  # 短片段不做模糊匹配，避免误判为命中
        return False
    # 数字（id、行号、状态码、耗时）必须原样出现：编造的引用往往就是改了数字，不能被“相似度够高”放过
    # 必须是完整的数字 token：子串判断会让 order=1009 匹配上原文的 order=10090
    numbers = set(_DIGITS.findall(line))
    if any(number not in numbers for number in _DIGITS.findall(piece)):
        return False
    line = line[:_FUZZY_LINE_CHARS]
    matcher = SequenceMatcher(None, piece, line, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return matched / len(piece) >= _FUZZY_RATIO


def _contains(piece: str, line: str) -> bool:
    """子串包含，但片段首尾的数字不能是原文更长数字的一部分（order=100 不算包含在 order=1001 里）。"""
    start = line.find(piece)
    if start < 0:
        return False
    head_digit, tail_digit = piece[0].isdigit(), piece[-1].isdigit()
    if not head_digit and not tail_digit:
        return True
    while start >= 0:
        end = start + len(piece)
        if not (head_digit and start > 0 and line[start - 1].isdigit()) and \
                not (tail_digit and end < len(line) and line[end].isdigit()):
            return True
        start = line.find(piece, start + 1)
    return False


def _line_satisfies(variants: list[list[str]], line: str, fuzzy: bool) -> bool:
    for pieces in variants:
        if all(_contains(p, line) for p in pieces):
            return True
        if fuzzy and all(_contains(p, line) or _fuzzy_contains(p, line) for p in pieces):
            return True
    return False


def _locate(reqs: list[list[list[str]]], lines: dict[int, str], lo: int, hi: int) -> list[int] | None:
    """每条摘录行在 [lo, hi] 里找一个满足的原文行，返回命中的行号；任意一条找不到返回 None。"""
    normalized = {n: _norm(t) for n, t in lines.items() if lo <= n <= hi}
    hits: list[int] = []
    for variants in reqs:
        found = None
        for fuzzy in (False, True):
            found = next((n for n, text in normalized.items() if _line_satisfies(variants, text, fuzzy)), None)
            if found is not None:
                break
        if found is None:
            return None
        hits.append(found)
    return hits


# ---------------------------------------------------------------------------
# 读取原文（与工具给模型看的内容保持一致：同样脱敏）
# ---------------------------------------------------------------------------


def _read_log_lines(path: Path, lo: int, hi: int) -> tuple[dict[int, str], int | None]:
    log = open_log(path)
    raw: dict[int, str] = {}
    with closing(log.iter_lines(lo)) as it:
        for lineno, text in it:
            if lineno > hi:
                break
            raw[lineno] = text
    total = log.total_lines if len(raw) < hi - lo + 1 else None
    return _redact_lines(raw, redact_log), total


def _read_code_lines(path: Path, lo: int, hi: int) -> tuple[dict[int, str], int | None]:
    if path.stat().st_size > _MAX_CODE_BYTES:
        raise OSError("文件过大，跳过核对")
    # 与 read_code_file 一致：整份文件脱敏后再取区间
    all_lines = redact_code_lines(read_text_file(path).splitlines())
    return {n: all_lines[n - 1] for n in range(lo, min(hi, len(all_lines)) + 1)}, len(all_lines)


def _redact_lines(raw: dict[int, str], redact) -> dict[int, str]:
    if not raw:
        return raw
    numbers = sorted(raw)
    joined = redact("\n".join(raw[n] for n in numbers)).split("\n")
    if len(joined) == len(numbers):  # 跨行规则（私钥块）会合并行，此时退回逐行脱敏
        return dict(zip(numbers, joined, strict=True))
    return {n: redact(raw[n]) for n in numbers}


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def check_one(resolver: SourceResolver, evidence: dict[str, Any], issue: int, index: int) -> EvidenceResult:
    start, end = int(evidence["line_start"]), int(evidence["line_end"])
    result = EvidenceResult(issue, index, evidence["source"], start, end, "unresolved")
    target = resolver.resolve(evidence["source"])
    if target is None:
        result.note = "来源对应不到本次提供的日志或源码"
        return result
    kind, path = target
    reqs = _requirements(evidence["excerpt"])
    if not reqs:
        result.status, result.note = "mismatch", "摘录里没有可核对的原文"
        return result

    checked_end = min(end, start + MAX_CHECK_LINES - 1)
    lo, hi = max(1, start - SHIFT_TOLERANCE), checked_end + SHIFT_TOLERANCE
    try:
        lines, total = (_read_log_lines if kind == "log" else _read_code_lines)(path, lo, hi)
    except Exception as exc:  # noqa: BLE001 — 损坏的 gzip 会抛 EOFError / zlib.error；核对失败不能打断本轮结果
        result.note = redact_log(f"读取失败：{type(exc).__name__}: {exc}")
        return result

    if start not in lines:
        result.status = "mismatch"
        result.note = f"行号超出文件范围（共 {total:,} 行）" if total is not None else "行号超出文件范围"
        return result

    # 容差窗口也可能落在原引用范围内，此处命中仍是 verified，而非行号偏移。
    if _locate(reqs, lines, start, min(end, hi)) is not None:
        result.status = "verified"
        return result
    if end > hi and (total is None or total > hi):
        result.note = (
            f"达到单条证据 {MAX_CHECK_LINES} 行的核对上限，仅检查第 {lo}-{hi} 行（含偏移容差）；"
            f"未覆盖完整引用范围 {start}-{end}，无法判定摘录是否存在"
        )
        return result
    hits = _locate(reqs, lines, lo, hi)
    if hits is not None:
        result.status = "shifted"
        result.actual_start, result.actual_end = min(hits), max(hits)
        result.note = f"内容实际位于第 {result.actual_start}-{result.actual_end} 行"
        return result

    missing = next(
        (_norm(line) for line, variants in zip(_excerpt_lines(evidence["excerpt"]), reqs, strict=False)
         if _locate([variants], lines, lo, hi) is None),
        "",
    )
    result.status = "mismatch"
    result.note = f"所引行中未找到摘录：「{_clip(missing, 60)}」" if missing else "所引行与摘录不一致"
    return result


def _safe_check(resolver: SourceResolver, evidence: dict[str, Any], issue: int, index: int) -> EvidenceResult:
    try:
        return check_one(resolver, evidence, issue, index)
    except Exception as exc:  # noqa: BLE001
        return EvidenceResult(issue, index, str(evidence.get("source", "")), int(evidence.get("line_start", 0)),
                              int(evidence.get("line_end", 0)), "unresolved", f"核对出错：{type(exc).__name__}: {exc}")


def check_analysis(
    analysis: dict[str, Any] | None, log_paths: Sequence[str | Path], code_dirs: Sequence[str | Path],
) -> dict[str, Any] | None:
    """核对整份结构化报告的全部证据；没有证据时返回 None。"""
    if not analysis:
        return None
    resolver = SourceResolver(log_paths, code_dirs)
    items = [
        _safe_check(resolver, evidence, i, j)
        for i, issue in enumerate(analysis.get("issues") or [], start=1)
        for j, evidence in enumerate(issue.get("evidence") or [], start=1)
    ]
    if not items:
        return None
    counts = Counter(item.status for item in items)
    supported = counts["verified"] + counts["shifted"]
    if counts["mismatch"]:
        overall = "failed" if not supported else "mismatch"
    elif not supported:
        overall = "unverifiable"
    elif counts["unresolved"]:
        overall = "incomplete"
    else:
        overall = "verified"
    return {
        "status": overall,
        "total": len(items),
        "verified": counts["verified"],
        "shifted": counts["shifted"],
        "mismatch": counts["mismatch"],
        "unresolved": counts["unresolved"],
        "items": [asdict(item) for item in items],
    }


def item_for(check: dict[str, Any] | None, issue: int, index: int) -> dict[str, Any] | None:
    if not check:
        return None
    return next((it for it in check.get("items", []) if it["issue"] == issue and it["index"] == index), None)


_LABELS = {
    "verified": "✓ 已核对原文",
    "shifted": "△ 行号偏移",
    "mismatch": "✗ 与原文不符",
    "unresolved": "? 无法核对",
}


def item_label(item: dict[str, Any]) -> str:
    label = _LABELS.get(item["status"], item["status"])
    return f"{label}：{item['note']}" if item.get("note") else label


def summary_line(check: dict[str, Any]) -> str:
    """一句话概括核对结果，用于终端与 Markdown 元信息。"""
    if check.get("error"):
        return f"核对失败，无法判定：{check['error']}"
    total = check["total"]
    supported = check["verified"] + check["shifted"]
    parts = [f"{supported}/{total} 条与原文一致"]
    if check["shifted"]:
        parts.append(f"其中 {check['shifted']} 条行号偏移")
    if check["mismatch"]:
        parts.append(f"{check['mismatch']} 条与原文不符")
    if check["unresolved"]:
        parts.append(f"{check['unresolved']} 条无法核对")
    return "，".join(parts)


def _excerpt_lines(excerpt: str) -> list[str]:
    return [line for line in excerpt.splitlines() if _requirements(line)]


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
