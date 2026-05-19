"""MiniMax 联网搜索 API。"""

from __future__ import annotations

from typing import Any

from ..client import MiniMaxClient
from ..endpoints import search_endpoint


class SearchAPI:
    """MiniMax 联网搜索接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def query(self, query: str) -> dict[str, Any]:
        """执行联网搜索查询。"""
        return await self._client.request_json(
            "POST",
            search_endpoint(self._client.base_url),
            body={"q": query},
        )
