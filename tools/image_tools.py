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
        default_model: str = "",
        data_dir: str = ".",
        extra_allowed_dirs: list[str] | None = None,
    ):
        super().__init__(
            name="mmx_generate_image",
            description="Generate images using MiniMax AI. Provide a detailed prompt describing the image you want.",
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
                    "aigcWatermark": boolean_param(
                        "Embed AI-generated content watermark in the output image."
                    ),
                    "responseFormat": string_param(
                        "Response format: url (default) or base64."
                    ),
                    "model": string_param(
                        "Model override: image-01 or image-01-live. Omit to use the plugin default_image_model configuration."
                    ),
                    "subjectRef": string_param(
                        "Subject reference for character consistency. Format: image URL/plugin-data or AstrBot temp path, or type=character,image=path-or-url."
                    ),
                },
                required=["prompt"],
            ),
        )
        self._api = api
        self._default_model = default_model
        self._data_dir = data_dir
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
                model=kwargs.get("model") or self._default_model or None,
                n=kwargs.get("n", 1),
                aspect_ratio=aspect_ratio,
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
        return tool_result(json.dumps(resp, ensure_ascii=False))
