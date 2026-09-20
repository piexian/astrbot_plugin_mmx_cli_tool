"""SSE 流式解析器。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx


async def parse_sse(
    response: httpx.Response, *, strict: bool = False
) -> AsyncGenerator[dict[str, Any], None]:
    """解析多行 data、注释和 EOF 残留事件，可严格拒绝损坏事件。"""
    data_lines: list[str] = []
    event_size = 0

    def decode(data: str) -> dict[str, Any] | None:
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("SSE 事件必须是 JSON 对象")
            return value
        except ValueError:
            if strict:
                raise
            return None

    async for line in response.aiter_lines():
        if not line:
            if data_lines:
                data = "\n".join(data_lines)
                if data == "[DONE]":
                    return
                value = decode(data)
                if value is not None:
                    yield value
                data_lines = []
                event_size = 0
        elif line.startswith("data:"):
            part = line[5:]
            part = part[1:] if part.startswith(" ") else part
            event_size += len(part)
            if event_size > 1024 * 1024:
                raise ValueError("SSE 单个事件超过大小限制")
            data_lines.append(part)
    if data_lines:
        data = "\n".join(data_lines)
        if data != "[DONE]":
            value = decode(data)
            if value is not None:
                yield value
