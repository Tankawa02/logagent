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

_enabled = True


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def is_enabled() -> bool:
    return _enabled


def _tag(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:4]


# ---- 高置信度密钥（日志和源码都处理） -------------------------------------

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"), "[私钥已脱敏]"),
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


def _apply_secrets(text: str) -> str:
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


def redact_code(text: str) -> str:
    if not _enabled or not text:
        return text
    return _apply_secrets(text)
