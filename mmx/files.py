"""MiniMax 文件 API — 上传、列表、删除、检索。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from .client import MiniMaxClient
from .endpoints import (
    file_upload_endpoint,
    file_list_endpoint,
    file_delete_endpoint,
    file_retrieve_endpoint,
)


class FileAPI:
    """MiniMax 文件管理接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def upload(self, file_path: str, purpose: str = "file-extract") -> dict[str, Any]:
        """上传文件到 MiniMax。"""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        async with httpx.AsyncClient() as cl:
            with open(p, "rb") as f:
                res = await cl.post(
                    file_upload_endpoint(self._client.base_url),
                    files={"file": (p.name, f, "application/octet-stream")},
                    data={"purpose": purpose},
                    headers={"Authorization": f"Bearer {self._client._api_key}"},
                )
            if not res.is_success:
                from .errors import classify_error

                raise classify_error(res.status_code, res, file_upload_endpoint(self._client.base_url))
            return res.json()

    async def list(self) -> dict[str, Any]:
        """列出已上传的文件。"""
        return await self._client.request_json(
            "GET",
            file_list_endpoint(self._client.base_url),
        )

    async def delete(self, file_id: str | int) -> dict[str, Any]:
        """删除指定文件。"""
        return await self._client.request_json(
            "POST",
            file_delete_endpoint(self._client.base_url),
            body={"file_id": int(file_id)},
        )

    async def retrieve(self, file_id: str) -> dict[str, Any]:
        """获取文件详情和下载链接。"""
        return await self._client.request_json(
            "GET",
            file_retrieve_endpoint(self._client.base_url, file_id),
        )
