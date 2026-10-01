"""敏感信息脱敏：工具输出在交给大模型之前先过一遍。

设计原则：
- 宁可漏打码也不要误伤排障线索。订单号、trace id 这类长数字不能被当成身份证抹掉，
  所以身份证走校验位校验，手机号要求两侧不是数字。
- 同一个 IP 始终映射成同一个代号，模型仍能分辨"是不是同一台机器"。
- 日志走全量规则；源码只打高置信度的密钥（sk- / AKIA / JWT / Bearer），
  避免把 `password = os.environ[...]` 这类代码本身改掉。
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
from bisect import bisect_right

_enabled = True


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


def _tag(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:4]


# ---- 高置信度密钥（日志和源码都处理） -------------------------------------

# 私钥块按行处理（见 mask_private_keys），这里只放单行规则
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[JWT已脱敏]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "sk-[已脱敏]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "[AccessKey已脱敏]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), r"\1 [已脱敏]"),
]

# ---- 日志专用规则 -----------------------------------------------------------

# key=value / "key": "value" 形式的凭据。只替换值，保留键名，模型仍知道"这里有个 token"
_KV_SECRET = re.compile(
    r"""(?ix)
    (\b(?:password|passwd|pwd|secret|token|access[_-]?token|refresh[_-]?token|
        api[_-]?key|apikey|access[_-]?key|secret[_-]?key|private[_-]?key|credential)s?\b)
    (\s*["']?\s*[:=]\s*["']?)
    ([^\s"',;&}]{3,})
    """
)

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
_PHONE_CN = re.compile(r"(?<![\d.])(1[3-9]\d)\d{4}(\d{4})(?![\d.])")
_ID_CARD_CN = re.compile(r"(?<!\d)(\d{6})(\d{8})(\d{3})([\dXx])(?!\d)")
_IPV4 = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")

_ID_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ID_CHECK = "10X98765432"


def _valid_id_card(number: str) -> bool:
    total = sum(int(d) * w for d, w in zip(number[:17], _ID_WEIGHTS, strict=True))
    return _ID_CHECK[total % 11] == number[17].upper()


def _mask_id(match: re.Match[str]) -> str:
    number = match.group(0)
    if not _valid_id_card(number):
        return number
    return f"{match.group(1)}********{match.group(3)[-2:]}{match.group(4)}"


def _mask_ip(match: re.Match[str]) -> str:
    raw = match.group(1)
    try:
        ip = ipaddress.IPv4Address(raw)
    except ValueError:
        return raw  # 版本号之类的 1.2.300.4，不是 IP
    if ip.is_loopback or ip.is_unspecified:
        return raw
    head = ".".join(raw.split(".")[:2])
    return f"{head}.x.x#{_tag(raw)}"


# ---- 私钥块（跨多行）-------------------------------------------------------

KEY_MASK = "[私钥已脱敏]"
_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_KEY_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
# 工具输出 / diff 的行前缀：`12: `、`12- `、`app.log:12  `、`  12 | `、diff 的 `+` `-` 空格
_LINE_PREFIX = re.compile(r"^(?:\s*(?:[^\s:|]+:)?\d+(?:\s*\|\s?|[:\-]\s?|\s{2,})|[+\- ](?=\S))?")
# 私钥正文与 PEM 头部字段。遇到其它内容视为块已结束（截断、只含半个块的 diff hunk）
_KEY_BODY = re.compile(
    r"^\s*(?:[A-Za-z0-9+/=]{8,}|[A-Za-z0-9+/]+={1,2}|Proc-Type:.*|DEK-Info:.*|)\s*[\"',]?\s*$"
)


def _split_prefix(line: str) -> tuple[str, str]:
    match = _LINE_PREFIX.match(line)
    end = match.end() if match else 0
    return line[:end], line[end:]




def mask_private_keys(text: str) -> str:
    """逐行遮盖私钥块，行数保持不变，行号前缀原样保留。

    - 有 BEGIN 且后面能找到 END：两者之间每一行（含空行、BEGIN / END 行本身）都换成占位符
    - 缺 END（被截断或 diff hunk 只含前半段）：正文行一直遮到第一条不像私钥正文的行
    - 缺 BEGIN（只含后半段）：遇到 END 时回头把紧挨着的正文行一并遮住
    """
    if "PRIVATE KEY-----" not in text:
        return text
    lines = text.split("\n")
    end_rows = [n for n, row in enumerate(lines) if _KEY_END.search(row)]
    begin_rows = [n for n, row in enumerate(lines) if _KEY_BEGIN.search(row)]
    i = 0
    while i < len(lines):
        line = lines[i]
        # 起止标记在整行里找：`-----BEGIN` 本身以 `-` 开头，不能先当 diff 前缀剥掉
        begin, end = _KEY_BEGIN.search(line), _KEY_END.search(line)
        if begin:
            if end and end.start() > begin.start():
                lines[i] = line[:begin.start()] + KEY_MASK + line[end.end():]
                i += 1
                continue
            lines[i] = line[:begin.start()] + KEY_MASK
            # 完整的块不限长度地找 END（超长密钥的最后一行可能很短）；中间又出现 BEGIN 说明这块被截断了
            k = bisect_right(end_rows, i)
            close = end_rows[k] if k < len(end_rows) else None
            if close is not None and bisect_right(begin_rows, i) < len(begin_rows) \
                    and begin_rows[bisect_right(begin_rows, i)] < close:
                close = None
            j = i + 1
            if close is not None:
                for j in range(i + 1, close):
                    lines[j] = _split_prefix(lines[j])[0] + KEY_MASK
                marker = _KEY_END.search(lines[close])
                lines[close] = _split_prefix(lines[close][:marker.start()])[0] + KEY_MASK + lines[close][marker.end():]
                i = close + 1
                continue
            while j < len(lines):
                prefix, body = _split_prefix(lines[j])
                if not _KEY_BODY.match(body):
                    break
                lines[j] = prefix + KEY_MASK
                j += 1
            i = j
            continue
        if end:
            j = i - 1
            while j >= 0:
                p, b = _split_prefix(lines[j])
                if not b.strip() or not _KEY_BODY.match(b) or KEY_MASK in b:
                    break
                lines[j] = p + KEY_MASK
                j -= 1
            lines[i] = line[:end.start()] + KEY_MASK + line[end.end():]
        i += 1
    return "\n".join(lines)


# 旧行为：完整的私钥块整体换成一个占位符（不关心行号的场景，如存进记忆的一行文本）
_PEM_BLOCK = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----")


def _apply_secrets(text: str, *, preserve_lines: bool = True) -> str:
    if not preserve_lines:
        text = _PEM_BLOCK.sub(KEY_MASK, text)
    text = mask_private_keys(text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_log(text: str) -> str:
    if not _enabled or not text:
        return text
    text = _apply_secrets(text)
    text = _KV_SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}[已脱敏]", text)
    text = _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)
    text = _ID_CARD_CN.sub(_mask_id, text)
    text = _PHONE_CN.sub(r"\1****\2", text)
    text = _IPV4.sub(_mask_ip, text)
    return text


def redact_code(text: str, *, preserve_lines: bool = False) -> str:
    """源码脱敏。preserve_lines=True 时私钥逐行遮盖、保持行数（带行号的输出必须用它）。"""
    if not _enabled or not text:
        return text
    return _apply_secrets(text, preserve_lines=preserve_lines)


def redact_code_lines(lines: list[str]) -> list[str]:
    """按整份文件脱敏源码，逐行返回。

    只展示文件中间一段时，单看这段可能既没有 BEGIN 也没有 END，私钥正文会漏掉；
    所以先在整份文件上定位私钥块，再按行号取需要的部分。
    """
    if not _enabled or not lines:
        return list(lines)
    masked = _apply_secrets("\n".join(lines)).split("\n")
    return masked if len(masked) == len(lines) else [_apply_secrets(line) for line in lines]
