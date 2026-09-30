from __future__ import annotations

import pytest

from log_agent import redact
from log_agent.redact import redact_code, redact_log

# 11010519491231002X 是公开的合法校验位示例身份证号
VALID_ID = "11010519491231002X"


@pytest.mark.parametrize(
    ("raw", "must_not_contain", "must_contain"),
    [
        ("password=hunter2 ok", "hunter2", "password=[已脱敏]"),
        ('{"api_key": "abc123xyz"}', "abc123xyz", '"api_key": "[已脱敏]'),
        ("Authorization: Bearer abcdefghijklmnop123", "abcdefghijklmnop123", "Bearer [已脱敏]"),
        ("key sk-proj-ABCDEFGHIJKLMNOPQRSTUV", "ABCDEFGHIJKLMNOP", "sk-[已脱敏]"),
        ("aws AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP", "[AccessKey已脱敏]"),
        ("jwt eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeK", "SflKxwRJSMeK", "[JWT已脱敏]"),
        ("user zhang.san@example.com", "zhang.san@", "z***@example.com"),
        ("phone 13812345678 end", "13812345678", "138****5678"),
        (f"id {VALID_ID} end", VALID_ID, "110105********02X"),
    ],
)
def test_masks_sensitive_values(raw: str, must_not_contain: str, must_contain: str) -> None:
    out = redact_log(raw)
    assert must_not_contain not in out
    assert must_contain in out


def test_ip_mapping_is_stable_and_distinguishable() -> None:
    out = redact_log("a=10.12.3.4 b=10.12.3.4 c=10.12.9.9")
    tokens = [part.split("=")[1] for part in out.split()]
    assert tokens[0] == tokens[1]
    assert tokens[0] != tokens[2]
    assert tokens[0].startswith("10.12.x.x#")


@pytest.mark.parametrize(
    "raw",
    [
        "order 110105194912310021 created",  # 18 位但校验位不对：订单号，不能被当成身份证
        "trace 1717900000123 ts",  # 13 位毫秒时间戳，不是手机号
        "version 1.2.300.4",  # 不是合法 IP
        "bind 127.0.0.1 and 0.0.0.0",
        "no secrets here",
    ],
)
def test_leaves_diagnostic_values_alone(raw: str) -> None:
    assert redact_log(raw) == raw


def test_code_redaction_keeps_code_intact() -> None:
    code = 'password = os.environ["DB_PASSWORD"]\ntoken = "sk-live-ABCDEFGHIJKLMNOPQRST"'
    out = redact_code(code)
    assert 'password = os.environ["DB_PASSWORD"]' in out
    assert "ABCDEFGHIJKLMNOPQRST" not in out


def test_can_be_disabled() -> None:
    redact.set_enabled(False)
    assert redact_log("password=hunter2") == "password=hunter2"


def test_code_redaction_preserves_lines_for_multiple_private_keys() -> None:
    block = "-----BEGIN PRIVATE KEY-----\nfirst_payload\n\nlast_payload\n-----END PRIVATE KEY-----"
    code = f'before\nkey = "{block}"\nbetween\n{block}\nafter\n'
    out = redact_code(code, preserve_lines=True)
    assert out.split("\n") == [
        "before", 'key = "[私钥已脱敏]', "[私钥已脱敏]", "[私钥已脱敏]", "[私钥已脱敏]", '[私钥已脱敏]"',
        "between", *(["[私钥已脱敏]"] * 5), "after", "",
    ]
    assert redact_code(block) == "[私钥已脱敏]"
    redact.set_enabled(False)
    assert redact_code(code, preserve_lines=True) == code


def test_long_private_key_masks_every_line_including_short_tail() -> None:
    # 超过旧版 400 行前瞻的密钥，最后一行 base64 很短，也必须整块遮住且行数不变
    body = ["QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo0123456789ab"] * 450
    lines = ["log start", "-----BEGIN RSA PRIVATE KEY-----", *body, "tAil", "-----END RSA PRIVATE KEY-----", "log end"]
    text = "\n".join(lines)
    for out in (redact_log(text), redact_code(text, preserve_lines=True)):
        rows = out.split("\n")
        assert len(rows) == len(lines)
        assert "tAil" not in out and "QUJD" not in out
        assert rows[0] == "log start" and rows[-1] == "log end"
        assert all(row == redact.KEY_MASK for row in rows[1:-1])


def test_truncated_private_key_masks_short_padded_tail() -> None:
    text = "-----BEGIN PRIVATE KEY-----\nQUJDREVGR0hJSktMTU5P\nZw==\nnext log line here"
    rows = redact_log(text).split("\n")
    assert rows[:3] == [redact.KEY_MASK] * 3
    assert rows[3] == "next log line here"
