"""MiniMax 额度查询 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.quota import QuotaAPI
from .result import tool_result
from .schema import object_parameters


@dataclass
class CheckQuotaTool(FunctionTool):
    """LLM 工具：查询 MiniMax API 额度使用情况。"""

    def __init__(self, api: QuotaAPI):
        super().__init__(
            name="mmx_check_quota",
            description="Check MiniMax API quota and usage. Returns remaining usage for each model.",
            parameters=object_parameters({}),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            result = await self._api.info()
        except Exception as e:
            logger.error(f"[mmx] 额度查询失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"额度查询失败: {e}"}, ensure_ascii=False
                )
            )

        # 精简为人类可读的摘要
        model_remains = result.get("model_remains", [])
        summary = []
        for m in model_remains:
            name = m.get("model_name", "unknown")
            total = m.get("current_interval_total_count", 0)
            used = m.get("current_interval_usage_count", 0)
            remaining = total - used
            summary.append(
                {
                    "model": name,
                    "total": total,
                    "used": used,
                    "remaining": max(remaining, 0),
                }
            )

        return tool_result(
            json.dumps(
                {"ok": True, "models": summary, "raw": result}, ensure_ascii=False
            )
        )
