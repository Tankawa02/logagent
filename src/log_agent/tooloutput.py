"""工具返回值：发给模型的正文 + 只给展示层的结构化状态。单独成模块，便于各类工具共用。"""

from __future__ import annotations

from typing import Any, Literal

Status = Literal["ok", "hint", "error"]


class ToolOutput(str):
    """工具结果：字符串内容 + 结构化状态。继承 str，直接调用工具函数时用法与普通字符串一致。"""

    status: Status
    meta: dict[str, Any]

    def __new__(cls, text: str, status: Status = "ok", **meta: Any) -> ToolOutput:
        obj = super().__new__(cls, text)
        obj.status = status
        obj.meta = meta
        return obj

    @property
    def artifact(self) -> dict[str, Any]:
        return {"status": self.status, **self.meta}


def _ok(text: str, **meta: Any) -> ToolOutput:
    return ToolOutput(text, "ok", **meta)


def _hint(message: str, kind: str, **meta: Any) -> ToolOutput:
    return ToolOutput(f"[提示] {message}", "hint", kind=kind, message=message, **meta)


def _err(message: str) -> ToolOutput:
    return ToolOutput(f"[错误] {message}", "error", message=message)
