"""MiniMax 语音转文字 API。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from ..client import MiniMaxClient
from ..endpoints import speech_to_text_endpoint
from ..errors import ErrorCategory, MiniMaxError, classify_error
from ..sse import parse_sse

DEFAULT_TRANSCRIPTION_MODEL = "asr-1.0"
MAX_AUDIO_BYTES = 50 * 1024 * 1024
TRANSCRIPTION_FORMATS = ("json", "verbose_json", "srt", "vtt")


@dataclass(frozen=True)
class TranscriptionOptions:
    model: str = DEFAULT_TRANSCRIPTION_MODEL
    response_format: str = "json"
    language: str | None = None
    timestamp_level: str | None = None
    stream: bool = False

    def validate(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model 必须为非空字符串")
        if self.response_format not in TRANSCRIPTION_FORMATS:
            raise ValueError("responseFormat 仅支持 json、verbose_json、srt、vtt")
        if self.timestamp_level not in (None, "", "sentence", "word"):
            raise ValueError("timestampLevel 仅支持 sentence 或 word")
        if self.language not in (None, "") and (
            not isinstance(self.language, str)
            or not re.fullmatch(r"[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*", self.language)
        ):
            raise ValueError("language 必须是 BCP-47 语言标签，例如 zh、en、zh-CN")
        if not isinstance(self.stream, bool):
            raise ValueError("stream 必须是布尔值")
        if self.stream and self.response_format != "json":
            raise ValueError("stream 只能与 responseFormat=json 一起使用")


def validate_audio_size(size: int) -> None:
    if size <= 0:
        raise ValueError("音频文件为空")
    if size > MAX_AUDIO_BYTES:
        raise ValueError("音频超过 50 MB 限制，请压缩或分段后提交")


def _valid_duration(value: Any) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


class SpeechToTextAPI:
    """上传音频并读取 JSON、字幕或 SSE，流式结果合并后返回。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def transcribe(
        self, audio: bytes, filename: str, options: TranscriptionOptions
    ) -> dict[str, Any] | str:
        options.validate()
        validate_audio_size(len(audio))
        fields = {"model": options.model, "response_format": options.response_format}
        if options.timestamp_level:
            fields["timestamp_level"] = options.timestamp_level
        if options.stream:
            fields["stream"] = "true"
        kwargs = {
            "data": fields,
            "files": {"file": (filename, audio, "application/octet-stream")},
            "headers": {"language": options.language} if options.language else {},
            "model": options.model,
        }
        path = speech_to_text_endpoint(self._client.base_url)
        if options.stream:
            async with self._client.stream("POST", path, **kwargs) as response:
                media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if media_type != "text/event-stream":
                    await response.aread()
                    if media_type == "application/json":
                        self._client.decode_json_response(response, path)
                    raise MiniMaxError(ErrorCategory.GENERAL, "ASR 未返回 SSE 流，转写未完成")
                return await self._collect_stream(response, path)

        response = await self._client.request("POST", path, **kwargs)
        if options.response_format in ("srt", "vtt"):
            media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type == "application/json":
                self._client.decode_json_response(response, path)
                raise MiniMaxError(ErrorCategory.GENERAL, "ASR 未返回预期的字幕文档")
            if media_type not in ("text/plain", "text/vtt", "application/x-subrip"):
                raise MiniMaxError(ErrorCategory.GENERAL, "ASR 返回了无效的字幕类型")
            return response.content.decode("utf-8")
        result = self._client.decode_json_response(response, path)
        if not isinstance(result.get("text"), str) or not _valid_duration(result.get("duration")):
            raise MiniMaxError(ErrorCategory.GENERAL, "ASR 返回缺少有效 text 或 duration")
        return result

    async def _collect_stream(self, response, path: str) -> dict[str, Any]:
        parts: dict[int, str] = {}
        total_chars = 0
        try:
            async for chunk in parse_sse(response, strict=True):
                base = chunk.get("base_resp")
                if chunk.get("error") or (isinstance(base, dict) and base.get("status_code", 0)):
                    raise classify_error(200, path=path, api_body=chunk)
                index, delta, finish = chunk.get("index"), chunk.get("delta", ""), chunk.get("finish")
                if (not isinstance(index, int) or isinstance(index, bool) or index < 0
                        or not isinstance(delta, str) or not isinstance(finish, bool)):
                    raise ValueError("ASR 流事件字段无效")
                if index in parts:
                    raise ValueError("ASR 流包含重复事件")
                parts[index] = delta
                total_chars += len(delta)
                if total_chars > 2 * 1024 * 1024:
                    raise ValueError("ASR 转写内容超过大小限制")
                if finish:
                    if len(parts) != index + 1 or max(parts) != index:
                        raise ValueError("ASR 流缺少事件，转写可能不完整")
                    if not _valid_duration(chunk.get("duration")):
                        raise ValueError("ASR 结束事件缺少有效 duration")
                    return {
                        "text": "".join(parts[i] for i in sorted(parts)),
                        "duration": chunk["duration"],
                    }
        except ValueError as exc:
            raise MiniMaxError(ErrorCategory.GENERAL, str(exc)) from exc
        raise MiniMaxError(ErrorCategory.GENERAL, "ASR 流在 finish=true 前结束，转写不完整")
