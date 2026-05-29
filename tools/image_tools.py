"""MiniMax 图片生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.image import ImageAPI
from ..mmx.utils import is_url, resolve_image
from .schema import boolean_param, integer_param, object_parameters, string_param


@dataclass
class GenerateImageTool(FunctionTool):
    """LLM 工具：调用 MiniMax 图片生成 API。"""

    def __init__(self, api: ImageAPI):
        super().__init__(
            name="mmx_generate_image",
            description="Generate images using MiniMax AI (image-01 / image-01-live). Provide a detailed prompt describing the image you want.",
            parameters=object_parameters(
                {
                    "prompt": string_param(
                        "Detailed image description in English or Chinese"
                    ),
                    "aspectRatio": string_param(
                        "Image aspect ratio, e.g. '1:1', '16:9', '9:16', '4:3'. Ignored if width and height are both set."
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
                        "Automatically optimize the prompt for better results (default: true)"
                    ),
                    "model": string_param("Model: image-01 (default) or image-01-live"),
                    "subjectRef": string_param(
                        "Subject reference for character consistency. Format: image URL or local path."
                    ),
                },
                required=["prompt"],
            ),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return ToolExecResult(
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

        # 构建 subject_reference（对齐 mmx-cli：URL → image_url，本地 → image_file）
        subject_reference = None
        subject_ref = kwargs.get("subjectRef")
        if subject_ref:
            if is_url(subject_ref):
                subject_reference = [{"type": "character", "image_url": subject_ref}]
            else:
                data_uri = await resolve_image(subject_ref)
                subject_reference = [{"type": "character", "image_file": data_uri}]

        try:
            result = await self._api.generate(
                prompt=prompt,
                model=kwargs.get("model", "image-01"),
                n=kwargs.get("n", 1),
                aspect_ratio=aspect_ratio,
                width=kwargs.get("width"),
                height=kwargs.get("height"),
                prompt_optimizer=kwargs.get("promptOptimizer", True),
                subject_reference=subject_reference,
                seed=kwargs.get("seed"),
            )
        except Exception as e:
            logger.error(f"[mmx] 图片生成失败: {e}")
            return ToolExecResult(
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
        return ToolExecResult(json.dumps(resp, ensure_ascii=False))
