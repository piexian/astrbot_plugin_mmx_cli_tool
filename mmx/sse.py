"""SSE（Server-Sent Events）流式解析器。"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import Any

import httpx


async def parse_sse(response: httpx.Response) -> AsyncGenerator[dict[str, Any], None]:
    """解析 ``text/event-stream`` 响应，逐事件产出 JSON 对象。

    处理多行 ``data:``、``event:`` / ``id:`` 字段、以 ``:`` 开头的
    注释行、以及 ``[DONE]`` 终止标记。
    """
    data_lines: list[str] = []

    async for line in response.aiter_lines():
        # 去除行尾 \r，跳过注释行
        line = line.rstrip("\r")
        if not line:
            # 空行 → 派发已缓冲的事件
            if data_lines:
                data_str = "\n".join(data_lines)
                if data_str == "[DONE]":
                    return
                try:
                    yield json.loads(data_str)
                except json.JSONDecodeError:
                    pass
                data_lines = []
            continue

        if line.startswith(":"):
            continue

        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
        # event: 和 id: 字段在 MiniMax API 中被忽略
