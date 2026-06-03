"""MiniMax 额度查询 API。"""

from __future__ import annotations

from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import quota_endpoints


class QuotaAPI:
    """MiniMax 额度查询接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def info(self, api_key: str | None = None) -> dict[str, Any]:
        """查询 API Key 的额度使用情况。"""
        last_error: Exception | None = None
        endpoints = quota_endpoints(self._client.base_url)

        for endpoint in endpoints:
            try:
                return await self._client.request_json(
                    "GET",
                    endpoint,
                    api_key_override=api_key,
                )
            except Exception as e:
                last_error = e
                if not self._should_try_next_endpoint(e):
                    raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("未配置 MiniMax 额度查询端点")

    @staticmethod
    def _should_try_next_endpoint(error: Exception) -> bool:
        return bool(
            isinstance(error, httpx.HTTPError)
            or getattr(error, "http_status", None) in (404, 405)
            or getattr(error, "api_code", None) in (404, 405)
        )
