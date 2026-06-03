"""MiniMax Token Plan quota parsing helpers."""

from __future__ import annotations

from typing import Any


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
            "remaining_percent": _to_percent(
                item.get("current_interval_remaining_percent")
            ),
            "status": _to_int(item.get("current_interval_status")),
            "remains_time": _to_int(item.get("remains_time")),
            "start_time": _to_int(item.get("start_time")),
            "end_time": _to_int(item.get("end_time")),
        },
        "weekly": {
            **weekly,
            "remaining_percent": _to_percent(
                item.get("current_weekly_remaining_percent")
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
