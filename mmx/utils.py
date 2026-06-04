"""MiniMax 工具共享实用函数。"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import shlex
from pathlib import Path
from urllib.parse import unquote, urlparse


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
    return resolve_data_path(base_dir, path) is not None


def resolve_data_path(base_dir: str, path: str) -> Path | None:
    """Resolve ``path`` only when it stays inside ``base_dir``."""
    base = Path(base_dir).resolve()
    target = (base / _strip_file_scheme(path)).resolve()
    if target == base or base in target.parents:
        return target
    return None


def resolve_local_input_path(
    path: str,
    *,
    data_dir: str | None = None,
    allow_trusted_local_path: bool = False,
    label: str = "文件",
) -> Path:
    """Resolve a local input file without allowing untrusted host file reads."""
    if data_dir is not None:
        target = resolve_data_path(data_dir, path)
        if target is None:
            raise ValueError(f"{label} 必须位于插件数据目录内，不允许绝对路径或 .. 穿越")
    elif allow_trusted_local_path:
        target = Path(_strip_file_scheme(path)).resolve()
    else:
        raise ValueError(f"{label} 不允许读取插件数据目录之外的本地路径")

    if not target.is_file():
        raise FileNotFoundError(f"{label}不存在: {path}")
    return target


def _strip_file_scheme(path: str) -> str:
    value = str(path).strip()
    if value.startswith("file://"):
        parsed = urlparse(value)
        local_path = unquote(parsed.path)
        if parsed.netloc and parsed.netloc != "localhost":
            return f"//{parsed.netloc}{local_path}"
        return local_path
    return value


def is_url(s: str) -> bool:
    """判断字符串是否为 HTTP(S) URL。"""
    return s.startswith("http://") or s.startswith("https://")


async def resolve_image(
    image: str,
    *,
    data_dir: str | None = None,
    allow_trusted_local_path: bool = False,
) -> str:
    """将图片路径或 URL 统一处理（对齐 mmx-cli Rn 函数）。

    - URL → 原样返回
    - data: URI → 原样返回
    - 本地文件路径 → 读取并转为 data:image/...;base64,... 格式
    """
    if image.startswith("data:"):
        return image

    if image.startswith("base64://"):
        return f"data:image/jpeg;base64,{image.removeprefix('base64://')}"

    # URL 原样返回
    if is_url(image):
        return image

    # 本地文件 → Data URI
    p = resolve_local_input_path(
        image,
        data_dir=data_dir,
        allow_trusted_local_path=allow_trusted_local_path,
        label="图片文件",
    )

    mime, _ = mimetypes.guess_type(p.name)
    if not mime:
        mime = "image/jpeg"

    raw = await asyncio.to_thread(p.read_bytes)
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


async def resolve_subject_reference(
    subject_ref: str,
    *,
    data_dir: str | None = None,
    allow_trusted_local_path: bool = False,
) -> list[dict[str, str]]:
    """Build MiniMax subject_reference from mmx-cli-style subject-ref input."""
    params = _parse_subject_ref_params(subject_ref)
    ref_type = params.get("type") or "character"
    image = params.get("image") or subject_ref
    item: dict[str, str] = {"type": ref_type}
    if is_url(image):
        item["image_url"] = image
    else:
        item["image_file"] = await resolve_image(
            image,
            data_dir=data_dir,
            allow_trusted_local_path=allow_trusted_local_path,
        )
    return [item]


def _parse_subject_ref_params(value: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for part in value.split(","):
        key, sep, raw = part.partition("=")
        if sep:
            params[key.strip()] = raw.strip()
    return params
