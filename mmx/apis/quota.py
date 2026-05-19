"""MiniMax 额度查询 API。"""

from __future__ import annotations

from typing import Any

from ..client import MiniMaxClient
from ..endpoints import quota_endpoint


class QuotaAPI:
    """MiniMax 额度查询接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def info(self) -> dict[str, Any]:
        """查询当前 API Key 的额度使用情况。"""
        return await self._client.request_json(
            "GET",
            quota_endpoint(self._client.base_url),
        )
