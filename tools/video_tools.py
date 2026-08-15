"""MiniMax 视频生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.video import VideoAPI
from ..mmx.utils import resolve_image
from .result import tool_result
from .schema import boolean_param, integer_param, object_parameters, string_param


def _hint_json(error: str, hint: str, example: dict | None = None) -> str:
    """构造带提示的错误 JSON。"""
    resp: dict = {"ok": False, "error": error, "hint": hint}
    if example:
        resp["example"] = example
    resp["docs"] = "https://platform.minimaxi.com/docs/api-reference/video-generation"
    return json.dumps(resp, ensure_ascii=False)


@dataclass
class GenerateVideoTool(FunctionTool):
    """LLM 工具：调用 MiniMax 视频生成 API。

    支持 T2V、I2V、SEF（首尾帧插值）、S2V（角色一致性）四种模式。
    """

    def __init__(
        self,
        api: VideoAPI,
        poll_interval: int = 5,
        video_timeout: int = 600,
        default_model: str = "",
        default_sef_model: str = "",
        default_subject_model: str = "",
        data_dir: str = ".",
        extra_allowed_dirs: list[str] | None = None,
    ):
        super().__init__(
            name="mmx_generate_video",
            description=(
                "Generate a video using MiniMax AI.\n"
                "Modes:\n"
                "  T2V: text prompt only\n"
                "  I2V: firstFrame image\n"
                "  SEF: firstFrame + lastFrame (start-end frame interpolation)\n"
                "  S2V: subjectImage (character consistency)\n"
                "Model selection follows plugin configuration unless explicitly set."
            ),
            parameters=object_parameters(
                {
                    "prompt": string_param("Video description (required)"),
                    "model": string_param(
                        "Model override. Omit to use plugin default_video_model or the configured SEF/S2V model."
                    ),
                    "firstFrame": string_param(
                        "First frame image (URL, plugin-data path, or AstrBot temp path)."
                    ),
                    "lastFrame": string_param(
                        "Last frame image (URL, plugin-data path, or AstrBot temp path). Requires firstFrame."
                    ),
                    "subjectImage": string_param(
                        "Subject reference image for character consistency (URL, plugin-data path, or AstrBot temp path)."
                    ),
                    "callbackUrl": string_param(
                        "Webhook URL for completion notification"
                    ),
                    "noWait": boolean_param(
                        "Return task_id immediately without waiting for completion."
                    ),
                    "pollInterval": integer_param(
                        "Polling interval in seconds when waiting."
                    ),
                },
                required=["prompt"],
            ),
        )
        self._api = api
        self._poll_interval = poll_interval
        self._video_timeout = video_timeout
        self._default_model = default_model
        self._default_sef_model = default_sef_model
        self._default_subject_model = default_subject_model
        self._data_dir = data_dir
        self._extra_allowed_dirs = extra_allowed_dirs

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = kwargs.get("prompt", "")
        if not prompt:
            return tool_result(
                _hint_json(
                    "缺少 prompt 参数",
                    "prompt 为必需参数，请描述你想要生成的视频内容",
                    {"prompt": "A cat playing piano, cinematic lighting"},
                )
            )

        first_frame = kwargs.get("firstFrame")
        last_frame = kwargs.get("lastFrame")
        subject_image = kwargs.get("subjectImage")
        model = kwargs.get("model")
        callback_url = kwargs.get("callbackUrl")
        no_wait = kwargs.get("noWait", False)
        poll_interval = kwargs.get("pollInterval") or self._poll_interval

        # 校验 SEF 模式：lastFrame 需要 firstFrame
        if last_frame and not first_frame:
            return tool_result(
                _hint_json(
                    "SEF 模式需要同时提供 firstFrame 和 lastFrame",
                    "请同时提供 firstFrame（起始帧）和 lastFrame（结束帧）来启用首尾帧插值模式",
                    {
                        "prompt": "Walk forward",
                        "firstFrame": "start.jpg",
                        "lastFrame": "end.jpg",
                    },
                )
            )

        # 校验 SEF 与 S2V 互斥（对齐 mmx-cli）
        if (first_frame or last_frame) and subject_image:
            return tool_result(
                _hint_json(
                    "firstFrame/lastFrame 与 subjectImage 不能同时使用",
                    "首尾帧插值（SEF）和角色一致性（S2V）是两种独立模式，请选择其一",
                    {
                        "prompt": "A person walking",
                        "firstFrame": "start.jpg",
                        "lastFrame": "end.jpg",
                    },
                )
            )

        # 校验 Fast 模型需要 firstFrame（对齐 mmx-cli）
        if model and "Fast" in model and not first_frame:
            return tool_result(
                _hint_json(
                    f"{model} 模型需要提供 firstFrame",
                    "Fast 模型属于 I2V 模式，必须提供 firstFrame（起始帧图片）",
                    {
                        "prompt": "A cat running",
                        "firstFrame": "cat.jpg",
                        "model": model,
                    },
                )
            )

        # 构建 subject_reference（image 必须为数组，对齐 mmx-cli）
        subject_reference = None
        if subject_image:
            converted_subject = await resolve_image(
                subject_image,
                data_dir=self._data_dir,
                extra_allowed_dirs=self._extra_allowed_dirs,
            )
            subject_reference = [{"type": "character", "image": [converted_subject]}]

        # 插件数据目录内路径转 Data URI（对齐 mmx-cli Rn 函数）
        resolved_first = (
            await resolve_image(
                first_frame,
                data_dir=self._data_dir,
                extra_allowed_dirs=self._extra_allowed_dirs,
            )
            if first_frame
            else None
        )
        resolved_last = (
            await resolve_image(
                last_frame,
                data_dir=self._data_dir,
                extra_allowed_dirs=self._extra_allowed_dirs,
            )
            if last_frame
            else None
        )
        selected_model = (
            model
            or (
                self._default_sef_model
                if resolved_first and resolved_last
                else self._default_subject_model
                if subject_reference
                else self._default_model
            )
            or None
        )

        try:
            result = await self._api.generate(
                prompt=prompt,
                model=selected_model,
                first_frame_image=resolved_first,
                last_frame_image=resolved_last,
                subject_reference=subject_reference,
                callback_url=callback_url,
            )
        except Exception as e:
            logger.error(f"[mmx] 视频生成失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"视频生成失败: {e}"}, ensure_ascii=False
                )
            )

        task_id = result.get("task_id", "")
        status = result.get("status", "Unknown")

        if no_wait or not task_id:
            return tool_result(
                json.dumps(
                    {
                        "ok": True,
                        "task_id": task_id,
                        "status": status,
                        "hint": "使用 mmx_video_task_get 工具查询进度，mmx_video_download 下载视频",
                    },
                    ensure_ascii=False,
                )
            )

        # 同步等待完成
        try:
            final = await self._api.wait_for_completion(
                task_id,
                poll_interval=poll_interval,
                timeout=self._video_timeout,
            )
            file_id = final.get("file_id", "")
            return tool_result(
                json.dumps(
                    {
                        "ok": True,
                        "task_id": task_id,
                        "status": "Success",
                        "file_id": file_id,
                        "message": "视频生成完成",
                        "hint": "使用 mmx_video_download 工具下载视频文件",
                    },
                    ensure_ascii=False,
                )
            )
        except TimeoutError:
            return tool_result(
                json.dumps(
                    {
                        "ok": True,
                        "task_id": task_id,
                        "status": "Processing",
                        "message": f"视频仍在生成中，task_id={task_id}，请稍后查询",
                        "hint": "使用 mmx_video_task_get 工具查询进度",
                    },
                    ensure_ascii=False,
                )
            )
