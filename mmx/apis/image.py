"""MiniMax 图片生成 API。

双路由：已登录 MiniMax Code（OAuth，经刷新校验）时优先走 Matrix 桌面网关
（image_synthesize，模型由服务端按桌面端配置选择，当前为 MiniMax-H3-Image，
支持 resolution 1K/2K/4K）；否则回退开放平台 /v1/image_generation（image-01 系列）。
显式指定 model、自定义宽高、seed、参考图、response_format=base64、加水印或
关闭提示词优化等开放平台专属诉求时一律走开放平台；resolution 在此路无法兑现，
会直接报错而不是静默忽略。路由在提交生图请求前一次性决定，超时或部分成功
不自动跨路由重试。
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import image_endpoint
from ..errors import ErrorCategory, MiniMaxError
from ..matrix import MatrixAPI


async def _matrix_disabled() -> bool:
    return False


class ImageAPI:
    """MiniMax 图片生成接口（Matrix 优先，开放平台回退）。"""

    def __init__(
        self,
        client: MiniMaxClient,
        matrix: MatrixAPI | None = None,
        matrix_enabled: Callable[[], Awaitable[bool]] | None = None,
        default_model: str = "",
    ) -> None:
        self._client = client
        self._matrix = matrix
        # 异步谓词：每次生图前刷新校验 OAuth 可用性
        self._matrix_enabled = matrix_enabled or _matrix_disabled
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
        """生成图片；路由在提交请求前一次性决定，超时或部分成功不跨路由重试。"""
        if not isinstance(prompt, str) or not prompt.strip():
            raise MiniMaxError(ErrorCategory.GENERAL, "prompt 不能为空")
        if type(n) is not int or not 1 <= n <= 9:
            raise MiniMaxError(ErrorCategory.GENERAL, "n 必须为 1-9 的整数")
        if resolution is not None and resolution not in {"1K", "2K", "4K"}:
            raise MiniMaxError(ErrorCategory.GENERAL, "resolution 只支持 1K、2K 或 4K")
        open_only = (
            bool(model)
            or width is not None
            or height is not None
            or seed is not None
            or subject_reference is not None
            or response_format != "url"
            or aigc_watermark
            or not prompt_optimizer
        )
        if self._matrix is not None and not open_only and await self._matrix_enabled():
            return await self._generate_matrix(
                prompt, n=n, aspect_ratio=aspect_ratio, resolution=resolution
            )
        if resolution is not None:
            raise MiniMaxError(
                ErrorCategory.GENERAL,
                "resolution 仅在已登录 MiniMax Code 的 H3 Image 路由可用；"
                "请去掉 --resolution 或先 /mmx login",
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
        """Matrix 网关生图，响应归一化为 data.image_urls 并保留元数据。"""
        assert self._matrix is not None
        data = await self._matrix.image_synthesize(
            [prompt] * n, aspect_ratio=aspect_ratio, resolution=resolution
        )
        items = data.get("success_items")
        entries = [e for e in items if isinstance(e, dict)] if isinstance(items, list) else []
        urls = [e["output_url"] for e in entries if e.get("output_url")]
        failed = data.get("failed_items")
        failures = [e for e in failed if isinstance(e, dict)] if isinstance(failed, list) else []
        result = {
            "data": {"image_urls": urls, "image_base64": []},
            "meta": {
                "route": "matrix",
                "total_requested": data.get("total_requested", n),
                "total_success": data.get("total_success", len(urls)),
                "failed_items": failures,
            },
        }
        if urls:
            return result
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

        result = await self._client.request_json(
            "POST",
            image_endpoint(self._client.base_url),
            body=body,
            model=effective_model or "",
        )
        result["meta"] = {"route": "open"}
        return result
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
