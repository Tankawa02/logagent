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
