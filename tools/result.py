"""Helpers for AstrBot FunctionTool return values."""

from __future__ import annotations

from typing import cast

from astrbot.core.agent.tool import ToolExecResult


def tool_result(value: str) -> ToolExecResult:
    """Return a plain tool result while preserving AstrBot's type annotation."""
    return cast(ToolExecResult, value)
