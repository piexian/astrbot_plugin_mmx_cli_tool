"""MiniMax 视觉理解 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.vision import VisionAPI


@dataclass
class DescribeImageTool(FunctionTool):
    """LLM 工具：调用 MiniMax 视觉理解 API 分析图片。"""

    def __init__(self, api: VisionAPI):
        super().__init__(
            name="mmx_describe_image",
            description="Analyze and describe an image using MiniMax vision AI. Provide an image URL or file path.",
            parameters={
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "URL or local file path of the image to analyze",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "What to look for in the image. Default 'Describe the image.'",
                    },
                },
                "required": ["image_url"],
            },
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        image = kwargs.get("image_url", "")
        if not image:
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": "缺少 image_url 参数"}, ensure_ascii=False
                )
            )

        try:
            result = await self._api.describe(
                image=image,
                prompt=kwargs.get("prompt", "Describe the image."),
            )
        except Exception as e:
            logger.error(f"[mmx] 图片理解失败: {e}")
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": f"图片理解失败: {e}"}, ensure_ascii=False
                )
            )

        return ToolExecResult(
            json.dumps({"ok": True, "data": result}, ensure_ascii=False)
        )
