"""Helpers for resolving media attachments from AstrBot message chains."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import unquote, urlparse


_AUDIO_EXTENSIONS = {
    ".aac",
    ".amr",
    ".flac",
    ".m4a",
    ".mp3",
    ".oga",
    ".ogg",
    ".opus",
    ".pcm",
    ".wav",
}


async def extract_first_audio_input(
    messages: list[Any],
    *,
    record_type: type[Any],
    file_type: type[Any],
    reply_type: type[Any],
    event: Any | None = None,
) -> tuple[str | None, bool]:
    """Return the first audio-like attachment from current or quoted messages."""
    value, saw_attachment = await _extract_first_media_input(
        messages,
        media_types=(record_type, file_type),
        reply_type=reply_type,
        event=event,
    )
    return value, saw_attachment


async def _extract_first_media_input(
    messages: list[Any],
    *,
    media_types: tuple[type[Any], ...],
    reply_type: type[Any],
    event: Any | None = None,
) -> tuple[str | None, bool]:
    saw_attachment = False
    reply_ids: list[str] = []

    async def _scan(segments: list[Any]) -> str | None:
        nonlocal saw_attachment
        for seg in segments:
            if isinstance(seg, media_types):
                saw_attachment = True
                resolved = await _resolve_component_file(seg)
                if resolved:
                    return resolved

            if isinstance(seg, reply_type):
                reply_id = getattr(seg, "id", None)
                if reply_id is not None:
                    reply_ids.append(str(reply_id))
                for attr in ("chain", "message", "origin", "content"):
                    nested = getattr(seg, attr, None)
                    if isinstance(nested, list) and nested:
                        resolved = await _scan(nested)
                        if resolved:
                            return resolved
        return None

    resolved = await _scan(list(messages or []))
    if resolved:
        return resolved, saw_attachment

    if event is not None:
        for reply_id in reply_ids:
            resolved = await _resolve_quoted_audio(event, reply_id)
            if resolved:
                return resolved, True

    return None, saw_attachment


async def _resolve_component_file(comp: Any) -> str | None:
    get_file = getattr(comp, "get_file", None)
    if callable(get_file):
        try:
            resolved = await get_file(allow_return_url=True)
        except TypeError:
            resolved = await get_file()
        except Exception:
            resolved = None
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()

    convert_to_file_path = getattr(comp, "convert_to_file_path", None)
    if callable(convert_to_file_path):
        try:
            resolved = await convert_to_file_path()
        except Exception:
            resolved = None
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()

    for attr in ("path", "file", "file_", "url"):
        value = getattr(comp, attr, None)
        if isinstance(value, str) and value.strip():
            return _strip_file_scheme(value.strip())

    payload = getattr(comp, "data", None)
    if isinstance(payload, dict):
        for key in ("path", "file", "file_", "url"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return _strip_file_scheme(value.strip())

    return None


def _strip_file_scheme(value: str) -> str:
    if value.startswith("file://"):
        parsed = urlparse(value)
        path = unquote(parsed.path or value[7:])
        if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        return path
    return value


async def _resolve_quoted_audio(event: Any, reply_id: str) -> str | None:
    payload = await _call_action(
        event,
        "get_msg",
        [{"message_id": reply_id}, {"id": reply_id}],
    )
    data = _unwrap_payload(payload)
    segments = data.get("message") or data.get("messages")
    if not isinstance(segments, list):
        return None
    return await _resolve_onebot_audio_segments(event, segments)


async def _resolve_onebot_audio_segments(
    event: Any,
    segments: list[Any],
) -> str | None:
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        seg_type = str(seg.get("type") or "").lower()
        data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
        if seg_type in {"record", "voice", "audio"}:
            resolved = await _resolve_onebot_media_data(event, data)
            if resolved:
                return resolved
        if seg_type == "file" and _looks_like_audio_file(data):
            resolved = await _resolve_onebot_media_data(event, data)
            if resolved:
                return resolved
    return None


async def _resolve_onebot_media_data(event: Any, data: dict[str, Any]) -> str | None:
    for key in ("url", "path"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _strip_file_scheme(value.strip())

    file_value = data.get("file")
    if isinstance(file_value, str) and file_value.strip():
        candidate = _strip_file_scheme(file_value.strip())
        if (
            candidate.startswith(("http://", "https://"))
            or os.path.exists(candidate)
        ):
            return candidate
        resolved = await _resolve_onebot_file_ref(event, candidate)
        if resolved:
            return resolved

    for key in ("file_id", "id"):
        value = data.get(key)
        if isinstance(value, (str, int)) and str(value).strip():
            resolved = await _resolve_onebot_file_ref(event, str(value).strip())
            if resolved:
                return resolved
    return None


async def _resolve_onebot_file_ref(event: Any, file_ref: str) -> str | None:
    params: list[tuple[str, list[dict[str, Any]]]] = [
        ("get_file", [{"file_id": file_ref}, {"file": file_ref}]),
    ]
    try:
        group_id = event.get_group_id()
    except Exception:
        group_id = None
    if group_id:
        group_id_value: str | int = int(group_id) if str(group_id).isdigit() else group_id
        params.append(
            (
                "get_group_file_url",
                [{"group_id": group_id_value, "file_id": file_ref}],
            )
        )
    params.append(("get_private_file_url", [{"file_id": file_ref}]))

    for action, param_list in params:
        payload = await _call_action(event, action, param_list)
        data = _unwrap_payload(payload)
        for key in ("url", "file", "path"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return _strip_file_scheme(value.strip())
    return None


async def _call_action(
    event: Any,
    action: str,
    params_list: list[dict[str, Any]],
) -> Any:
    bot = getattr(event, "bot", None)
    api = getattr(bot, "api", None)
    call_action = getattr(api, "call_action", None)
    if not callable(call_action):
        return None

    for params in params_list:
        try:
            result = await call_action(action, **params)
        except Exception:
            continue
        if isinstance(result, dict):
            return result
    return None


def _unwrap_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _looks_like_audio_file(data: dict[str, Any]) -> bool:
    for key in ("name", "file_name", "file", "url", "path"):
        value = data.get(key)
        if not isinstance(value, str):
            continue
        _, ext = os.path.splitext(value.lower())
        if ext in _AUDIO_EXTENSIONS:
            return True
    return False
