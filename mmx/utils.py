"""MiniMax 工具共享实用函数。"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path


def is_url(s: str) -> bool:
    """判断字符串是否为 HTTP(S) URL。"""
    return s.startswith("http://") or s.startswith("https://")


async def resolve_image(image: str) -> str:
    """将图片路径或 URL 统一处理（对齐 mmx-cli Rn 函数）。

    - URL → 原样返回
    - data: URI → 原样返回
    - 本地文件路径 → 读取并转为 data:image/...;base64,... 格式
    """
    if image.startswith("data:"):
        return image

    if image.startswith("base64://"):
        return f"data:image/jpeg;base64,{image.removeprefix('base64://')}"

    # 去除 file:// 前缀
    if image.startswith("file:///"):
        image = image[8:]
    elif image.startswith("file://"):
        image = image[7:]

    # URL 原样返回
    if is_url(image):
        return image

    # 本地文件 → Data URI
    p = Path(image)
    if not p.is_file():
        raise FileNotFoundError(f"图片文件不存在: {image}")

    mime, _ = mimetypes.guess_type(p.name)
    if not mime:
        mime = "image/jpeg"

    raw = await asyncio.to_thread(p.read_bytes)
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


async def resolve_subject_reference(subject_ref: str) -> list[dict[str, str]]:
    """Build MiniMax subject_reference from mmx-cli-style subject-ref input."""
    params = _parse_subject_ref_params(subject_ref)
    ref_type = params.get("type") or "character"
    image = params.get("image") or subject_ref
    item: dict[str, str] = {"type": ref_type}
    if is_url(image):
        item["image_url"] = image
    else:
        item["image_file"] = await resolve_image(image)
    return [item]


def _parse_subject_ref_params(value: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in value.split(","):
        key, sep, raw = part.partition("=")
        if sep:
            params[key.strip()] = raw.strip()
    return params
