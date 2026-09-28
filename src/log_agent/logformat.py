"""共享的日志字段识别：结构化字段优先，普通文本保持兼容。"""

from __future__ import annotations

import json
import re
from typing import Any

LEVEL_RE = re.compile(r"\b(FATAL|CRITICAL|SEVERE|ERROR|ERR|WARNING|WARN|INFO|DEBUG|TRACE)\b", re.IGNORECASE)
LEVEL_ALIAS = {"CRITICAL": "FATAL", "SEVERE": "FATAL", "ERR": "ERROR", "WARNING": "WARN"}
CODE_FIELDS = ("status", "status_code", "statusCode", "http_status", "code", "error_code", "errorCode")


def json_record(line: str) -> dict[str, Any] | None:
    # 防止超长单行解析引入额外的大量内存开销。
    if len(line) > 65536 or not line.lstrip().startswith("{"):
        return None
    try:
        value = json.loads(line)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def level_and_body(line: str) -> tuple[str, str] | None:
    record = json_record(line)
    if record is not None:
        for key in ("level", "severity", "levelname", "severityText"):
            if key not in record:
                continue
            level = str(record[key]).strip().upper()
            if not LEVEL_RE.fullmatch(level):
                return None
            message = record.get("message", record.get("msg", ""))
            if not isinstance(message, str):
                message = json.dumps(message, ensure_ascii=False, sort_keys=True)
            codes = [f"{key}={record[key]}" for key in CODE_FIELDS if isinstance(record.get(key), (str, int))]
            return LEVEL_ALIAS.get(level, level), " ".join([*codes, message]).strip()
        # JSON 的正文中可能提到 ERROR；没有级别字段时不猜测其日志级别。
        return None
    match = LEVEL_RE.search(line[:200])
    if match:
        level = match.group(1).upper()
        return LEVEL_ALIAS.get(level, level), line[match.end():].lstrip(" ]:|-\t")
    return None
