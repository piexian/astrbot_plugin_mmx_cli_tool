"""MiniMax 语音合成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.speech import SpeechAPI
from .result import tool_result
from .schema import boolean_param, number_param, object_parameters, string_param


def _language_prefix(voice_id: str) -> str:
    match = voice_id.split("_", 1)
    return match[0] if match else voice_id


def _filter_voices_by_language(voices: list[dict], language: str) -> list[dict]:
    target = language.lower()
    filtered: list[dict] = []
    for voice in voices:
        voice_id = str(voice.get("voice_id", ""))
        prefix = _language_prefix(voice_id).lower()
        if (
            prefix == target
            or prefix.startswith(f"{target}_")
            or prefix.startswith(f"{target} (")
        ):
            filtered.append(voice)
    return filtered


@dataclass
class SpeechSynthesizeTool(FunctionTool):
    """LLM 工具：调用 MiniMax 语音合成（TTS）API。"""

    def __init__(self, api: SpeechAPI, data_dir: str = ".", default_model: str = ""):
        super().__init__(
            name="mmx_speech_synthesize",
            description=(
                "Synthesize speech from text using MiniMax TTS. "
                "Supports 30+ voices, speed/pitch/volume control. Max 10k characters. "
                "Returns the generated audio file path."
            ),
            parameters=object_parameters(
                {
                    "text": string_param(
                        "Text to synthesize (required, max 10000 characters)"
                    ),
                    "voice": string_param(
                        "Voice ID. Use mmx_speech_voices to list available voices."
                    ),
                    "model": string_param(
                        "Model override: speech-2.8-hd, speech-2.6, or speech-02. Omit to use the plugin default_speech_model configuration."
                    ),
                    "speed": number_param(
                        "Speech speed multiplier (e.g. 0.5 = half speed, 2.0 = double)"
                    ),
                    "volume": number_param("Volume level"),
                    "pitch": number_param("Pitch adjustment"),
                    "format": string_param(
                        "Audio format: mp3, pcm, flac, wav, pcmu_raw, pcmu_wav, opus"
                    ),
                    "sampleRate": number_param("Sample rate"),
                    "bitrate": number_param("Bitrate"),
                    "channels": number_param("Audio channels"),
                    "language": string_param(
                        "Language boost (e.g. english, chinese, korean)"
                    ),
                    "subtitles": boolean_param(
                        "Include subtitle timing data when supported."
                    ),
                },
                required=["text"],
            ),
        )
        self._api = api
        self._data_dir = data_dir
        self._default_model = default_model

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        text = kwargs.get("text", "")
        if not text:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 text 参数",
                        "hint": "请提供 text 参数，指定要合成的文本内容",
                        "example": {
                            "text": "Hello, world!",
                            "voice": "English_expressive_narrator",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/speech-t2a-http",
                    },
                    ensure_ascii=False,
                )
            )

        audio_format = kwargs.get("format", "mp3")

        try:
            result = await self._api.synthesize(
                text=text,
                model=kwargs.get("model") or self._default_model or None,
                voice=kwargs.get("voice") or "English_expressive_narrator",
                speed=kwargs.get("speed"),
                volume=kwargs.get("volume"),
                pitch=kwargs.get("pitch"),
                audio_format=audio_format,
                sample_rate=kwargs.get("sampleRate") or 32000,
                bitrate=kwargs.get("bitrate") or 128000,
                channels=kwargs.get("channels") or 1,
                language=kwargs.get("language"),
                subtitles=bool(kwargs.get("subtitles", False)),
            )
        except Exception as e:
            logger.error(f"[mmx] 语音合成失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"语音合成失败: {e}"}, ensure_ascii=False
                )
            )

        # 保存音频文件
        import time
        from pathlib import Path

        out_path = str(
            Path(self._data_dir)
            / f"mmx_speech_{int(time.time() * 1000)}.{audio_format}"
        )

        try:
            saved = self._api.save(result, out_path)
        except Exception as e:
            logger.warning(f"[mmx] 语音保存失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"语音保存失败: {e}"}, ensure_ascii=False
                )
            )

        return tool_result(
            json.dumps(
                {"ok": True, "file_path": saved, "message": "语音合成完成"},
                ensure_ascii=False,
            )
        )


@dataclass
class ListVoicesTool(FunctionTool):
    """LLM 工具：列出 MiniMax 可用的 TTS 音色。"""

    def __init__(self, api: SpeechAPI):
        super().__init__(
            name="mmx_speech_voices",
            description="List available system voices for MiniMax TTS.",
            parameters=object_parameters(
                {
                    "language": string_param(
                        "Client-side filter by voice ID language prefix (e.g. english, korean, japanese)"
                    ),
                },
            ),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            result = await self._api.list_voices()
        except Exception as e:
            logger.error(f"[mmx] 音色列表查询失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"音色列表查询失败: {e}"},
                    ensure_ascii=False,
                )
            )

        language = kwargs.get("language")
        if language:
            data = result.copy()
            voices = data.get("system_voice", [])
            if isinstance(voices, list):
                data["system_voice"] = _filter_voices_by_language(voices, language)
            result = data

        return tool_result(
            json.dumps({"ok": True, "data": result}, ensure_ascii=False)
        )
