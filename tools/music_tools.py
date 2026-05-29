"""MiniMax 音乐生成 FunctionTool。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.music import MusicAPI
from .schema import boolean_param, integer_param, object_parameters, string_param


def _hint_json(error: str, hint: str, example: dict | None = None) -> str:
    """构造带提示的错误 JSON。"""
    resp: dict = {"ok": False, "error": error, "hint": hint}
    if example:
        resp["example"] = example
    resp["docs"] = "https://platform.minimaxi.com/docs/api-reference/music-generation"
    return json.dumps(resp, ensure_ascii=False)


@dataclass
class GenerateMusicTool(FunctionTool):
    """LLM 工具：调用 MiniMax 音乐生成 API。

    三种生成模式（互斥）：
    1. lyrics — 带歌词的歌曲
    2. instrumental — 纯器乐（无人声）
    3. lyricsOptimizer — 根据 prompt 自动生成歌词
    """

    def __init__(self, api: MusicAPI):
        super().__init__(
            name="mmx_generate_music",
            description=(
                "Generate music using MiniMax AI. Three modes (mutually exclusive):\n"
                "1. With lyrics: provide 'lyrics' with structure tags like [Verse], [Chorus], etc.\n"
                "2. Instrumental: set 'instrumental' to true for music without vocals.\n"
                "3. Auto lyrics: set 'lyricsOptimizer' to true to auto-generate lyrics from prompt.\n"
                "Provide at least one content, mode, or style-control parameter."
            ),
            parameters=object_parameters(
                {
                    "prompt": string_param(
                        "Music style description (e.g. 'cinematic orchestral, building tension'). Max 2000 chars."
                    ),
                    "lyrics": string_param(
                        "Song lyrics with structure tags: [Intro], [Verse], [Chorus], [Bridge], [Outro], etc. "
                        "Max 3500 chars. Mutually exclusive with 'instrumental'."
                    ),
                    "lyricsOptimizer": boolean_param(
                        "Auto-generate lyrics from prompt. Cannot be used with lyrics or instrumental."
                    ),
                    "instrumental": boolean_param(
                        "Generate instrumental music (no vocals). Cannot be used with lyrics."
                    ),
                    "vocals": string_param(
                        "Vocal style, e.g. 'warm male baritone', 'bright female soprano'"
                    ),
                    "genre": string_param(
                        "Music genre, e.g. folk, pop, jazz, electronic"
                    ),
                    "mood": string_param(
                        "Mood or emotion, e.g. warm, melancholic, uplifting"
                    ),
                    "instruments": string_param(
                        "Instruments to feature, e.g. 'acoustic guitar, piano, strings'"
                    ),
                    "tempo": string_param(
                        "Tempo description, e.g. fast, slow, moderate"
                    ),
                    "bpm": integer_param("Exact tempo in beats per minute"),
                    "key": string_param("Musical key, e.g. 'C major', 'A minor'"),
                    "avoid": string_param("Elements to avoid in the generated music"),
                    "useCase": string_param(
                        "Use case context, e.g. 'background music for video', 'theme song'"
                    ),
                    "structure": string_param(
                        "Song structure, e.g. 'verse-chorus-verse-bridge-chorus'"
                    ),
                    "references": string_param(
                        "Reference tracks or artists, e.g. 'similar to Ed Sheeran'"
                    ),
                    "extra": string_param(
                        "Additional fine-grained requirements not covered above"
                    ),
                    "model": string_param(
                        "Model: music-2.6 (default), music-2.5+, or music-2.5"
                    ),
                },
            ),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        # 参数直接使用官方名
        is_instrumental = kwargs.get("instrumental", False)
        lyrics = kwargs.get("lyrics")
        lyrics_optimizer = kwargs.get("lyricsOptimizer", False)
        generation_inputs = (
            kwargs.get("prompt"),
            lyrics,
            is_instrumental,
            lyrics_optimizer,
            kwargs.get("genre"),
            kwargs.get("mood"),
            kwargs.get("vocals"),
            kwargs.get("instruments"),
            kwargs.get("tempo"),
            kwargs.get("bpm"),
            kwargs.get("key"),
            kwargs.get("avoid"),
            kwargs.get("useCase"),
            kwargs.get("structure"),
            kwargs.get("references"),
            kwargs.get("extra"),
        )
        if not any(bool(value) for value in generation_inputs):
            return ToolExecResult(
                _hint_json(
                    "缺少音乐生成参数",
                    "请至少提供 prompt、lyrics、instrumental、lyricsOptimizer 或一个风格控制参数",
                    {"prompt": "Cinematic orchestral", "instrumental": True},
                )
            )

        # 互斥校验
        if lyrics and is_instrumental:
            return ToolExecResult(
                _hint_json(
                    "lyrics 和 instrumental 互斥，不能同时使用",
                    "如果要生成带歌词的歌曲，请只提供 lyrics；如果要纯器乐，请只设置 instrumental=true",
                    {"prompt": "Cinematic orchestral", "instrumental": True},
                )
            )
        if lyrics_optimizer and (lyrics or is_instrumental):
            return ToolExecResult(
                _hint_json(
                    "lyricsOptimizer 不能与 lyrics 或 instrumental 同时使用",
                    "lyricsOptimizer 会根据 prompt 自动生成歌词，无需手动提供 lyrics",
                    {"prompt": "Upbeat pop about summer", "lyricsOptimizer": True},
                )
            )

        # 非纯器乐且未开启自动歌词时，lyrics 必填（对齐 mmx-cli）
        if not is_instrumental and not lyrics_optimizer and not lyrics:
            return ToolExecResult(
                _hint_json(
                    "缺少 lyrics 参数",
                    "非纯器乐模式必须提供 lyrics（歌词）。"
                    "如果要纯器乐请设置 instrumental=true，"
                    "如果要自动生成歌词请设置 lyricsOptimizer=true",
                    {"prompt": "Upbeat pop", "lyricsOptimizer": True},
                )
            )

        try:
            result = await self._api.generate(
                prompt=kwargs.get("prompt"),
                lyrics=lyrics,
                is_instrumental=is_instrumental,
                lyrics_optimizer=lyrics_optimizer,
                genre=kwargs.get("genre"),
                mood=kwargs.get("mood"),
                vocals=kwargs.get("vocals"),
                instruments=kwargs.get("instruments"),
                tempo=kwargs.get("tempo"),
                bpm=kwargs.get("bpm"),
                key=kwargs.get("key"),
                avoid=kwargs.get("avoid"),
                use_case=kwargs.get("useCase"),
                structure=kwargs.get("structure"),
                references=kwargs.get("references"),
                extra=kwargs.get("extra"),
                model=kwargs.get("model", "music-2.6"),
            )
        except Exception as e:
            logger.error(f"[mmx] 音乐生成失败: {e}")
            return ToolExecResult(
                json.dumps(
                    {"ok": False, "error": f"音乐生成失败: {e}"}, ensure_ascii=False
                )
            )

        data = result.get("data", {})
        audio_url = data.get("audio_url", "")
        resp: dict = {"ok": True, "audio_url": audio_url, "raw_data": data}
        return ToolExecResult(json.dumps(resp, ensure_ascii=False))
