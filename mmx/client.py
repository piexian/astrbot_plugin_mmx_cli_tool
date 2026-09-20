"""MiniMax API 客户端 — 共享 httpx.AsyncClient，统一处理鉴权和错误。

支持单 Key 和多 Key 池模式。多 Key 模式下通过 key_getter(model) 按模型额度选 Key。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import Callable, Awaitable
from typing import Any

import httpx

from .errors import ErrorCategory, MiniMaxError, classify_error
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
        self._base_url = (base_url or REGIONS.get(region, REGIONS["cn"])).rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            proxy=proxy or None,
        )
        self._streams: set[httpx.Response] = set()

    @property
    def base_url(self) -> str:
        """当前 API 基础地址。"""
        return self._base_url

    @property
    def api_key(self) -> str | None:
        """客户端固定 API Key（key_getter 模式下为 None）。"""
        return self._api_key

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
        data: Any = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        stream: bool = False,
        auth_style: str = "bearer",
        model: str = "",
        api_key_override: str | None = None,
    ) -> httpx.Response:
        """发送 HTTP 请求，返回原始 Response。"""
        hdrs: dict[str, str] = {"User-Agent": "astrbot-plugin-mmx/0.5.0"}
        if headers:
            hdrs.update(headers)

        api_key = api_key_override or await self._resolve(model)

        if auth_style == "x-api-key":
            hdrs["x-api-key"] = api_key
        else:
            hdrs["Authorization"] = f"Bearer {api_key}"

        if body is not None and data is None and files is None and isinstance(
            body, (dict, list)
        ):
            hdrs.setdefault("Content-Type", "application/json")

        url = (
            path
            if path.startswith(("http://", "https://"))
            else f"{self._base_url}{path}"
        )
        request = self._client.build_request(
            method=method,
            url=url,
            headers=hdrs,
            json=(
                body
                if body is not None
                and data is None
                and files is None
                and not isinstance(body, bytes)
                else None
            ),
            content=body if isinstance(body, bytes) else None,
            data=data,
            files=files,
        )
        res = await self._client.send(request, stream=stream)
        if not res.is_success:
            try:
                await res.aread()
                raise classify_error(res.status_code, res, path)
            finally:
                await res.aclose()
        if stream:
            self._streams.add(res)
        return res

    @asynccontextmanager
    async def stream(self, method: str, path: str, **kwargs):
        """保持响应流开放，并在完成、取消或异常时释放连接。"""
        response = await self.request(method, path, stream=True, **kwargs)
        try:
            yield response
        finally:
            self._streams.discard(response)
            await response.aclose()

    async def request_json(
        self,
        method: str,
        path: str,
        body: Any = None,
        data: Any = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        auth_style: str = "bearer",
        model: str = "",
        api_key_override: str | None = None,
    ) -> Any:
        """发送请求并解析 JSON 响应体。"""
        res = await self.request(
            method=method,
            path=path,
            body=body,
            data=data,
            files=files,
            headers=headers,
            auth_style=auth_style,
            model=model,
            api_key_override=api_key_override,
        )
        return self.decode_json_response(res, path)

    @staticmethod
    def decode_json_response(res: httpx.Response, path: str) -> dict[str, Any]:
        """解析 JSON 并检查 HTTP 200 内的业务错误。"""
        try:
            data = res.json()
        except ValueError as exc:
            raise MiniMaxError(ErrorCategory.GENERAL, "API 返回了无效 JSON") from exc
        if not isinstance(data, dict):
            raise MiniMaxError(ErrorCategory.GENERAL, "API 返回了非对象 JSON")
        base_resp = data.get("base_resp")
        if (isinstance(base_resp, dict) and base_resp.get("status_code", 0) != 0) or data.get("error"):
            raise classify_error(res.status_code, path=path, api_body=data)
        return data

    async def close(self) -> None:
        """关闭底层 httpx 客户端连接。"""
        try:
            for response in tuple(self._streams):
                try:
                    await response.aclose()
                finally:
                    self._streams.discard(response)
        finally:
            await self._client.aclose()
