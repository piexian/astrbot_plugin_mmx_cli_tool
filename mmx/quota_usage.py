"""MiniMax Token Plan quota parsing helpers."""

from __future__ import annotations

from typing import Any


VIDEO_QUOTA_MODEL_KEYWORDS = ("video", "hailuo", "t2v", "i2v", "s2v", "视频")


def is_video_quota_model(model: object) -> bool:
    name = str(model or "").lower()
    return any(keyword in name for keyword in VIDEO_QUOTA_MODEL_KEYWORDS)


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_percent(value: Any) -> int | None:
    number = _to_int(value)
    if number is None:
        return None
    return max(0, min(100, number))


def _computed_remaining_percent(window: dict[str, int | None]) -> int | None:
    total = window.get("total")
    remaining = window.get("remaining")
    if isinstance(total, int) and total > 0 and isinstance(remaining, int):
        return max(0, min(100, int(remaining / total * 100)))
    return None


def resolve_remaining_percent(
    upstream_percent: Any,
    window: dict[str, int | None],
) -> int | None:
    """Prefer count-derived remaining percent when counts are available."""
    computed = _computed_remaining_percent(window)
    if computed is not None:
        return computed
    return _to_percent(upstream_percent)


def resolve_used_percent(window: dict[str, Any]) -> int | None:
    total = window.get("total")
    used = window.get("used")
    if isinstance(total, int) and total > 0 and isinstance(used, int):
        return max(0, min(100, int(used / total * 100)))

    remaining_percent = window.get("remaining_percent")
    if isinstance(remaining_percent, int):
        return max(0, min(100, 100 - remaining_percent))
    return None


def merge_quota_window(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key in ("total", "used", "remaining"):
        value = source.get(key)
        if isinstance(value, int):
            target[key] = (target.get(key) or 0) + value
    reset_time = source.get("remains_time")
    if isinstance(reset_time, int):
        current_reset_time = target.get("remains_time")
        target["remains_time"] = (
            min(current_reset_time, reset_time)
            if isinstance(current_reset_time, int)
            else reset_time
        )
    if source.get("unlimited") is True:
        target["unlimited"] = True


def finalize_merged_quota_window(window: dict[str, Any]) -> dict[str, Any]:
    if window.get("unlimited") is True:
        window["remaining_percent"] = 100
        return window
    total = window.get("total")
    remaining = window.get("remaining")
    if isinstance(total, int) and total > 0 and isinstance(remaining, int):
        window["remaining_percent"] = max(0, min(100, int(remaining / total * 100)))
    return window


def merge_quota_models(models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for model in models:
        name = str(model.get("model", "unknown"))
        target = merged.setdefault(name, {"current": {}, "weekly": {}})
        merge_quota_window(target["current"], model["current"])
        merge_quota_window(target["weekly"], model["weekly"])

    for model in merged.values():
        finalize_merged_quota_window(model["current"])
        finalize_merged_quota_window(model["weekly"])
    return merged


def resolve_window_quota(
    *,
    total: Any,
    used: Any,
    remaining: Any,
) -> dict[str, int | None]:
    """Resolve total/used/remaining from old and new MiniMax quota fields."""
    total_count = _to_int(total)
    used_count = _to_int(used)
    remaining_count = _to_int(remaining)

    if used_count is None and total_count is not None and remaining_count is not None:
        used_count = max(total_count - remaining_count, 0)
    if remaining_count is None and total_count is not None and used_count is not None:
        remaining_count = max(total_count - used_count, 0)

    return {
        "total": total_count,
        "used": used_count,
        "remaining": remaining_count,
    }


def is_unlimited_weekly_quota(item: dict[str, Any]) -> bool:
    """Detect MiniMax's unlimited weekly quota sentinel."""
    weekly_status = _to_int(item.get("current_weekly_status"))
    if weekly_status == 3:
        return True

    total = _to_int(item.get("current_weekly_total_count"))
    used = _to_int(item.get("current_weekly_usage_count"))
    remaining = _to_int(item.get("current_weekly_remaining_count"))
    remaining_percent = _to_percent(item.get("current_weekly_remaining_percent"))
    has_weekly_window = any(
        _to_int(item.get(key)) is not None
        for key in ("weekly_remains_time", "weekly_start_time", "weekly_end_time")
    )
    return (
        has_weekly_window
        and total == 0
        and (used is None or used == 0)
        and (remaining is None or remaining == 0)
        and remaining_percent == 100
    )


def normalize_model_quota(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize one MiniMax model_remains entry.

    mmx-cli 1.0.16 exposes both current interval and weekly quota windows.
    new-api also accounts for optional *_remaining_count fields, so this parser
    accepts those fields when present and falls back to total - used otherwise.
    """
    current = resolve_window_quota(
        total=item.get("current_interval_total_count"),
        used=item.get("current_interval_usage_count"),
        remaining=item.get("current_interval_remaining_count"),
    )
    weekly = resolve_window_quota(
        total=item.get("current_weekly_total_count"),
        used=item.get("current_weekly_usage_count"),
        remaining=item.get("current_weekly_remaining_count"),
    )
    weekly_unlimited = is_unlimited_weekly_quota(item)

    return {
        "model": item.get("model_name", "unknown"),
        "current": {
            **current,
            "remaining_percent": resolve_remaining_percent(
                item.get("current_interval_remaining_percent"),
                current,
            ),
            "status": _to_int(item.get("current_interval_status")),
            "remains_time": _to_int(item.get("remains_time")),
            "start_time": _to_int(item.get("start_time")),
            "end_time": _to_int(item.get("end_time")),
        },
        "weekly": {
            **weekly,
            "remaining_percent": resolve_remaining_percent(
                item.get("current_weekly_remaining_percent"),
                weekly,
            )
            if not weekly_unlimited
            else 100,
            "status": _to_int(item.get("current_weekly_status")),
            "remains_time": _to_int(item.get("weekly_remains_time")),
            "start_time": _to_int(item.get("weekly_start_time")),
            "end_time": _to_int(item.get("weekly_end_time")),
            "unlimited": weekly_unlimited,
        },
        "raw": item,
    }


def normalize_quota_models(model_remains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize a MiniMax model_remains list."""
    return [normalize_model_quota(m) for m in model_remains]


def summarize_keypool_quota(item: dict[str, Any]) -> dict[str, int | bool | None]:
    """Return the compact quota shape used by KeyPool scheduling."""
    normalized = normalize_model_quota(item)
    current = normalized["current"]
    weekly = normalized["weekly"]
    remaining = current["remaining"]
    if remaining is None:
        remaining = weekly["remaining"]
    if remaining is None:
        remaining = -1

    return {
        "total": current["total"],
        "used": current["used"],
        "remaining": remaining,
        "weekly_total": weekly["total"],
        "weekly_used": weekly["used"],
        "weekly_remaining": weekly["remaining"],
        "weekly_unlimited": weekly["unlimited"],
    }
