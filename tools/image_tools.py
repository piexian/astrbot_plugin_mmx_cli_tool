"""MiniMax 图片生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.image import ImageAPI


@dataclass
class GenerateImageTool(FunctionTool):
    """LLM 工具：调用 MiniMax 图片生成 API。"""

    def __init__(self, api: ImageAPI):
        super().__init__(
            name="mmx_generate_image",
            description="Generate images using MiniMax AI. Provide a detailed prompt describing the image you want.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed image description in English or Chinese",
                    },
                    "n": {
                        "type": "integer",
                        "description": "Number of images to generate (1-9, default 1)",
                        "default": 1,
                    },
                    "aspect_ratio": {
                        "type": "string",
                        "description": "Image aspect ratio, e.g. '1:1', '16:9', '9:16', '4:3'",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Image width in pixels (512-2048, multiple of 8). Use with height for exact size.",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Image height in pixels (512-2048, multiple of 8). Use with width for exact size.",
                    },
                },
                "required": ["prompt"],
            },
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": "缺少 prompt 参数"}, ensure_ascii=False
                )
            )

        try:
            result = await self._api.generate(
                prompt=prompt,
                n=kwargs.get("n", 1),
                aspect_ratio=kwargs.get("aspect_ratio"),
                width=kwargs.get("width"),
                height=kwargs.get("height"),
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
