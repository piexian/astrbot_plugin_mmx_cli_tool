"""Vision 指令图片输入提取辅助。"""

from __future__ import annotations

from typing import Any


async def resolve_image_input_from_component(
    comp: Any,
    image_type: type[Any],
) -> str | None:
    """从单个图片组件中解析出可供 VisionAPI 使用的输入。"""
    if isinstance(comp, image_type):
        try:
            return await comp.convert_to_file_path()
        except Exception:
            pass

        for key in ("path", "url", "file", "src", "base64", "data"):
            value = getattr(comp, key, None)
            if isinstance(value, str) and value:
                return value

        payload = getattr(comp, "data", None)
        if isinstance(payload, dict):
            for key in ("path", "url", "file", "src", "base64", "data"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value

    return None


async def extract_image_input(
    messages: list[Any],
    *,
    image_type: type[Any],
    reply_type: type[Any],
) -> str | None:
    """从当前消息链或引用消息链中提取第一张图片输入。"""

    async def _scan(segments: list[Any]) -> str | None:
        for seg in segments:
            image_input = await resolve_image_input_from_component(seg, image_type)
            if image_input:
                return image_input

            if isinstance(seg, reply_type):
                for attr in ("chain", "message", "origin", "content"):
                    nested = getattr(seg, attr, None)
                    if isinstance(nested, list) and nested:
                        image_input = await _scan(nested)
                        if image_input:
                            return image_input
        return None

    return await _scan(list(messages or []))
