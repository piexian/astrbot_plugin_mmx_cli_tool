"""MiniMax 音乐生成 API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..client import MiniMaxClient
from ..endpoints import music_endpoint
from ..utils import resolve_local_input_path


class MusicAPI:
    """MiniMax 音乐生成接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def generate(
        self,
        *,
        model: str | None = None,
        prompt: str | None = None,
        lyrics: str | None = None,
        is_instrumental: bool = False,
        lyrics_optimizer: bool = False,
        vocals: str | None = None,
        genre: str | None = None,
        mood: str | None = None,
        instruments: str | None = None,
        tempo: str | None = None,
        bpm: int | None = None,
        key: str | None = None,
        avoid: str | None = None,
        use_case: str | None = None,
        structure: str | None = None,
        references: str | None = None,
        extra: str | None = None,
        output_format: str = "hex",
        audio_format: str = "mp3",
        sample_rate: int = 44100,
        bitrate: int = 256000,
        aigc_watermark: bool = False,
    ) -> dict[str, Any]:
        """生成音乐。对齐 mmx-cli TS SDK 的请求格式。"""
        body: dict[str, Any] = {
            "output_format": output_format,
            "audio_setting": {
                "format": audio_format,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
            },
        }
        if model:
            body["model"] = model

        # 按 TS SDK buildPrompt 逻辑构建结构化 prompt
        structured: list[str] = []
        if vocals:
            structured.append(f"Vocals: {vocals}")
        if genre:
            structured.append(f"Genre: {genre}")
        if mood:
            structured.append(f"Mood: {mood}")
        if instruments:
            structured.append(f"Instruments: {instruments}")
        if tempo:
            structured.append(f"Tempo: {tempo}")
        if key:
            structured.append(f"Key: {key}")
        if bpm:
            structured.append(f"BPM: {bpm}")
        if avoid:
            structured.append(f"Avoid: {avoid}")
        if use_case:
            structured.append(f"Use case: {use_case}")
        if structure:
            structured.append(f"Structure: {structure}")
        if references:
            structured.append(f"References: {references}")
        if extra:
            structured.append(f"Extra: {extra}")

        if is_instrumental:
            body["is_instrumental"] = True
        elif lyrics_optimizer:
            body["lyrics_optimizer"] = True
        elif lyrics:
            body["lyrics"] = lyrics
        if aigc_watermark:
            body["aigc_watermark"] = True

        # 拼接最终 prompt
        if structured:
            final_prompt = ". ".join(structured)
            if prompt:
                final_prompt = f"{prompt}. {final_prompt}"
        else:
            final_prompt = prompt or ""

        if final_prompt:
            body["prompt"] = final_prompt

        return await self._client.request_json(
            "POST",
            music_endpoint(self._client.base_url),
            body=body,
        )

    def save(self, response: dict[str, Any], out_path: str) -> str:
        """将响应中的音频（hex 或 url）保存到本地文件。"""
        data = response.get("data", {})
        audio_hex = data.get("audio")
        audio_url = data.get("audio_url")

        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if audio_hex:
            raw = bytes.fromhex(audio_hex)
            path.write_bytes(raw)
        elif audio_url:
            import httpx

            r = httpx.get(audio_url, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
        else:
            raise ValueError("响应中没有 audio 或 audio_url 字段")

        return str(path)

    async def cover(
        self,
        *,
        model: str | None = None,
        prompt: str | None = None,
        audio: str | None = None,
        audio_file: str | None = None,
        lyrics: str | None = None,
        seed: int | None = None,
        audio_format: str = "mp3",
        sample_rate: int = 44100,
        bitrate: int = 256000,
        channel: int = 2,
        data_dir: str | None = None,
        allow_trusted_local_path: bool = False,
        extra_allowed_dirs: list[str] | None = None,
    ) -> dict[str, Any]:
        """生成翻唱版本。基于参考音频和风格提示词生成 Cover。"""
        body: dict[str, Any] = {
            "audio_setting": {
                "format": audio_format,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channel": channel,
            },
        }
        if model:
            body["model"] = model
        if prompt:
            body["prompt"] = prompt
        if audio:
            body["audio_url"] = audio
        elif audio_file:
            import asyncio
            import base64

            path = resolve_local_input_path(
                audio_file,
                data_dir=data_dir,
                allow_trusted_local_path=allow_trusted_local_path,
                extra_allowed_dirs=extra_allowed_dirs,
                label="audioFile",
            )
            raw = await asyncio.to_thread(path.read_bytes)
            body["audio_base64"] = base64.b64encode(raw).decode("ascii")
        if lyrics:
            body["lyrics"] = lyrics
        if seed is not None:
            body["seed"] = seed

        return await self._client.request_json(
            "POST",
            music_endpoint(self._client.base_url),
            body=body,
        )
