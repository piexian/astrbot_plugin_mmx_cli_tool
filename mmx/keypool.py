"""MiniMax API Key 池 — 多 Key 轮询与按模型额度感知调度。

每个 Key 可关联区域（region），请求时自动使用对应区域的 API 端点。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class KeyState:
    """单个 API Key 的状态。"""

    key: str
    index: int
    region: str = "cn"
    enabled: bool = True
    disabled_time: float = 0.0
    disabled_reason: str = ""
    model_quotas: dict[str, dict[str, int]] = field(default_factory=dict)
    quota_checked_at: float = 0.0
    total_requests: int = 0
    failed_requests: int = 0


class KeyPool:
    """多 API Key 池，按目标模型剩余额度从多到少优先选择。"""

    QUOTA_CACHE_TTL = 300
    DISABLED_COOLDOWN = 600
    CHECK_TIMEOUT = 10.0

    def __init__(self, entries: list[dict[str, str]]) -> None:
        """entries: [{"key": "sk-xxx", "region": "cn"}, ...]"""
        if not entries:
            raise ValueError("至少需要提供一个 API Key")
        self._states: list[KeyState] = [
            KeyState(key=e["key"].strip(), index=i, region=e.get("region", "cn"))
            for i, e in enumerate(entries)
            if e.get("key", "").strip()
        ]
        if not self._states:
            raise ValueError("没有有效的 API Key")
        self._index: int = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _mask_key(key: str) -> str:
        if len(key) <= 8:
            return "*" * len(key)
        return key[:4] + "..." + key[-4:]

    @property
    def key_count(self) -> int:
        return len(self._states)

    def enabled_count(self) -> int:
        return sum(1 for s in self._states if s.enabled)

    def get_summary(self) -> dict[str, Any]:
        items = []
        for s in self._states:
            items.append({
                "index": s.index,
                "masked_key": self._mask_key(s.key),
                "region": s.region,
                "enabled": s.enabled,
                "disabled_reason": s.disabled_reason or None,
                "model_quotas": s.model_quotas,
                "total_requests": s.total_requests,
                "failed_requests": s.failed_requests,
            })
        return {"keys": items, "total": len(self._states), "enabled": self.enabled_count()}

    def _model_remaining(self, state: KeyState, model: str) -> int:
        mq = state.model_quotas.get(model)
        if mq is None:
            return -1
        return mq.get("remaining", -1)

    async def get_key(self, model: str = "") -> tuple[str, int, str]:
        """选择目标模型有可用额度的 Key。

        Returns:
            (api_key, key_index, region)
        """
        async with self._lock:
            now = time.time()
            for s in self._states:
                if not s.enabled and s.disabled_time > 0 and (now - s.disabled_time) > self.DISABLED_COOLDOWN:
                    s.enabled = True
                    s.disabled_reason = ""
                    s.disabled_time = 0

            enabled = [s for s in self._states if s.enabled]
            if not enabled:
                reasons = [
                    f"[{s.index}] {self._mask_key(s.key)}: {s.disabled_reason}"
                    for s in self._states
                ]
                raise RuntimeError("所有 API Key 额度已用尽:\n" + "\n".join(reasons))

            if model:
                with_quota = [s for s in enabled if self._model_remaining(s, model) > 0]
                unknown = [s for s in enabled if self._model_remaining(s, model) == -1]
                if with_quota:
                    chosen = max(with_quota, key=lambda s: self._model_remaining(s, model))
                elif unknown:
                    self._index = (self._index + 1) % len(unknown)
                    chosen = unknown[self._index]
                else:
                    reasons = [
                        f"[{s.index}] {self._mask_key(s.key)}: 模型 {model} 额度已用尽"
                        for s in enabled
                    ]
                    raise RuntimeError(f"所有 Key 的 {model} 模型额度已用尽:\n" + "\n".join(reasons))
            else:
                known = [s for s in enabled if any(
                    mq.get("remaining", -1) >= 0 for mq in s.model_quotas.values()
                )]
                if known:
                    chosen = max(known, key=lambda s: sum(
                        mq.get("remaining", 0) for mq in s.model_quotas.values()
                    ))
                else:
                    self._index = (self._index + 1) % len(enabled)
                    chosen = enabled[self._index]

            chosen.total_requests += 1
            return chosen.key, chosen.index, chosen.region

    async def mark_failed(self, key_index: int) -> None:
        async with self._lock:
            for s in self._states:
                if s.index == key_index:
                    s.failed_requests += 1
                    break

    async def mark_model_exhausted(self, key_index: int, model: str) -> None:
        async with self._lock:
            for s in self._states:
                if s.index == key_index:
                    if model in s.model_quotas:
                        s.model_quotas[model]["remaining"] = 0
                    all_zero = all(
                        mq.get("remaining", -1) == 0
                        for mq in s.model_quotas.values()
                    )
                    if all_zero and s.model_quotas:
                        s.enabled = False
                        s.disabled_time = time.time()
                        s.disabled_reason = "所有模型额度已用尽"
                    break

    async def check_all_quotas(self, fetcher: Callable[[str], Any]) -> None:
        for s in self._states:
            if not s.enabled and s.disabled_time > 0:
                if time.time() - s.disabled_time < self.DISABLED_COOLDOWN:
                    continue
            try:
                result = await asyncio.wait_for(fetcher(s.key), timeout=self.CHECK_TIMEOUT)
                model_remains = result.get("model_remains", [])
                new_quotas: dict[str, dict[str, int]] = {}
                all_zero = True
                for m in model_remains:
                    name = m.get("model_name", "unknown")
                    total = m.get("current_interval_total_count", 0)
                    used = m.get("current_interval_usage_count", 0)
                    remaining = max(total - used, 0)
                    new_quotas[name] = {"total": total, "used": used, "remaining": remaining}
                    if remaining > 0:
                        all_zero = False

                s.model_quotas = new_quotas
                s.quota_checked_at = time.time()

                if all_zero and new_quotas and s.enabled:
                    s.enabled = False
                    s.disabled_time = time.time()
                    s.disabled_reason = "所有模型额度均已用尽"
                elif not all_zero and not s.enabled:
                    s.enabled = True
                    s.disabled_reason = ""
            except Exception:
                pass
