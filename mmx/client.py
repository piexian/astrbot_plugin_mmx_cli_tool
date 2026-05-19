"""MiniMax API 客户端 — 共享 httpx.AsyncClient，统一处理鉴权和错误。

支持单 Key 和多 Key 池模式。多 Key 模式下通过 key_getter(model) 按模型额度选 Key。
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
        key_getter: Callable[[str], Awaitable[tuple[str, int]]] | None = None,
        base_url: str | None = None,
        region: str = "cn",
        timeout: float = 300,
        proxy: str | None = None,
    ) -> None:
        self._api_key = api_key
        self._key_getter = key_getter
        self._base_url = base_url or REGIONS.get(region, REGIONS["cn"])
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            proxy=proxy or None,
        )

    @property
    def base_url(self) -> str:
        """当前 API 基础地址。"""
        return self._base_url

    async def _resolve(self, model: str = "") -> str:
        """获取本次请求的 API Key。"""
        if self._key_getter is not None:
            key, _ = await self._key_getter(model)
            return key
        if self._api_key is not None:
            return self._api_key
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
        """发送 HTTP 请求，返回原始 Response。"""
        hdrs: dict[str, str] = {"User-Agent": "astrbot-plugin-mmx/0.1.0"}
        if headers:
            hdrs.update(headers)

        api_key = await self._resolve(model)

        if auth_style == "x-api-key":
            hdrs["x-api-key"] = api_key
        else:
            hdrs["Authorization"] = f"Bearer {api_key}"

        if body is not None and isinstance(body, (dict, list)):
            hdrs.setdefault("Content-Type", "application/json")

        url = f"{self._base_url}{path}"
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
        """发送请求并解析 JSON 响应体。"""
        res = await self.request(
            method=method,
            path=path,
            body=body,
            headers=headers,
            auth_style=auth_style,
            model=model,
        )
        data: dict[str, Any] = res.json()
        base_resp = data.get("base_resp")
        if base_resp and base_resp.get("status_code", 0) != 0:
            raise classify_error(res.status_code, None, path, api_body=data)
        return data

    async def close(self) -> None:
        """关闭底层 httpx 客户端连接。"""
        await self._client.aclose()
