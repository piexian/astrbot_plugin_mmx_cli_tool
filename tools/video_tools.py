"""MiniMax 视频生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.video import VideoAPI


@dataclass
class GenerateVideoTool(FunctionTool):
    """LLM 工具：调用 MiniMax 视频生成 API。"""

    def __init__(self, api: VideoAPI, poll_interval: int = 5, video_timeout: int = 600):
        super().__init__(
            name="mmx_generate_video",
            description="Generate a video using MiniMax AI from a text prompt. Returns a task_id for tracking.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Detailed video description",
                    },
                    "first_frame_image_url": {
                        "type": "string",
                        "description": "Optional URL of an image to use as the first frame for image-to-video generation",
                    },
                    "wait_for_result": {
                        "type": "boolean",
                        "description": "Whether to wait for the video to finish generating (may take several minutes). Default false.",
                        "default": False,
                    },
                },
                "required": ["prompt"],
            },
        )
        self._api = api
        self._poll_interval = poll_interval
        self._video_timeout = video_timeout

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
                first_frame_image=kwargs.get("first_frame_image_url"),
            )
        except Exception as e:
            logger.error(f"[mmx] 视频生成失败: {e}")
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": f"视频生成失败: {e}"}, ensure_ascii=False
                )
            )

        task_id = result.get("task_id", "")
        status = result.get("status", "Unknown")

        if kwargs.get("wait_for_result") and task_id:
            try:
                final = await self._api.wait_for_completion(
                    task_id,
                    poll_interval=self._poll_interval,
                    timeout=self._video_timeout,
                )
                file_id = final.get("file_id", "")
                return ToolExecResult(
                    json.dumps(
                        {
                            "ok": True,
                            "task_id": task_id,
                            "status": "Success",
                            "file_id": file_id,
                            "message": "视频生成完成",
                        },
                        ensure_ascii=False,
                    )
                )
            except TimeoutError:
                return ToolExecResult(
                    json.dumps(
                        {
                            "ok": True,
                            "task_id": task_id,
                            "status": "Processing",
                            "message": f"视频仍在生成中，task_id={task_id}，请稍后查询",
                        },
                        ensure_ascii=False,
                    )
                )

        return ToolExecResult(
            json.dumps(
                {"ok": True, "task_id": task_id, "status": status}, ensure_ascii=False
            )
        )
