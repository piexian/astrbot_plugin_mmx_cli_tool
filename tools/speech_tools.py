"""MiniMax 语音合成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.speech import SpeechAPI


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

    def __init__(self, api: SpeechAPI, data_dir: str = "."):
        super().__init__(
            name="mmx_speech_synthesize",
            description=(
                "Synthesize speech from text using MiniMax TTS. "
                "Supports 30+ voices, speed/pitch/volume control. Max 10k characters. "
                "Returns the generated audio file path."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to synthesize (required, max 10000 characters)",
                    },
                    "voice": {
                        "type": "string",
                        "description": "Voice ID (default: English_expressive_narrator). Use mmx_speech_voices to list available voices.",
                    },
                    "model": {
                        "type": "string",
                        "description": "Model ID: speech-2.8-hd (default), speech-2.6, speech-02",
                    },
                    "speed": {
                        "type": "number",
                        "description": "Speech speed multiplier (e.g. 0.5 = half speed, 2.0 = double)",
                    },
                    "volume": {
                        "type": "number",
                        "description": "Volume level",
                    },
                    "pitch": {
                        "type": "number",
                        "description": "Pitch adjustment",
                    },
                    "format": {
                        "type": "string",
                        "description": "Audio format: mp3, pcm, flac, wav, opus (default: mp3)",
                    },
                    "language": {
                        "type": "string",
                        "description": "Language boost (e.g. english, chinese, korean)",
                    },
                },
                "required": ["text"],
            },
        )
        self._api = api
        self._data_dir = data_dir

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        text = kwargs.get("text", "")
        if not text:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 text 参数",
                        "hint": "请提供 text 参数，指定要合成的文本内容",
                        "example": {"text": "Hello, world!", "voice": "English_expressive_narrator"},
                        "docs": "https://platform.minimaxi.com/docs/api-reference/speech-t2a-http",
                    },
                    ensure_ascii=False,
                )
            )

        audio_format = kwargs.get("format", "mp3")

        try:
            result = await self._api.synthesize(
                text=text,
                model=kwargs.get("model", "speech-2.8-hd"),
                voice=kwargs.get("voice", "English_expressive_narrator"),
                speed=kwargs.get("speed"),
                volume=kwargs.get("volume"),
                pitch=kwargs.get("pitch"),
                audio_format=audio_format,
                language=kwargs.get("language"),
            )
        except Exception as e:
            logger.error(f"[mmx] 语音合成失败: {e}")
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": f"语音合成失败: {e}"}, ensure_ascii=False
                )
            )

        # 保存音频文件
        import time
        from pathlib import Path

        out_path = str(
            Path(self._data_dir) / f"mmx_speech_{int(time.time() * 1000)}.{audio_format}"
        )

        try:
            saved = self._api.save(result, out_path)
        except Exception as e:
            logger.warning(f"[mmx] 语音保存失败: {e}")
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": f"语音保存失败: {e}"}, ensure_ascii=False
                )
            )

        return ToolExecResult(
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
            parameters={
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "description": "Client-side filter by voice ID language prefix (e.g. english, korean, japanese)",
                    },
                },
                "required": [],
            },
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        try:
            result = await self._api.list_voices()
        except Exception as e:
            logger.error(f"[mmx] 音色列表查询失败: {e}")
            return ToolExecResult(
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

        return ToolExecResult(
            json.dumps({"ok": True, "data": result}, ensure_ascii=False)
        )
