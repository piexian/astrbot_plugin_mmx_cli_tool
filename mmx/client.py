"""MiniMax API 客户端 — 共享 httpx.AsyncClient，统一处理鉴权和错误。

支持单 Key 和多 Key 池模式。多 Key 模式通过 key_getter(model) 按模型额度选 Key+区域。
"""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from typing import Any

import httpx

from .errors import classify_error
from .endpoints import REGIONS


class MiniMaxClient:
    """MiniMax API 共享异步 HTTP 客户端。"""

    def __init__(
        self,
        api_key: str | None = None,
        key_getter: Callable[[str], Awaitable[tuple[str, int, str]]] | None = None,
        base_url: str | None = None,
        region: str = "cn",
        timeout: float = 300,
        proxy: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._key_getter = key_getter
        self._default_base_url = base_url or REGIONS.get(region, REGIONS["cn"])
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            proxy=proxy or None,
        )

    @property
    def base_url(self) -> str:
        return self._default_base_url

    async def _resolve(self, model: str = "") -> tuple[str, str]:
        """获取本次请求的 (api_key, base_url)。"""
        if self._key_getter is not None:
            key, _, key_region = await self._key_getter(model)
            return key, REGIONS.get(key_region, self._default_base_url)
        if self._api_key is not None:
            return self._api_key, self._default_base_url
        raise RuntimeError("未配置 API Key")

    async def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,  # noqa: ARG002
        auth_style: str = "bearer",
        model: str = "",
    ) -> httpx.Response:
        hdrs: dict[str, str] = {"User-Agent": "astrbot-plugin-mmx/0.1.0"}
        if headers:
            hdrs.update(headers)

        api_key, base_url = await self._resolve(model)

        if auth_style == "x-api-key":
            hdrs["x-api-key"] = api_key
        else:
            hdrs["Authorization"] = f"Bearer {api_key}"

        if body is not None and isinstance(body, (dict, list)):
            hdrs.setdefault("Content-Type", "application/json")

        url = f"{base_url}{path}"
        res = await self._client.request(
            method=method,
            url=url,
            headers=hdrs,
            json=body if body is not None and not isinstance(body, bytes) else None,
            content=body if isinstance(body, bytes) else None,
        )

        if not res.is_success:
            raise classify_error(res.status_code, res, path)

        return res

    async def request_json(
        self,
        method: str,
        path: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        auth_style: str = "bearer",
        model: str = "",
    ) -> Any:
        res = await self.request(
            method=method, path=path, body=body, headers=headers,
            auth_style=auth_style, model=model,
        )
        data: dict[str, Any] = res.json()
        base_resp = data.get("base_resp")
        if base_resp and base_resp.get("status_code", 0) != 0:
            raise classify_error(res.status_code, None, path, api_body=data)
        return data

    async def close(self) -> None:
        await self._client.aclose()
