"""MiniMax 额度查询 API。"""

from __future__ import annotations

from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import account_balance_endpoint, quota_endpoints

ACCOUNT_BALANCE_KIND = "account_balance"
ACCOUNT_KEY_PREFIX = "sk-api-"


class QuotaAPI:
    """MiniMax 额度查询接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def info(self, api_key: str | None = None) -> dict[str, Any]:
        """查询 API Key 的额度使用情况。

        对齐 mmx-cli 1.0.25：sk-api- 开头的 Key 走账户余额端点，
        返回带 kind="account_balance" 标记的余额数据；其余 Key 走 Token Plan。
        """
        resolved_key = api_key or self._client.api_key
        if resolved_key and resolved_key.startswith(ACCOUNT_KEY_PREFIX):
            result = await self._client.request_json(
                "GET",
                account_balance_endpoint(self._client.base_url),
                api_key_override=api_key,
            )
            if isinstance(result, dict):
                result.setdefault("kind", ACCOUNT_BALANCE_KIND)
            return result

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
