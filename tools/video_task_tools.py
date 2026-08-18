"""MiniMax 视频任务查询与下载 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.video import VideoAPI
from ..mmx.utils import resolve_data_path
from .result import tool_result
from .schema import object_parameters, string_param


@dataclass
class QueryVideoTaskTool(FunctionTool):
    """LLM 工具：查询 MiniMax 视频生成任务状态。"""

    def __init__(self, api: VideoAPI):
        super().__init__(
            name="mmx_video_task_get",
            description="Query the status of a video generation task. Returns status, progress, and file_id when completed.",
            parameters=object_parameters(
                {
                    "taskId": string_param(
                        "Video generation task ID (returned by mmx_generate_video)"
                    ),
                    "model": string_param(
                        "Model used for generation. Set to MiniMax-H3 for V2 task queries."
                    ),
                },
                required=["taskId"],
            ),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        task_id = kwargs.get("taskId", "")
        if not task_id:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 taskId 参数",
                        "hint": "请提供 mmx_generate_video 返回的 task_id",
                        "example": {"taskId": "task_xxxxxxxxxxxx"},
                    },
                    ensure_ascii=False,
                )
            )

        try:
            result = await self._api.get_task(task_id, model=kwargs.get("model"))
        except Exception as e:
            logger.error(f"[mmx] 视频任务查询失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"视频任务查询失败: {e}"},
                    ensure_ascii=False,
                )
            )

        status = result.get("status", "Unknown")
        file_id = result.get("file_id", "")
        resp: dict = {"ok": True, "task_id": task_id, "status": status}
        if file_id:
            resp["file_id"] = file_id
            resp["hint"] = "视频已完成，使用 mmx_video_download 工具下载"
        elif status == "Processing":
            resp["hint"] = "视频仍在生成中，请稍后再次查询"
        elif status == "Failed":
            resp["ok"] = False
            resp["error"] = "视频生成失败"
        return tool_result(json.dumps(resp, ensure_ascii=False))


@dataclass
class DownloadVideoTool(FunctionTool):
    """LLM 工具：下载已完成的 MiniMax 视频。"""

    def __init__(
        self, api: VideoAPI, data_dir: str = ".", cache_dir: str | None = None
    ):
        super().__init__(
            name="mmx_video_download",
            description="Download a completed video by file ID. Returns the local file path.",
            parameters=object_parameters(
                {
                    "fileId": string_param(
                        "File ID of the completed video (from mmx_video_task_get result)"
                    ),
                    "out": string_param(
                        "Output file path under the plugin data directory (optional; saves to the AstrBot temp directory if omitted)"
                    ),
                },
                required=["fileId"],
            ),
        )
        self._api = api
        self._data_dir = data_dir
        self._cache_dir = cache_dir or data_dir

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        file_id = kwargs.get("fileId", "")
        if not file_id:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 fileId 参数",
                        "hint": "请提供 mmx_video_task_get 返回的 file_id",
                        "example": {"fileId": "file_xxxxxxxxxxxx"},
                    },
                    ensure_ascii=False,
                )
            )

        out = str(kwargs.get("out") or "").strip()
        if out:
            out_path = resolve_data_path(self._data_dir, out)
            if out_path is None:
                return tool_result(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "out 路径不合法",
                            "hint": f"out 必须位于插件数据目录内：{self._data_dir}",
                        },
                        ensure_ascii=False,
                    )
                )
        else:
            import time

            out_path = (
                Path(self._cache_dir) / f"mmx_video_{int(time.time() * 1000)}.mp4"
            ).resolve()
        if out_path == Path(self._data_dir).resolve():
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "out 必须是文件路径",
                        "hint": "请提供插件数据目录内的文件路径，例如 videos/output.mp4",
                    },
                    ensure_ascii=False,
                )
            )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            saved = await self._api.download(file_id, str(out_path))
        except Exception as e:
            logger.error(f"[mmx] 视频下载失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"视频下载失败: {e}"},
                    ensure_ascii=False,
                )
            )

        return tool_result(
            json.dumps(
                {"ok": True, "file_path": saved, "message": "视频下载完成"},
                ensure_ascii=False,
            )
        )
