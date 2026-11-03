"""MiniMax API Key 池 — 多 Key 轮询与按模型额度感知调度。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from .quota_usage import resolve_quota_model, summarize_keypool_quota


@dataclass
class KeyState:
    """单个 API Key 的状态。"""

    key: str
    index: int
    enabled: bool = True
    disabled_time: float = 0.0
    disabled_reason: str = ""
    model_quotas: dict[str, dict[str, Any]] = field(default_factory=dict)
    quota_checked_at: float = 0.0
    total_requests: int = 0
    failed_requests: int = 0


def unique_keys(keys: Iterable[str]) -> list[str]:
    """去掉空白与重复的 Key，保持原顺序。"""
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        text = str(key or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


class KeyPool:
    """多 API Key 池，按目标模型剩余额度从多到少优先选择。"""

    QUOTA_CACHE_TTL = 300
    DISABLED_COOLDOWN = 600
    CHECK_TIMEOUT = 10.0

    def __init__(self, keys: list[str], region: str = "cn") -> None:
        if not keys:
            raise ValueError("至少需要提供一个 API Key")
        self._region = region
        self._states: list[KeyState] = [
            KeyState(key=key, index=i) for i, key in enumerate(unique_keys(keys))
        ]
        if not self._states:
            raise ValueError("没有有效的 API Key")
        self._index: int = 0
        self._quotas_refreshed_at: float = 0.0
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
            items.append(
                {
                    "index": s.index,
                    "masked_key": self._mask_key(s.key),
                    "enabled": s.enabled,
                    "disabled_reason": s.disabled_reason or None,
                    "model_quotas": s.model_quotas,
                    "total_requests": s.total_requests,
                    "failed_requests": s.failed_requests,
                }
            )
        return {
            "keys": items,
            "total": len(self._states),
            "enabled": self.enabled_count(),
        }

    def set_keys(self, keys: Iterable[str]) -> bool:
        """按给定 Key 列表同步池内容：已有的保留序号与状态，新增追加，消失的移除。"""
        existing = {state.key: state for state in self._states}
        states = [
            existing.get(key) or KeyState(key=key, index=-1)
            for key in unique_keys(keys)
        ]
        used = {state.index for state in states if state.index >= 0}
        for state in states:
            if state.index >= 0:
                continue
            index = 0
            while index in used:
                index += 1
            state.index = index
            used.add(index)
        self._states = states
        return bool(states)

    def _model_remaining(self, state: KeyState, model: str) -> int:
        quota_model = resolve_quota_model(model, state.model_quotas)
        if not quota_model:
            return -1
        remaining = state.model_quotas[quota_model].get("remaining", -1)
        return remaining if isinstance(remaining, int) else -1

    async def get_key(self, model: str = "") -> tuple[str, int]:
        """选择目标模型有可用额度的 Key。

        Returns:
            (api_key, key_index)
        """
        async with self._lock:
            now = time.time()
            for s in self._states:
                if (
                    not s.enabled
                    and s.disabled_time > 0
                    and (now - s.disabled_time) > self.DISABLED_COOLDOWN
                ):
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
                    chosen = max(
                        with_quota, key=lambda s: self._model_remaining(s, model)
                    )
                elif unknown:
                    self._index = (self._index + 1) % len(unknown)
                    chosen = unknown[self._index]
                else:
                    reasons = [
                        f"[{s.index}] {self._mask_key(s.key)}: 模型 {model} 额度已用尽"
                        for s in enabled
                    ]
                    raise RuntimeError(
                        f"所有 Key 的 {model} 模型额度已用尽:\n" + "\n".join(reasons)
                    )
            else:
                known = [
                    s
                    for s in enabled
                    if any(
                        mq.get("remaining", -1) >= 0 for mq in s.model_quotas.values()
                    )
                ]
                if known:
                    chosen = max(
                        known,
                        key=lambda s: sum(
                            mq.get("remaining", 0) for mq in s.model_quotas.values()
                        ),
                    )
                else:
                    self._index = (self._index + 1) % len(enabled)
                    chosen = enabled[self._index]

            chosen.total_requests += 1
            return chosen.key, chosen.index

    @staticmethod
    def _disable(state: KeyState, reason: str) -> None:
        state.enabled = False
        state.disabled_time = time.time()
        state.disabled_reason = reason

    async def mark_key_invalid(self, key_index: int) -> None:
        """鉴权失败时停用该 Key，冷却结束后由 get_key 重新启用。"""
        async with self._lock:
            for s in self._states:
                if s.index == key_index:
                    s.failed_requests += 1
                    self._disable(s, "API Key 无效或已过期")
                    break

    async def mark_model_exhausted(self, key_index: int, model: str) -> None:
        """把该 Key 的目标模型额度记为用尽；模型对不上时不改动额度。"""
        async with self._lock:
            for s in self._states:
                if s.index != key_index:
                    continue
                quota_model = resolve_quota_model(model, s.model_quotas)
                if quota_model:
                    s.model_quotas[quota_model]["remaining"] = 0
                all_zero = all(
                    quota.get("remaining", -1) == 0 for quota in s.model_quotas.values()
                )
                if all_zero and s.model_quotas:
                    self._disable(s, "所有模型额度已用尽")
                break

    async def refresh_if_stale(
        self, fetcher: Callable[[str], Any], *, force: bool = False
    ) -> list[str]:
        """按缓存 TTL 刷新全部 Key 的额度；返回刷新失败的说明。"""
        if not force and (time.time() - self._quotas_refreshed_at) < self.QUOTA_CACHE_TTL:
            return []
        return await self.check_all_quotas(fetcher)

    async def check_all_quotas(self, fetcher: Callable[[str], Any]) -> list[str]:
        """刷新全部 Key 的模型额度；返回刷新失败的说明，由调用方记录。"""
        failures: list[str] = []
        # 先记时间戳：中途失败也不会让下一轮立刻重试
        self._quotas_refreshed_at = time.time()
        for s in self._states:
            if not s.enabled and s.disabled_time > 0:
                if time.time() - s.disabled_time < self.DISABLED_COOLDOWN:
                    continue
            try:
                result = await asyncio.wait_for(
                    fetcher(s.key), timeout=self.CHECK_TIMEOUT
                )
                model_remains = result.get("model_remains", [])
                new_quotas: dict[str, dict[str, Any]] = {}
                all_zero = True
                for m in model_remains:
                    name = m.get("model_name", "unknown")
                    quota = summarize_keypool_quota(m)
                    remaining = quota.get("remaining", -1)
                    new_quotas[name] = quota
                    if remaining == -1 or remaining > 0:
                        all_zero = False

                s.model_quotas = new_quotas
                s.quota_checked_at = time.time()

                if all_zero and new_quotas:
                    self._disable(s, "所有模型额度均已用尽")
                elif not all_zero and not s.enabled:
                    s.enabled = True
                    s.disabled_reason = ""
                    s.disabled_time = 0
            except Exception as e:
                failures.append(f"Key [{s.index}] 额度刷新失败: {e}")
        return failures
