"""MiniMax 工具共享实用函数。"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import shlex
from pathlib import Path


def split_command_tokens(text: str) -> list[str]:
    """按 shell 语义切分命令参数，仅将双引号视为引用符。

    标准 ``shlex.split`` 在 POSIX 模式下会把单引号当作成对引用符，
    导致 prompt 中常见的英文撇号（如 ``cat's``、``rock 'n' roll``）触发
    ``ValueError: No closing quotation``。这里禁用单引号引用，让撇号可
    自由出现在提示词中，同时保留双引号包裹带空格参数的能力。
    """
    lexer = shlex.shlex(text, posix=True)
    lexer.whitespace_split = True
    lexer.quotes = '"'
    lexer.commenters = ""
    return list(lexer)


def is_safe_data_path(base_dir: str, path: str) -> bool:
    """校验 ``path`` 解析后是否位于 ``base_dir`` 之内（防止路径穿越）。

    用于约束由 LLM 工具参数或外部输入提供的本地文件路径，拒绝绝对路径
    逃逸与 ``..`` 段穿越，避免读取受信任数据目录之外的任意宿主文件。
    """
    base = Path(base_dir).resolve()
    target = (base / path).resolve()
    return target == base or base in target.parents


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
