"""MiniMax 视觉理解（VLM）API。"""

from __future__ import annotations

from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import vision_endpoint

MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50 MB


async def _to_data_uri(image: str) -> str:
    """将 URL 或本地文件路径转为 base64 数据 URI（对齐 mmx-cli TS SDK）。"""
    if image.startswith("data:"):
        return image

    if image.startswith("base64://"):
        return f"data:image/jpeg;base64,{image.removeprefix('base64://')}"

    if image.startswith("file:///"):
        image = image[8:]
    elif image.startswith("file://"):
        image = image[7:]

    if image.startswith("http://") or image.startswith("https://"):
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
            import base64

            return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"

    # 本地文件路径
    import base64
    import mimetypes
    from pathlib import Path

    p = Path(image)
    if not p.is_file():
        raise FileNotFoundError(f"图片文件不存在: {image}")
    mime, _ = mimetypes.guess_type(p.name)
    if not mime:
        mime = "image/jpeg"
    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}"


class VisionAPI:
    """MiniMax 视觉理解（VLM）接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def describe(
        self,
        image: str,
        prompt: str = "Describe the image.",
    ) -> dict[str, Any]:
        """对图片进行理解和描述。"""
        body: dict[str, Any] = {
            "prompt": prompt,
            "image_url": await _to_data_uri(image),
        }
        return await self._client.request_json(
            "POST",
            vision_endpoint(self._client.base_url),
            body=body,
        )
