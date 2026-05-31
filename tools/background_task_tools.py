"""Unified background task query FunctionTool."""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from .background_tasks import BACKGROUND_TASKS
from .result import tool_result
from .schema import object_parameters, string_param


@dataclass
class QueryBackgroundTaskTool(FunctionTool):
    """LLM 工具：查询插件内部后台任务状态。"""

    def __init__(self) -> None:
        super().__init__(
            name="mmx_background_task_get",
            description=(
                "Query a MiniMax plugin background task started by tools such as "
                "mmx_generate_music or mmx_music_cover. Use the taskId returned by the start tool."
            ),
            parameters=object_parameters(
                {
                    "taskId": string_param(
                        "Background task ID returned by mmx_generate_music or mmx_music_cover"
                    ),
                },
                required=["taskId"],
            ),
        )

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        task_id = str(kwargs.get("taskId") or "").strip()
        if not task_id:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 taskId 参数",
                        "hint": "请使用后台任务提交工具返回的 task_id 查询。",
                    },
                    ensure_ascii=False,
                )
            )

        record = BACKGROUND_TASKS.get(task_id)
        if record is None:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "task_id": task_id,
                        "status": "not_found",
                        "error": "后台任务不存在，可能来自旧插件实例或插件已重载。",
                    },
                    ensure_ascii=False,
                )
            )

        return tool_result(json.dumps(record.public(), ensure_ascii=False))
