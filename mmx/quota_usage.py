"""MiniMax Token Plan quota parsing and display helpers."""

from __future__ import annotations

import math
from typing import Any

VIDEO_QUOTA_MODEL_KEYWORDS = ("video", "hailuo", "t2v", "i2v", "s2v", "视频")


def is_video_quota_model(model: object) -> bool:
    return any(word in str(model or "").lower() for word in VIDEO_QUOTA_MODEL_KEYWORDS)


def _to_number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value: Any) -> int | None:
    number = _to_number(value)
    return int(number) if number is not None and number.is_integer() else None


def _to_percent(value: Any) -> float | None:
    number = _to_number(value)
    return max(0, min(100, number)) if number is not None else None


def resolve_window_quota(
    *, total: Any, used: Any, remaining: Any, remaining_percent: Any = None
) -> dict[str, int | None]:
    """Resolve explicit counts or infer legacy usage_count semantics from percent."""
    total_count = _to_int(total)
    reported = _to_int(used)
    explicit = _to_int(remaining)
    percent = _to_percent(remaining_percent)
    result = {"total": total_count, "used": None, "remaining": None}
    if total_count is None or total_count < 0:
        result["total"] = None
        return result
    if remaining is not None:
        if explicit is None or not 0 <= explicit <= total_count:
            return result
        count = explicit
    else:
        if reported is None or not 0 <= reported <= total_count:
            return result
        count = reported  # Without a percentage, retain the CLI's legacy meaning.
        if percent is not None and total_count > 0:
            as_remaining = abs(reported / total_count * 100 - percent)
            as_used = abs((total_count - reported) / total_count * 100 - percent)
            if min(as_remaining, as_used) > 1:
                return result
            if as_used < as_remaining:
                count = total_count - reported
    result.update(used=total_count - count, remaining=count)
    return result


def resolve_remaining_percent(upstream_percent: Any, window: dict) -> float | None:
    """Use the server percentage before falling back to unambiguous counts."""
    percent = _to_percent(upstream_percent)
    if percent is not None:
        return percent
    total, remaining = window.get("total"), window.get("remaining")
    if isinstance(total, int) and total > 0 and isinstance(remaining, int):
        return max(0, min(100, remaining / total * 100))
    return None


def resolve_used_percent(window: dict[str, Any]) -> int | None:
    remaining = resolve_remaining_percent(window.get("remaining_percent"), window)
    return round(100 - remaining) if remaining is not None else None


def is_unavailable_plan(item: dict[str, Any]) -> bool:
    return all(
        _to_int(item.get(key)) == value
        for key, value in (
            ("current_interval_total_count", 0), ("current_weekly_total_count", 0),
            ("current_interval_status", 3), ("current_weekly_status", 3),
        )
    )


def is_unlimited_weekly_quota(item: dict[str, Any]) -> bool:
    if is_unavailable_plan(item):
        return False
    if _to_int(item.get("current_weekly_status")) == 3:
        return True
    return (
        _to_int(item.get("current_weekly_total_count")) == 0
        and _to_int(item.get("current_weekly_usage_count")) in (None, 0)
        and _to_int(item.get("current_weekly_remaining_count")) in (None, 0)
        and _to_percent(item.get("current_weekly_remaining_percent")) == 100
        and any(_to_int(item.get(k)) is not None for k in (
            "weekly_remains_time", "weekly_start_time", "weekly_end_time"
        ))
    )


def normalize_model_quota(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize counts, plan availability, time windows and weekly boost."""
    unavailable = is_unavailable_plan(item)
    result: dict[str, Any] = {"model": item.get("model_name", "unknown"), "raw": item}
    for name, prefix, time_prefix in (
        ("current", "current_interval", ""), ("weekly", "current_weekly", "weekly_")
    ):
        percent = item.get(f"{prefix}_remaining_percent")
        window: dict[str, Any] = resolve_window_quota(
            total=item.get(f"{prefix}_total_count"),
            used=item.get(f"{prefix}_usage_count"),
            remaining=item.get(f"{prefix}_remaining_count"),
            remaining_percent=percent,
        )
        window.update(
            remaining_percent=resolve_remaining_percent(percent, window),
            status=_to_int(item.get(f"{prefix}_status")),
            remains_time=_to_int(item.get(f"{time_prefix}remains_time")),
            start_time=_to_int(item.get(f"{time_prefix}start_time")),
            end_time=_to_int(item.get(f"{time_prefix}end_time")),
            unavailable=unavailable,
            unlimited=name == "weekly" and is_unlimited_weekly_quota(item),
        )
        boost = _to_number(item.get("weekly_boost_permille")) if name == "weekly" else None
        window["boosted"] = boost is not None and boost != 1000
        base_percent = window["remaining_percent"]
        window["boosted_remaining_percent"] = (
            max(0, min(200, base_percent * max(0, boost if boost is not None else 1000) / 1000))
            if base_percent is not None else None
        )
        result[name] = window
    return result


def normalize_quota_models(model_remains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_model_quota(model) for model in model_remains]


def merge_quota_window(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge complete counts and weighted percentages without inventing missing data."""
    if not target or target.get("unavailable"):
        target.clear()
        target.update(source)
        return
    if source.get("unavailable"):
        return
    old_total, new_total = target.get("total"), source.get("total")
    for key in ("remaining_percent", "boosted_remaining_percent"):
        left, right = target.get(key), source.get(key)
        if (left is not None and right is not None
                and isinstance(old_total, int) and old_total > 0
                and isinstance(new_total, int) and new_total > 0):
            target[key] = (left * old_total + right * new_total) / (old_total + new_total)
        else:
            target[key] = left if left == right else None
    for key in ("total", "used", "remaining"):
        left, right = target.get(key), source.get(key)
        target[key] = left + right if isinstance(left, int) and isinstance(right, int) else None
    for key in ("start_time", "end_time"):
        if target.get(key) != source.get(key):
            target[key] = None
    resets = [v for v in (target.get("remains_time"), source.get("remains_time")) if isinstance(v, int)]
    target["remains_time"] = min(resets) if resets else None
    target["unlimited"] = bool(target.get("unlimited") or source.get("unlimited"))
    target["boosted"] = bool(target.get("boosted") or source.get("boosted"))


def finalize_merged_quota_window(window: dict[str, Any]) -> dict[str, Any]:
    if window.get("unlimited"):
        window["remaining_percent"] = 100
    else:
        window["remaining_percent"] = resolve_remaining_percent(window.get("remaining_percent"), window)
    return window


def merge_quota_models(models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for model in models:
        target = merged.setdefault(str(model.get("model", "unknown")), {"current": {}, "weekly": {}})
        for name in ("current", "weekly"):
            merge_quota_window(target[name], model[name])
    for model in merged.values():
        for window in model.values():
            finalize_merged_quota_window(window)
    return merged


def summarize_keypool_quota(item: dict[str, Any]) -> dict[str, int | bool | None]:
    normalized = normalize_model_quota(item)
    current, weekly = normalized["current"], normalized["weekly"]
    remaining = current["remaining"]
    if current["unavailable"]:
        remaining = 0
    elif remaining is None and current["total"] is None:
        remaining = weekly["remaining"]
    return {
        "total": current["total"], "used": current["used"],
        "remaining": remaining if remaining is not None else -1,
        "weekly_total": weekly["total"], "weekly_used": weekly["used"],
        "weekly_remaining": weekly["remaining"], "weekly_unlimited": weekly["unlimited"],
    }


def format_quota_reset_time(value: object) -> str | None:
    if not isinstance(value, int):
        return None
    if value <= 0:
        return "即将"
    if value < 60000:
        return f"{max(1, value // 1000)}秒"
    hours, minutes = divmod(value // 60000, 60)
    return (f"{hours}小时" if hours else "") + (f"{minutes}分钟" if minutes or not hours else "")


def quota_window_label(window: dict) -> str:
    start, end = window.get("start_time"), window.get("end_time")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        return "当前周期额度"
    ms = end - start
    for unit, size in (("周", 604800000), ("天", 86400000), ("小时", 3600000), ("分钟", 60000)):
        if ms % size == 0:
            return f"{ms // size}{unit}额度"
    return "当前周期额度"


def format_quota_usage(window: dict, *, is_video: bool = False) -> str:
    if window.get("unavailable"):
        return "不在当前套餐中"
    if window.get("unlimited"):
        return "∞"
    if is_video:
        counts = [window.get(key) for key in ("used", "remaining", "total")]
        if not any(isinstance(value, int) for value in counts):
            return "未知"
        text = " / ".join(str(v) if isinstance(v, int) else "未知" for v in counts[:2])
        text += f"（{counts[2] if isinstance(counts[2], int) else '未知'}）"
    else:
        percent = resolve_used_percent(window)
        text = f"已用{percent}%" if percent is not None else "未知"
    boosted = window.get("boosted_remaining_percent")
    if window.get("boosted") and boosted is not None:
        text += f"（加成后剩余{round(boosted)}%）"
    return text
