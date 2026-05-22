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
            description="Analyze and describe an image using MiniMax vision AI. Provide an image URL, local file path, or pre-uploaded file ID.",
            parameters={
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "Image URL or local file path (auto base64-encoded). Mutually exclusive with fileId.",
                    },
                    "fileId": {
                        "type": "string",
                        "description": "Pre-uploaded file ID (skips base64 conversion). Mutually exclusive with image.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Question about the image (default: 'Describe the image.')",
                    },
                },
                "oneOf": [{"required": ["image"]}, {"required": ["fileId"]}],
            },
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        image = kwargs.get("image", "")
        file_id = kwargs.get("fileId")

        if not image and not file_id:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少图片输入",
                        "hint": "请提供 image（图片 URL 或本地路径）或 fileId（预上传文件 ID）",
                        "example": {"image": "https://example.com/photo.jpg", "prompt": "这张图片里有什么？"},
                        "docs": "https://platform.minimaxi.com/docs/api-reference/vlm",
                    },
                    ensure_ascii=False,
                )
            )
        if image and file_id:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "image 和 fileId 不能同时提供",
                        "hint": "请只提供 image（图片 URL/本地路径）或 fileId（二选一）",
                        "example": {"fileId": "file-123456789", "prompt": "Extract the text"},
                    },
                    ensure_ascii=False,
                )
            )

        try:
            result = await self._api.describe(
                image=image or None,
                file_id=file_id,
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
