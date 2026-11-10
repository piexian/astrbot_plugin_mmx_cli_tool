"""MiniMax 图片生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.image import ImageAPI
from ..mmx.utils import resolve_subject_reference
from .result import tool_result
from .schema import boolean_param, integer_param, object_parameters, string_param


@dataclass
class GenerateImageTool(FunctionTool):
    """LLM 工具：调用 MiniMax 图片生成 API。"""

    def __init__(
        self,
        api: ImageAPI,
        data_dir: str = ".",
        cache_dir: str | None = None,
        extra_allowed_dirs: list[str] | None = None,
    ):
        super().__init__(
            name="mmx_generate_image",
            description=(
                "Generate images using MiniMax AI. Provide a detailed prompt describing the image you want. "
                "Generated images are downloaded to the local AstrBot temp directory; the result includes "
                "file_paths (local paths) and image_urls (remote URLs). Always send images via the local "
                "file_paths — do not pass the remote image_urls to sending tools."
            ),
            parameters=object_parameters(
                {
                    "prompt": string_param(
                        "Detailed image description in English or Chinese"
                    ),
                    "aspectRatio": string_param(
                        "Image aspect ratio, e.g. '1:1', '16:9', '9:16', '4:3'. Ignored if width and height are both set."
                    ),
                    "resolution": string_param(
                        "Output resolution '1K', '2K' or '4K' (MiniMax Code login route, H3 Image). Omit to let the server default apply; it maps to the H3 area parameter on both H3 routes; the request fails if it lands on image-01."
                    ),
                    "n": integer_param("Number of images to generate (1-9, default 1)"),
                    "seed": integer_param(
                        "Random seed for reproducible generation (same seed + parameters = reproducible output)"
                    ),
                    "width": integer_param(
                        "Custom width in pixels (512-2048, multiple of 8). Only for image-01. Overrides aspectRatio."
                    ),
                    "height": integer_param(
                        "Custom height in pixels (512-2048, multiple of 8). Only for image-01. Overrides aspectRatio."
                    ),
                    "promptOptimizer": boolean_param(
                        "Automatically optimize the prompt for better results (default: true). Setting false requires the open-platform route (image-01)."
                    ),
                    "aigcWatermark": boolean_param(
                        "Embed AI-generated content watermark in the output image. Requires the open-platform route (image-01)."
                    ),
                    "responseFormat": string_param(
                        "Response format: url (default) or base64."
                    ),
                    "model": string_param(
                        "Model override. image-01/image-01-live use the legacy endpoint; MiniMax-H3-Image/MiniMax-H3-Image-Highspeed use /v1/images/generations (H3, supports resolution). Omit to use plugin routing: MiniMax Code login generates with H3 Image (Matrix gateway), API-key mode uses the configured default_image_model."
                    ),
                    "subjectRef": string_param(
                        "Subject reference for character consistency. Format: image URL/plugin-data or AstrBot temp path, or type=character,image=path-or-url."
                    ),
                },
                required=["prompt"],
            ),
        )
        self._api = api
        self._data_dir = data_dir
        self._cache_dir = cache_dir or data_dir
        self._extra_allowed_dirs = extra_allowed_dirs

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 prompt 参数",
                        "hint": "请提供 prompt 参数描述想要生成的图片内容",
                        "example": {"prompt": "A cat in a spacesuit, digital art"},
                    },
                    ensure_ascii=False,
                )
            )

        aspect_ratio = kwargs.get("aspectRatio")

        subject_reference = None
        subject_ref = kwargs.get("subjectRef")
        if subject_ref:
            subject_reference = await resolve_subject_reference(
                subject_ref,
                data_dir=self._data_dir,
                extra_allowed_dirs=self._extra_allowed_dirs,
            )

        try:
            result = await self._api.generate(
                prompt=prompt,
                model=kwargs.get("model") or None,
                n=kwargs.get("n", 1),
                aspect_ratio=aspect_ratio,
                resolution=kwargs.get("resolution"),
                width=kwargs.get("width"),
                height=kwargs.get("height"),
                prompt_optimizer=kwargs.get("promptOptimizer", True),
                aigc_watermark=kwargs.get("aigcWatermark", False),
                response_format=kwargs.get("responseFormat", "url"),
                subject_reference=subject_reference,
                seed=kwargs.get("seed"),
            )
        except Exception as e:
            logger.error(f"[mmx] 图片生成失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"图片生成失败: {e}"}, ensure_ascii=False
                )
            )

        data = result.get("data", {})
        image_urls = data.get("image_urls", [])
        task_id = data.get("task_id", "")

        resp: dict = {
            "ok": True,
            "image_urls": image_urls,
            "task_id": task_id,
            "count": len(image_urls),
        }
        # 复用 /mmx image 指令的落地逻辑：先下载到 AstrBot 临时目录，失败才回退远程链接
        try:
            saved = await self._api.save(
                result, out_dir=self._cache_dir, prefix="mmx_image"
            )
        except Exception as e:
            logger.warning(f"[mmx] 图片下载到本地失败，仅返回远程链接: {e}")
        else:
            if saved:
                resp["file_paths"] = saved
                resp["hint"] = (
                    "图片已保存到本地。发送图片时必须使用 file_paths 中的本地路径，"
                    "不要使用 image_urls 远程链接（接收端可能无法下载）。"
                )
        return tool_result(json.dumps(resp, ensure_ascii=False))
