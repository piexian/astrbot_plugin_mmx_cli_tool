"""Vision 指令图片输入提取辅助。"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from .utils import get_shared_temp_dir

logger = logging.getLogger(__name__)


def _get_temp_dir() -> str:
    return get_shared_temp_dir()


def _looks_like_remote_ref(value: Any) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(
        ("http://", "https://")
    )


def _looks_like_qq_host(host: str) -> bool:
    normalized = host.lower()
    return "qq.com" in normalized or "qpic.cn" in normalized


def _existing_local_path(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None

    lowered = candidate.lower()
    if lowered.startswith("file://"):
        parsed = urlparse(candidate)
        file_path = parsed.path or candidate[7:]
        if file_path.startswith("/") and len(file_path) > 3 and file_path[2] == ":":
            file_path = file_path[1:]
        if file_path and os.path.exists(file_path):
            return os.path.abspath(file_path)
        return None

    if os.path.exists(candidate):
        return os.path.abspath(candidate)
    return None


def _extract_inline_base64(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None

    lowered = candidate.lower()
    if lowered.startswith("base64://"):
        return "image/jpeg", candidate.removeprefix("base64://")

    if lowered.startswith("data:image/") and ";base64," in lowered:
        header, _, payload = candidate.partition(";base64,")
        mime = header.removeprefix("data:") or "image/jpeg"
        return mime, payload.strip()

    return None


def _suffix_for_mime(mime: str | None) -> str:
    ext = mimetypes.guess_extension(mime or "") or ".jpg"
    if ext == ".jpe":
        return ".jpg"
    return ext


def _write_temp_bytes(data: bytes, *, mime: str | None = None) -> str:
    suffix = _suffix_for_mime(mime)
    target = os.path.join(
        _get_temp_dir(),
        f"mmx_vision_{uuid.uuid4().hex}{suffix}",
    )
    with open(target, "wb") as f:
        f.write(data)
    return os.path.abspath(target)


def _decode_base64_to_temp(value: str, *, mime: str | None = None) -> str | None:
    try:
        raw = base64.b64decode(value, validate=False)
    except Exception:
        return None
    if not raw:
        return None
    return _write_temp_bytes(raw, mime=mime)


def _collect_component_refs(comp: Any) -> list[str]:
    refs: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        if isinstance(value, str):
            candidate = value.strip()
            if candidate and candidate not in seen:
                seen.add(candidate)
                refs.append(candidate)

    for key in ("path", "file", "base64", "url", "src", "data"):
        _add(getattr(comp, key, None))

    payload = getattr(comp, "data", None)
    if isinstance(payload, dict):
        for key in ("path", "file", "base64", "url", "src", "data"):
            _add(payload.get(key))

    return refs


def _build_ref_candidates(ref: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        if not isinstance(value, str):
            return
        candidate = value.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    _add(ref)

    if _looks_like_remote_ref(ref):
        parsed = urlparse(ref)
        query = parse_qs(parsed.query or "")
        for key in ("fileid", "file_id", "id", "image"):
            values = query.get(key) or []
            for value in values:
                _add(value)
        basename = os.path.basename(parsed.path or "")
        _add(basename)
        stem, ext = os.path.splitext(basename)
        if stem and ext:
            _add(stem)
        return candidates

    basename = os.path.basename(ref)
    if basename and basename != ref:
        _add(basename)
    stem, ext = os.path.splitext(basename or ref)
    if stem and ext:
        _add(stem)
    return candidates


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
            data = result.get("data")
            if isinstance(data, dict):
                return data
            return result
    return None


async def _download_remote_image_to_local(ref: str) -> str | None:
    try:
        try:
            from astrbot.core.utils.io import download_image_by_url

            return await download_image_by_url(ref)
        except Exception:
            pass

        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            response = await client.get(ref)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "image/jpeg")
            mime = content_type.split(";")[0].strip()
            return _write_temp_bytes(response.content, mime=mime)
    except Exception as exc:
        logger.debug("download remote image failed ref=%s err=%s", ref[:160], exc)
        return None


async def _resolve_ref_to_local(ref: str, *, event: Any | None = None) -> str | None:
    existing = _existing_local_path(ref)
    if existing:
        return existing

    inline = _extract_inline_base64(ref)
    if inline:
        mime, payload = inline
        return _decode_base64_to_temp(payload, mime=mime)

    if event is not None:
        resolved = await _resolve_onebot_image_ref(event, [ref])
        if resolved:
            return resolved

    if _looks_like_remote_ref(ref):
        return await _download_remote_image_to_local(ref)

    return None


async def _resolve_action_payload_to_local(
    data: dict[str, Any],
    *,
    event: Any | None = None,
) -> str | None:
    base64_value = data.get("base64")
    if isinstance(base64_value, str) and base64_value.strip():
        return _decode_base64_to_temp(base64_value.strip(), mime="image/jpeg")

    for key in ("file", "path"):
        resolved = _existing_local_path(data.get(key))
        if resolved:
            return resolved

    for key in ("file", "path", "url"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            resolved = await _resolve_ref_to_local(value.strip(), event=event)
            if resolved:
                return resolved

    return None


async def _resolve_onebot_image_ref(event: Any, refs: list[str]) -> str | None:
    for ref in refs:
        for candidate in _build_ref_candidates(ref):
            data = await _call_action(
                event,
                "get_image",
                [
                    {"file": candidate},
                    {"file_id": candidate},
                    {"id": candidate},
                    {"image": candidate},
                ],
            )
            if isinstance(data, dict):
                resolved = await _resolve_action_payload_to_local(data, event=event)
                if resolved:
                    return resolved

            if _looks_like_remote_ref(candidate):
                parsed = urlparse(candidate)
                if _looks_like_qq_host(parsed.netloc or ""):
                    downloaded = await _download_remote_image_to_local(candidate)
                    if downloaded:
                        return downloaded

    return None


async def _extract_quoted_message_image_refs(event: Any) -> list[str]:
    try:
        from astrbot.core.utils.quoted_message_parser import (
            extract_quoted_message_images,
        )

        refs = await extract_quoted_message_images(event)
        return [
            str(ref).strip() for ref in refs if isinstance(ref, str) and ref.strip()
        ]
    except Exception:
        return []


async def resolve_image_input_from_component(
    comp: Any,
    image_type: type[Any],
    *,
    event: Any | None = None,
) -> str | None:
    """从单个图片组件中解析出本地图片路径。"""
    if not isinstance(comp, image_type):
        return None

    refs = _collect_component_refs(comp)
    for ref in refs:
        resolved = await _resolve_ref_to_local(ref, event=event)
        if resolved:
            return resolved

    convert_to_base64 = getattr(comp, "convert_to_base64", None)
    if callable(convert_to_base64):
        try:
            raw_b64 = await convert_to_base64()
        except Exception:
            raw_b64 = None
        if isinstance(raw_b64, str) and raw_b64.strip():
            resolved = _decode_base64_to_temp(raw_b64.strip(), mime="image/jpeg")
            if resolved:
                return resolved

    return None


async def extract_image_input(
    messages: list[Any],
    *,
    image_type: type[Any],
    reply_type: type[Any],
    event: Any | None = None,
) -> tuple[str | None, bool]:
    """从当前消息链或引用消息链中提取第一张本地图片路径。"""
    images, saw_image = await extract_image_inputs(
        messages,
        image_type=image_type,
        reply_type=reply_type,
        event=event,
        limit=1,
    )
    return (images[0] if images else None), saw_image


async def extract_image_inputs(
    messages: list[Any],
    *,
    image_type: type[Any],
    reply_type: type[Any],
    event: Any | None = None,
    limit: int = 2,
) -> tuple[list[str], bool]:
    """从当前消息链或引用消息链中提取本地图片路径。"""

    max_count = max(1, limit)
    found: list[str] = []
    seen: set[str] = set()

    def _add(value: str | None) -> None:
        if value and value not in seen and len(found) < max_count:
            seen.add(value)
            found.append(value)

    async def _scan(segments: list[Any]) -> tuple[str | None, bool, bool]:
        saw_image = False
        saw_reply = False
        for seg in segments:
            if isinstance(seg, image_type):
                saw_image = True

            image_input = await resolve_image_input_from_component(
                seg,
                image_type,
                event=event,
            )
            if image_input:
                _add(image_input)
                if len(found) >= max_count:
                    return image_input, True, saw_reply

            if isinstance(seg, reply_type):
                saw_reply = True
                for attr in ("chain", "message", "origin", "content"):
                    nested = getattr(seg, attr, None)
                    if isinstance(nested, list) and nested:
                        image_input, nested_saw_image, nested_saw_reply = await _scan(
                            nested
                        )
                        saw_image = saw_image or nested_saw_image
                        saw_reply = saw_reply or nested_saw_reply
                        if image_input and len(found) >= max_count:
                            return image_input, True, saw_reply
        return (found[0] if found else None), saw_image, saw_reply

    _, saw_image, saw_reply = await _scan(list(messages or []))
    if len(found) >= max_count or not event or not saw_reply:
        return found, saw_image

    quoted_refs = await _extract_quoted_message_image_refs(event)
    if quoted_refs:
        saw_image = True
    for ref in quoted_refs:
        resolved = await _resolve_ref_to_local(ref, event=event)
        if resolved:
            _add(resolved)
            if len(found) >= max_count:
                return found, True

    return found, saw_image
