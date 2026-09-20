"""受限音频输入与转写结果保存。"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from urllib.parse import unquote, urlparse

import httpx

from .apis.transcription import (
    DEFAULT_TRANSCRIPTION_MODEL,
    MAX_AUDIO_BYTES,
    SpeechToTextAPI,
    TranscriptionOptions,
    validate_audio_size,
)
from .utils import is_url, resolve_data_path, resolve_local_input_path


class TranscriptionService:
    """共用输入边界、附件下载上限及输出策略，不自动重试计费请求。"""

    def __init__(
        self,
        api: SpeechToTextAPI,
        data_dir: str,
        cache_dir: str,
        *,
        extra_allowed_dirs: list[str] | None = None,
        default_model: str = DEFAULT_TRANSCRIPTION_MODEL,
    ) -> None:
        self._api = api
        self._data_dir = data_dir
        self._cache_dir = cache_dir
        self._extra_dirs = extra_allowed_dirs
        self.default_model = default_model or DEFAULT_TRANSCRIPTION_MODEL

    def output_path(self, out: str | None) -> Path | None:
        if out is None:
            return None
        if not isinstance(out, str) or not out.strip():
            raise ValueError("out 必须为插件数据目录内的相对文件路径")
        if Path(out).is_absolute() or ":" in out or ".." in Path(out).parts:
            raise ValueError("out 不允许绝对路径、URI 或 .. 穿越")
        target = resolve_data_path(self._data_dir, out)
        if target is None or target.is_dir():
            raise ValueError("out 必须为插件数据目录内的文件路径")
        return target

    def validate(self, options: TranscriptionOptions, out: str | None) -> Path | None:
        options.validate()
        if options.stream and out is not None:
            raise ValueError("stream 与 out 不能同时使用")
        return self.output_path(out)

    @staticmethod
    def _read_audio(path: Path) -> bytes:
        with path.open("rb") as handle:
            validate_audio_size(os.fstat(handle.fileno()).st_size)
            audio = handle.read(MAX_AUDIO_BYTES + 1)
        validate_audio_size(len(audio))
        return audio

    async def _download_attachment(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("音频附件 URL 无效")
        # 仅平台解析的附件可进入此分支，不复用带 MiniMax 鉴权的客户端。
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, max_redirects=3) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length is not None and length.isdigit() and int(length) > MAX_AUDIO_BYTES:
                    raise ValueError("音频超过 50 MB 限制，请压缩或分段后提交")
                data = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    if len(data) + len(chunk) > MAX_AUDIO_BYTES:
                        raise ValueError("音频超过 50 MB 限制，请压缩或分段后提交")
                    data.extend(chunk)
                validate_audio_size(len(data))
                return bytes(data)

    async def run(
        self,
        file: str | None,
        options: TranscriptionOptions,
        *,
        trusted_attachment: bool = False,
        out: str | None = None,
    ) -> dict:
        target = self.validate(options, out)
        if not isinstance(file, str) or not file.strip():
            raise ValueError("请提供 file，或附带/引用一个音频文件")
        if is_url(file):
            if not trusted_attachment:
                raise ValueError("file 仅支持允许目录内的本地音频；远端音频请作为聊天附件发送")
            audio = await asyncio.wait_for(self._download_attachment(file), timeout=60)
            filename = Path(unquote(urlparse(file).path)).name or "audio"
        else:
            path = resolve_local_input_path(
                file,
                data_dir=None if trusted_attachment else self._data_dir,
                allow_trusted_local_path=trusted_attachment,
                extra_allowed_dirs=self._extra_dirs,
                label="音频文件",
            )
            audio = await asyncio.to_thread(self._read_audio, path)
            filename = path.name
        result = await self._api.transcribe(audio, filename, options)
        payload = {"ok": True, "response_format": options.response_format, "data": result}
        large_text = isinstance(result, dict) and len(result.get("text", "")) > 3500
        if target is None and options.response_format == "json" and not large_text:
            return payload
        if target is None:
            suffix = options.response_format if options.response_format in ("srt", "vtt") else "json"
            target = Path(self._cache_dir) / f"mmx_transcript_{uuid.uuid4().hex}.{suffix}"
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2)
        try:
            await asyncio.to_thread(self._save, target, content)
            payload["file_path"] = str(target)
        except OSError:
            payload["warning"] = "转写已完成，但保存文件失败；已保留文字结果，请勿重复提交计费请求。"
        return payload

    @staticmethod
    def _save(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode("utf-8"))
