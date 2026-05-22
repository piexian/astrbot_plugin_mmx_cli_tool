"""MiniMax 语音合成（TTS）API。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..client import MiniMaxClient
from ..endpoints import speech_endpoint, voices_endpoint


class SpeechAPI:
    """MiniMax 语音合成接口。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def synthesize(
        self,
        text: str,
        *,
        model: str = "speech-2.8-hd",
        voice: str = "English_expressive_narrator",
        speed: float | None = None,
        volume: float | None = None,
        pitch: float | None = None,
        audio_format: str = "mp3",
        sample_rate: int = 32000,
        bitrate: int = 128000,
        channels: int = 1,
        language: str | None = None,
        subtitles: bool = False,
    ) -> dict[str, Any]:
        """同步 TTS 合成，最大 10k 字符。"""
        body: dict[str, Any] = {
            "model": model,
            "text": text,
            "voice_setting": {
                "voice_id": voice,
            },
            "audio_setting": {
                "format": audio_format,
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "channel": channels,
            },
        }
        if speed is not None:
            body["voice_setting"]["speed"] = speed
        if volume is not None:
            body["voice_setting"]["vol"] = volume
        if pitch is not None:
            body["voice_setting"]["pitch"] = pitch
        if language:
            body["language_boost"] = language
        if subtitles:
            body["subtitle"] = True

        return await self._client.request_json(
            "POST",
            speech_endpoint(self._client.base_url),
            body=body,
        )

    def save(self, response: dict[str, Any], out_path: str) -> str:
        """将 TTS 响应中的音频数据保存为本地文件。"""
        data = response.get("data", {})
        audio_hex = data.get("audio")
        audio_url = data.get("audio_url")

        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if audio_hex:
            path.write_bytes(bytes.fromhex(audio_hex))
        elif audio_url:
            import httpx

            r = httpx.get(audio_url, timeout=60)
            r.raise_for_status()
            path.write_bytes(r.content)
        else:
            raise ValueError("响应中没有 audio 或 audio_url 字段")

        return str(path)

    async def list_voices(self, language: str | None = None) -> dict[str, Any]:
        """获取可用系统音色列表。"""
        return await self._client.request_json(
            "GET",
            voices_endpoint(self._client.base_url),
        )
