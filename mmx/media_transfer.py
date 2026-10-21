"""Matrix 媒体传输底座 — ≤600KB 内联 Base64 / >600KB OSS 预签名直传，流式下载。

对齐 @mavis/agent-tools desktop matrix-media-client：
- 内联上限 600KB，上传上限 500MB；
- 大文件走 get_upload_url 预签名 PUT，返回 cdn_url；
- 下载目标路径必须位于允许目录内，失败抛 MediaTransferError。
"""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from .matrix import MatrixAPI

INLINE_LIMIT_BYTES = 600 * 1024
MAX_UPLOAD_BYTES = 500 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".pcm": "audio/L16",
    ".mp4": "video/mp4",
}

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_-]")


class MediaTransferError(Exception):
    """媒体传输失败，kind 区分原因（file_not_found/file_too_large/upload_failed/download_failed/path_not_allowed）。"""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass
class UploadResult:
    """上传结果：inline_data 与 cdn_url 二选一。"""

    bytes: int
    mime_type: str
    oss_key: str = ""
    cdn_url: str = ""
    inline_data: str = ""


def guess_mime_type(filename: str) -> str:
    """按扩展名推断 MIME 类型。"""
    return _MIME_BY_EXT.get(Path(filename).suffix.lower(), "application/octet-stream")


def sanitize_filename(name: str) -> str:
    """清洗文件名，仅保留字母数字与 _-（对齐上游 sanitizeFilename）。"""
    if not name:
        return "file"
    stem, dot, ext = name.rpartition(".")
    if not dot or name.startswith(".") and not stem:
        cleaned = _UNSAFE_CHARS.sub("_", name)
        return cleaned or "file"
    stem = _UNSAFE_CHARS.sub("_", stem) or "file"
    ext = _UNSAFE_CHARS.sub("_", ext) or "bin"
    return f"{stem}.{ext}"


class MatrixMediaClient:
    """Matrix 媒体上传与下载。"""

    def __init__(self, api: MatrixAPI, http: httpx.AsyncClient | None = None) -> None:
        self._api = api
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(300), follow_redirects=True)
        self._owns_http = http is None

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def upload_file(self, path: str | Path, *, force_remote_url: bool = False) -> UploadResult:
        """上传本地文件：≤600KB 内联 Base64，否则 OSS 预签名直传。"""
        target = Path(path)
        try:
            size = target.stat().st_size
        except OSError as exc:
            raise MediaTransferError("file_not_found", f"输入文件不存在: {path}") from exc
        if not target.is_file():
            raise MediaTransferError("file_not_found", f"输入路径不是文件: {path}")
        if size > MAX_UPLOAD_BYTES:
            raise MediaTransferError(
                "file_too_large",
                f"文件 {size / 1024 / 1024:.1f} MiB 超过 Matrix 上传上限 500 MiB",
            )
        filename = sanitize_filename(target.name)
        mime_type = guess_mime_type(filename)

        if not force_remote_url and size <= INLINE_LIMIT_BYTES:
            raw = await asyncio.to_thread(target.read_bytes)
            return UploadResult(
                bytes=size,
                mime_type=mime_type,
                inline_data=base64.b64encode(raw).decode("ascii"),
            )

        presigned = await self._api.get_upload_url(filename, mime_type, size)
        put_url = str(presigned["put_url"])
        cdn_url = str(presigned["cdn_url"])
        content_type = str(presigned.get("content_type") or mime_type)

        async def _read_chunks():
            with target.open("rb") as fp:
                while True:
                    chunk = await asyncio.to_thread(fp.read, 1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        try:
            res = await self._http.put(
                put_url,
                content=_read_chunks(),
                headers={"Content-Type": content_type, "Content-Length": str(size)},
            )
        except httpx.HTTPError as exc:
            raise MediaTransferError("upload_failed", f"OSS 直传失败: {exc}") from exc
        if not res.is_success:
            raise MediaTransferError("upload_failed", f"OSS 直传失败: HTTP {res.status_code}")
        return UploadResult(
            bytes=size,
            mime_type=content_type,
            oss_key=str(presigned.get("oss_key") or ""),
            cdn_url=cdn_url,
        )

    async def download_to_file(
        self,
        url: str,
        dest: str | Path,
        *,
        allowed_dir: str | Path,
        timeout: float = 300,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> Path:
        """流式下载到允许目录内的目标路径；超限或失败时清理半成品文件。"""
        base = Path(allowed_dir).resolve()
        target = Path(dest).resolve()
        if target != base and base not in target.parents:
            raise MediaTransferError(
                "path_not_allowed", f"下载目标必须位于允许目录内: {allowed_dir}"
            )
        started = False
        try:
            async with self._http.stream("GET", url, timeout=httpx.Timeout(timeout)) as res:
                if not res.is_success:
                    raise MediaTransferError(
                        "download_failed", f"下载失败: HTTP {res.status_code}"
                    )
                content_length = res.headers.get("content-length")
                if content_length and int(content_length) > max_bytes:
                    raise MediaTransferError(
                        "file_too_large",
                        f"文件超过下载上限 {max_bytes // 1024 // 1024} MiB",
                    )
                await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
                written = 0
                with target.open("wb") as fp:
                    started = True
                    async for chunk in res.aiter_bytes(1024 * 256):
                        written += len(chunk)
                        if written > max_bytes:
                            raise MediaTransferError(
                                "file_too_large",
                                f"文件超过下载上限 {max_bytes // 1024 // 1024} MiB",
                            )
                        await asyncio.to_thread(fp.write, chunk)
        except MediaTransferError:
            if started:
                await asyncio.to_thread(target.unlink, missing_ok=True)
            raise
        except (httpx.HTTPError, OSError) as exc:
            if started:
                await asyncio.to_thread(target.unlink, missing_ok=True)
            raise MediaTransferError("download_failed", f"下载失败: {exc}") from exc
        return target
