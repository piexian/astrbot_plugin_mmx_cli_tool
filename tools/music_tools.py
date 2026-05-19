"""MiniMax 音乐生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.music import MusicAPI


@dataclass
class GenerateMusicTool(FunctionTool):
    """LLM 工具：调用 MiniMax 音乐生成 API。"""

    def __init__(self, api: MusicAPI):
        super().__init__(
            name="mmx_generate_music",
            description="Generate music using MiniMax AI. Can create instrumental tracks or songs with lyrics.",
            parameters={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Music description: style, mood, instruments, tempo. E.g. 'warm acoustic guitar, folk style, relaxing'",
                    },
                    "lyrics": {
                        "type": "string",
                        "description": "Song lyrics. If provided, generates a vocal song. Mutually exclusive with is_instrumental.",
                    },
                    "is_instrumental": {
                        "type": "boolean",
                        "description": "Generate instrumental music without vocals. Default false.",
                        "default": False,
                    },
                    "genre": {
                        "type": "string",
                        "description": "Music genre, e.g. folk, pop, jazz, classical, electronic",
                    },
                    "mood": {
                        "type": "string",
                        "description": "Music mood, e.g. warm, melancholic, energetic, calm",
                    },
                    "vocals": {
                        "type": "string",
                        "description": "Vocal description, e.g. 'warm male baritone', 'soft female soprano'",
                    },
                    "instruments": {
                        "type": "string",
                        "description": "Instruments to include, e.g. 'acoustic guitar, piano, strings'",
                    },
                    "tempo": {
                        "type": "string",
                        "description": "Tempo description: fast, slow, moderate",
                    },
                    "key": {
                        "type": "string",
                        "description": "Musical key, e.g. 'C major', 'A minor'",
                    },
                },
                "required": [],
            },
        )
        self._api = api

    async def handler(self, context: ContextWrapper[AstrAgentContext], **kwargs) -> ToolExecResult:
        try:
            result = await self._api.generate(
                prompt=kwargs.get("prompt"),
                lyrics=kwargs.get("lyrics"),
                is_instrumental=kwargs.get("is_instrumental", False),
                genre=kwargs.get("genre"),
                mood=kwargs.get("mood"),
                vocals=kwargs.get("vocals"),
                instruments=kwargs.get("instruments"),
                tempo=kwargs.get("tempo"),
                key=kwargs.get("key"),
            )
        except Exception as e:
            logger.error(f"[mmx] 音乐生成失败: {e}")
            return ToolExecResult(json.dumps({"ok": False, "error": f"音乐生成失败: {e}"}, ensure_ascii=False))

        data = result.get("data", {})
        audio_url = data.get("audio_url", "")
        resp: dict = {"ok": True, "audio_url": audio_url}
        return ToolExecResult(json.dumps(resp, ensure_ascii=False))
