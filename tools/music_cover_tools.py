"""MiniMax 音乐翻唱（Cover）FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.music import MusicAPI


@dataclass
class MusicCoverTool(FunctionTool):
    """LLM 工具：基于参考音频生成翻唱版本。"""

    def __init__(self, api: MusicAPI):
        super().__init__(
            name="mmx_music_cover",
            description=(
                "Generate a cover version of a song based on reference audio. "
                "Provide a target style prompt and a reference audio URL or local file."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Target cover style, e.g. 'Indie folk, acoustic guitar, warm male vocal'",
                    },
                    "audio": {
                        "type": "string",
                        "description": "URL of the reference audio (mp3, wav, flac, etc. — 6s to 6min, max 50MB)",
                    },
                    "audioFile": {
                        "type": "string",
                        "description": "Local reference audio file path (auto base64-encoded)",
                    },
                    "lyrics": {
                        "type": "string",
                        "description": "Cover lyrics. If omitted, extracted from reference audio via ASR.",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Random seed 0–1000000 for reproducible results",
                    },
                },
                "required": ["prompt"],
            },
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = kwargs.get("prompt", "")
        audio = kwargs.get("audio")
        audio_file = kwargs.get("audioFile")

        # prompt 必填（对齐 mmx-cli）
        if not prompt:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 prompt 参数",
                        "hint": "prompt 为必填参数，请描述翻唱的目标音乐风格",
                        "example": {
                            "prompt": "Indie folk, acoustic guitar, warm male vocal",
                            "audio": "https://example.com/song.mp3",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/music-generation",
                    },
                    ensure_ascii=False,
                )
            )

        # audio 和 audioFile 互斥（对齐 mmx-cli）
        if audio and audio_file:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "audio 和 audioFile 不能同时使用",
                        "hint": "请选择其一：audio 提供参考音频 URL，或 audioFile 提供本地文件路径",
                        "example": {
                            "prompt": "Jazz piano cover",
                            "audio": "https://example.com/song.mp3",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/music-generation",
                    },
                    ensure_ascii=False,
                )
            )

        # 至少需要一个音频来源
        if not audio and not audio_file:
            return ToolExecResult(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少参考音频",
                        "hint": "请提供 audio（参考音频 URL）或 audioFile（本地文件路径）",
                        "example": {
                            "prompt": "Indie folk, acoustic guitar, warm male vocal",
                            "audio": "https://example.com/song.mp3",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/music-generation",
                    },
                    ensure_ascii=False,
                )
            )

        try:
            result = await self._api.cover(
                prompt=prompt,
                audio=audio,
                audio_file=audio_file,
                lyrics=kwargs.get("lyrics"),
                seed=kwargs.get("seed"),
            )
        except Exception as e:
            logger.error(f"[mmx] 翻唱生成失败: {e}")
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": f"翻唱生成失败: {e}"}, ensure_ascii=False
                )
            )

        data = result.get("data", {})
        audio_url = data.get("audio_url", "")
        resp: dict = {"ok": True, "audio_url": audio_url, "raw_data": data}
        return ToolExecResult(json.dumps(resp, ensure_ascii=False))
