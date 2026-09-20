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
    format_quota_reset_time,
    format_quota_usage,
    is_video_quota_model,
    merge_quota_models,
    normalize_quota_models,
    quota_window_label,
)
from .result import tool_result
from .schema import object_parameters


def _quota_reset_display(window: dict) -> str | None:
    if window.get("unlimited") or window.get("unavailable"):
        return None
    return format_quota_reset_time(window.get("remains_time"))


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
        all_models: list[dict] = []
        balances: list[dict] = []
        failed_key_indexes: list[int] = []
        raw_results = []
        for idx, (raw_result, model_remains) in enumerate(results, start=1):
            if raw_result is None:
                failed_key_indexes.append(idx)
                continue
            raw_results.append(raw_result)
            if raw_result.get("kind") == "account_balance":
                # sk-api- 账户余额型 Key：无按模型额度，直接汇总余额字段
                balances.append(
                    {
                        "key_index": idx,
                        "available_amount": raw_result.get("available_amount"),
                        "cash_balance": raw_result.get("cash_balance"),
                        "voucher_balance": raw_result.get("voucher_balance"),
                        "credit_balance": raw_result.get("credit_balance"),
                        "owed_amount": raw_result.get("owed_amount"),
                    }
                )
                continue
            if not model_remains:
                failed_key_indexes.append(idx)
                continue
            all_models.extend(model_remains)

        summary = []
        merged_models = merge_quota_models(all_models)
        for model, m in sorted(merged_models.items()):
            is_video = is_video_quota_model(model)
            current = m["current"]
            weekly = m["weekly"]
            summary.append(
                {
                    "model": model,
                    "current": format_quota_usage(current, is_video=is_video),
                    "current_window": quota_window_label(current),
                    "current_reset": _quota_reset_display(current),
                    "weekly": format_quota_usage(weekly, is_video=is_video),
                    "weekly_reset": _quota_reset_display(weekly),
                }
            )

        return tool_result(
            json.dumps(
                {
                    "ok": True,
                    "key_count": key_count,
                    "merged": len(api_keys) > 1,
                    "models": summary,
                    "balances": balances,
                    "failed_key_indexes": failed_key_indexes,
                    "raw": raw_results[0] if len(raw_results) == 1 else None,
                },
                ensure_ascii=False,
            )
        )
