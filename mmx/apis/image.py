"""MiniMax 图片生成 API。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import image_endpoint


class ImageAPI:
    """MiniMax 图片生成接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def generate(
        self,
        prompt: str,
        *,
        model: str = "image-01",
        n: int = 1,
        aspect_ratio: str | None = None,
        width: int | None = None,
        height: int | None = None,
        response_format: str = "url",
        prompt_optimizer: bool = True,
        subject_reference: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """生成图片。"""
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "n": n,
            "response_format": response_format,
            "prompt_optimizer": prompt_optimizer,
        }
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if width and height:
            body["width"] = width
            body["height"] = height
        if subject_reference:
            body["subject_reference"] = subject_reference

        return await self._client.request_json(
            "POST",
            image_endpoint(self._client.base_url),
            body=body,
        )

    async def save(
        self,
        response: dict[str, Any],
        out_dir: str = ".",
        prefix: str = "image",
    ) -> list[str]:
        """下载响应中的图片到本地，返回文件路径列表。"""
        data = response.get("data", {})
        urls = data.get("image_urls", [])
        base64_images = data.get("image_base64", [])

        saved: list[str] = []
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)

        ts = int(time.time() * 1000)

        for i, url in enumerate(urls):
            filename = f"{prefix}_{ts}_{i}.png"
            filepath = out / filename
            async with httpx.AsyncClient() as cl:
                r = await cl.get(url)
                r.raise_for_status()
                filepath.write_bytes(r.content)
            saved.append(str(filepath))

        import base64

        for i, b64 in enumerate(base64_images):
            filename = f"{prefix}_{ts}_{i + len(urls)}.png"
            filepath = out / filename
            filepath.write_bytes(base64.b64decode(b64))
            saved.append(str(filepath))

        return saved
