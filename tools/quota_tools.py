"""MiniMax 额度查询 FunctionTool。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.quota import QuotaAPI
from ..mmx.quota_usage import (
    is_video_quota_model,
    merge_quota_models,
    merge_quota_window,
    normalize_quota_models,
    resolve_used_percent,
)
from .result import tool_result
from .schema import object_parameters


def _quota_number_display(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return "未知"


def _video_quota_window_display(window: dict) -> str:
    has_counts = any(
        isinstance(window.get(key), int) for key in ("used", "remaining", "total")
    )
    if not has_counts:
        return "未知"
    used = _quota_number_display(window.get("used"))
    remaining = _quota_number_display(window.get("remaining"))
    total = _quota_number_display(window.get("total"))
    return f"{used} / {remaining}（{total}）"


def _quota_window_display(window: dict, *, is_video: bool = False) -> str:
    if window.get("unlimited") is True:
        return "∞"
    if is_video:
        return _video_quota_window_display(window)
    percent = resolve_used_percent(window)
    if isinstance(percent, int):
        return f"已用{percent}%"
    return "未知"


def _quota_reset_display(window: dict) -> str | None:
    value = window.get("remains_time")
    if not isinstance(value, int) or value <= 0:
        return None
    total_minutes = value // 60000
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{hours}小时{minutes}分钟"
    if hours > 0:
        return f"{hours}小时"
    return f"{minutes}分钟"


@dataclass
class CheckQuotaTool(FunctionTool):
    """LLM 工具：查询 MiniMax API 额度使用情况。"""

    def __init__(self, api: QuotaAPI, api_keys: list[str] | None = None):
        super().__init__(
            name="mmx_check_quota",
            description="Check MiniMax API quota and usage. Returns merged quota for configured keys.",
            parameters=object_parameters({}),
        )
        self._api = api
        self._api_keys = [k.strip() for k in (api_keys or []) if k.strip()]

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        api_keys = self._api_keys or [None]
        key_count = len(self._api_keys)

        async def _fetch(api_key: str | None):
            try:
                result = await self._api.info(api_key)
                return result, normalize_quota_models(result.get("model_remains", []))
            except Exception as e:
                logger.error(f"[mmx] 额度查询失败: {e}")
                return None, []

        results = await asyncio.gather(*[_fetch(k) for k in api_keys])

        # 精简为人类可读的摘要
        merged: dict[str, dict] = {}
        failed_key_indexes: list[int] = []
        raw_results = []
        for idx, (raw_result, model_remains) in enumerate(results, start=1):
            if raw_result is not None:
                raw_results.append(raw_result)
            if not model_remains:
                failed_key_indexes.append(idx)
                continue
            for name, model in merge_quota_models(model_remains).items():
                target = merged.setdefault(name, {"current": {}, "weekly": {}})
                merge_quota_window(target["current"], model["current"])
                merge_quota_window(target["weekly"], model["weekly"])

        summary = []
        merged_models = merge_quota_models(
            [
                {"model": name, "current": value["current"], "weekly": value["weekly"]}
                for name, value in merged.items()
            ]
        )
        for model, m in sorted(merged_models.items()):
            is_video = is_video_quota_model(model)
            current = m["current"]
            weekly = m["weekly"]
            summary.append(
                {
                    "model": model,
                    "current": _quota_window_display(current, is_video=is_video),
                    "current_reset": None
                    if current.get("unlimited") is True
                    else _quota_reset_display(current),
                    "weekly": _quota_window_display(weekly, is_video=is_video),
                    "weekly_reset": None
                    if weekly.get("unlimited") is True
                    else _quota_reset_display(weekly),
                }
            )

        return tool_result(
            json.dumps(
                {
                    "ok": True,
                    "key_count": key_count,
                    "merged": len(api_keys) > 1,
                    "models": summary,
                    "failed_key_indexes": failed_key_indexes,
                    "raw": raw_results[0] if len(raw_results) == 1 else None,
                },
                ensure_ascii=False,
            )
        )
