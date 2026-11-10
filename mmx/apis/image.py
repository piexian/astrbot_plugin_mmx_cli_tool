"""MiniMax 图片生成 API。

双路由：已登录 MiniMax Code（OAuth）时优先走 Matrix 桌面网关（image_synthesize，
模型由服务端按桌面端配置选择，当前为 MiniMax-H3-Image，支持 resolution 1K/2K/4K）；
否则回退开放平台 /v1/image_generation（image-01 系列）。显式指定 model、自定义宽高、
seed、参考图或 response_format=base64 等开放平台专属参数时一律走开放平台。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import image_endpoint
from ..errors import ErrorCategory, MiniMaxError
from ..matrix import MatrixAPI


class ImageAPI:
    """MiniMax 图片生成接口（Matrix 优先，开放平台回退）。"""

    def __init__(
        self,
        client: MiniMaxClient,
        matrix: MatrixAPI | None = None,
        matrix_enabled: Callable[[], bool] | None = None,
        default_model: str = "",
    ) -> None:
        self._client = client
        self._matrix = matrix
        self._matrix_enabled = matrix_enabled or (lambda: False)
        # 开放平台回退默认（配置项 default_image_model）
        self._default_model = default_model
    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        n: int = 1,
        aspect_ratio: str | None = None,
        resolution: str | None = None,
        width: int | None = None,
        height: int | None = None,
        response_format: str = "url",
        prompt_optimizer: bool = True,
        aigc_watermark: bool = False,
        subject_reference: list[dict[str, Any]] | None = None,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """生成图片；resolution 仅 Matrix 路由支持（1K/2K/4K）。"""
        open_only = (
            bool(model)
            or width is not None
            or height is not None
            or seed is not None
            or subject_reference is not None
            or response_format != "url"
        )
        if self._matrix is not None and self._matrix_enabled() and not open_only:
            return await self._generate_matrix(
                prompt, n=n, aspect_ratio=aspect_ratio, resolution=resolution
            )
        return await self._generate_open(
            prompt,
            model=model,
            n=n,
            aspect_ratio=aspect_ratio,
            width=width,
            height=height,
            response_format=response_format,
            prompt_optimizer=prompt_optimizer,
            aigc_watermark=aigc_watermark,
            subject_reference=subject_reference,
            seed=seed,
        )

    async def _generate_matrix(
        self,
        prompt: str,
        *,
        n: int,
        aspect_ratio: str | None,
        resolution: str | None,
    ) -> dict[str, Any]:
        """Matrix 网关生图，响应归一化为开放平台的 data.image_urls 结构。"""
        assert self._matrix is not None
        data = await self._matrix.image_synthesize(
            [prompt] * n, aspect_ratio=aspect_ratio, resolution=resolution
        )
        items = data.get("success_items")
        entries = [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []
        urls = [e["output_url"] for e in entries if e.get("output_url")]
        if urls:
            return {"data": {"image_urls": urls, "image_base64": []}}
        failed = data.get("failed_items")
        failures = [e for e in failed if isinstance(e, dict)] if isinstance(failed, list) else []
        reason = next(
            (str(e["error_msg"]) for e in failures if e.get("error_msg")),
            "服务端未返回生成结果",
        )
        raise MiniMaxError(ErrorCategory.GENERAL, f"图片生成失败: {reason}")

    async def _generate_open(
        self,
        prompt: str,
        *,
        model: str | None,
        n: int,
        aspect_ratio: str | None,
        width: int | None,
        height: int | None,
        response_format: str,
        prompt_optimizer: bool,
        aigc_watermark: bool,
        subject_reference: list[dict[str, Any]] | None,
        seed: int | None,
    ) -> dict[str, Any]:
        """开放平台 /v1/image_generation（image-01 系列）。"""
        effective_model = model or self._default_model or None
        body: dict[str, Any] = {
            "prompt": prompt,
            "n": n,
            "response_format": response_format,
            "prompt_optimizer": prompt_optimizer,
        }
        if effective_model:
            body["model"] = effective_model
        if aigc_watermark:
            body["aigc_watermark"] = True
        if aspect_ratio:
            body["aspect_ratio"] = aspect_ratio
        if width and height:
            body["width"] = width
            body["height"] = height
        if subject_reference:
            body["subject_reference"] = subject_reference
        if seed is not None:
            body["seed"] = seed

        return await self._client.request_json(
            "POST",
            image_endpoint(self._client.base_url),
            body=body,
            model=effective_model or "",
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

        token = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        for i, url in enumerate(urls):
            filename = f"{prefix}_{token}_{i}.png"
            filepath = out / filename
            async with httpx.AsyncClient() as cl:
                r = await cl.get(url)
                r.raise_for_status()
                await asyncio.to_thread(filepath.write_bytes, r.content)
            saved.append(str(filepath))

        import base64

        for i, b64 in enumerate(base64_images):
            filename = f"{prefix}_{token}_{i + len(urls)}.png"
            filepath = out / filename
            await asyncio.to_thread(filepath.write_bytes, base64.b64decode(b64))
            saved.append(str(filepath))

        return saved
