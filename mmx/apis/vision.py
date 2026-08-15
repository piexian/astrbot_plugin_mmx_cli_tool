"""MiniMax 视觉理解（VLM）API。"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import vision_endpoint
from ..utils import is_url, resolve_local_input_path

MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50 MB


async def _to_data_uri(
    image: str,
    *,
    data_dir: str | None = None,
    allow_trusted_local_path: bool = False,
    extra_allowed_dirs: list[str] | None = None,
) -> str:
    """将 URL 或本地文件路径转为 base64 数据 URI（对齐 mmx-cli TS SDK）。"""
    if image.startswith("data:"):
        return image

    if image.startswith("base64://"):
        return f"data:image/jpeg;base64,{image.removeprefix('base64://')}"

    if is_url(image):
        async with httpx.AsyncClient() as cl:
            r = await cl.get(image)
            r.raise_for_status()
            content_type = r.headers.get("content-type", "image/jpeg")
            mime = content_type.split(";")[0].strip()
            data = r.content
            if len(data) > MAX_IMAGE_SIZE:
                raise ValueError(
                    f"图片过大 ({len(data) / 1024 / 1024:.1f} MB)，最大 50 MB"
                )

            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    p = resolve_local_input_path(
        image,
        data_dir=data_dir,
        allow_trusted_local_path=allow_trusted_local_path,
        extra_allowed_dirs=extra_allowed_dirs,
        label="图片文件",
    )
    mime, _ = mimetypes.guess_type(p.name)
    if not mime:
        mime = "image/jpeg"

    b64 = base64.b64encode(await asyncio.to_thread(p.read_bytes)).decode("ascii")
    return f"data:{mime};base64,{b64}"


class VisionAPI:
    """MiniMax 视觉理解（VLM）接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def describe(
        self,
        image: str | None = None,
        prompt: str = "Describe the image.",
        *,
        file_id: str | None = None,
        data_dir: str | None = None,
        allow_trusted_local_path: bool = False,
        extra_allowed_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        """对图片进行理解和描述。"""
        if image:
            body: dict[str, Any] = {
                "prompt": prompt,
                "image_url": await _to_data_uri(
                    image,
                    data_dir=data_dir,
                    allow_trusted_local_path=allow_trusted_local_path,
                    extra_allowed_dirs=extra_allowed_dirs,
                ),
            }
        elif file_id:
            body = {
                "prompt": prompt,
                "file_id": file_id,
            }
        else:
            raise ValueError("需要提供 image 或 file_id")
        return await self._client.request_json(
            "POST",
            vision_endpoint(self._client.base_url),
            body=body,
        )
