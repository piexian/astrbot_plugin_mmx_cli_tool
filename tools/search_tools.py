"""MiniMax 联网搜索 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.search import SearchAPI


@dataclass
class WebSearchTool(FunctionTool):
    """LLM 工具：调用 MiniMax 联网搜索 API。"""

    def __init__(self, api: SearchAPI):
        super().__init__(
            name="mmx_web_search",
            description="Search the web using MiniMax's search engine. Returns relevant results with titles, URLs, and snippets.",
            parameters={
                "type": "object",
                "properties": {
                    "q": {
                        "type": "string",
                        "description": "Search query string",
                    },
                },
                "required": ["q"],
            },
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        query = kwargs.get("q", "")
        if not query:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少搜索查询",
                        "hint": "请提供 q 参数作为搜索关键词",
                        "example": {"q": "MiniMax AI 最新动态"},
                    },
                    ensure_ascii=False,
                )
            )

        try:
            result = await self._api.query(query)
        except Exception as e:
            logger.error(f"[mmx] 搜索失败: {e}")
            return ToolExecResult(
                json.dumps({"ok": False, "error": f"搜索失败: {e}"}, ensure_ascii=False)
            )

        return ToolExecResult(
            json.dumps({"ok": True, "data": result}, ensure_ascii=False)
        )
