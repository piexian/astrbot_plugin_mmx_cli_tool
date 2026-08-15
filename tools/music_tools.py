"""MiniMax 音乐生成 FunctionTool。"""

from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.music import MusicAPI
from ..mmx.model_options import MUSIC_MODELS, model_options_text
from .audio_result import saved_audio_result, schedule_audio_result_to_agent
from .result import tool_result
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

    def __init__(
        self,
        api: MusicAPI,
        data_dir: str = ".",
        default_model: str = "",
        cache_dir: str | None = None,
    ):
        super().__init__(
            name="mmx_generate_music",
            description=(
                "Start MiniMax music generation in the background. Three modes (mutually exclusive):\n"
                "1. With lyrics: provide 'lyrics' with structure tags like [Verse], [Chorus], etc.\n"
                "2. Instrumental: set 'instrumental' to true for music without vocals.\n"
                "3. Auto lyrics: set 'lyricsOptimizer' to true to auto-generate lyrics from prompt.\n"
                "Provide at least one content, mode, or style-control parameter. "
                "Returns task_id, query_tool, max_wait_seconds, and poll_after_seconds when accepted."
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
                    "aigcWatermark": boolean_param(
                        "Embed AI-generated content watermark in audio for content provenance."
                    ),
                    "model": string_param(
                        "Model override: music-2.6, music-2.5+, or music-2.5. "
                        "Omit to use the plugin default_music_model configuration."
                    ),
                },
            ),
        )
        self._api = api
        self._data_dir = data_dir
        self._default_model = default_model
        self._cache_dir = cache_dir or data_dir
        self._tasks: set[asyncio.Task] = set()
        self._max_wait_seconds = 900
        self._poll_after_seconds = 60

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
            kwargs.get("aigcWatermark"),
        )
        if not any(bool(value) for value in generation_inputs):
            return tool_result(
                _hint_json(
                    "缺少音乐生成参数",
                    "请至少提供 prompt、lyrics、instrumental、lyricsOptimizer 或一个风格控制参数",
                    {"prompt": "Cinematic orchestral", "instrumental": True},
                )
            )

        # 互斥校验
        if lyrics and is_instrumental:
            return tool_result(
                _hint_json(
                    "lyrics 和 instrumental 互斥，不能同时使用",
                    "如果要生成带歌词的歌曲，请只提供 lyrics；如果要纯器乐，请只设置 instrumental=true",
                    {"prompt": "Cinematic orchestral", "instrumental": True},
                )
            )
        if lyrics_optimizer and (lyrics or is_instrumental):
            return tool_result(
                _hint_json(
                    "lyricsOptimizer 不能与 lyrics 或 instrumental 同时使用",
                    "lyricsOptimizer 会根据 prompt 自动生成歌词，无需手动提供 lyrics",
                    {"prompt": "Upbeat pop about summer", "lyricsOptimizer": True},
                )
            )

        # 非纯器乐且未开启自动歌词时，lyrics 必填（对齐 mmx-cli）
        if not is_instrumental and not lyrics_optimizer and not lyrics:
            return tool_result(
                _hint_json(
                    "缺少 lyrics 参数",
                    "非纯器乐模式必须提供 lyrics（歌词）。"
                    "如果要纯器乐请设置 instrumental=true，"
                    "如果要自动生成歌词请设置 lyricsOptimizer=true",
                    {"prompt": "Upbeat pop", "lyricsOptimizer": True},
                )
            )
        explicit_model = kwargs.get("model")
        if explicit_model and explicit_model not in MUSIC_MODELS:
            return tool_result(
                _hint_json(
                    "音乐模型不支持",
                    f"model 只支持: {model_options_text(MUSIC_MODELS)}",
                    {
                        "model": MUSIC_MODELS[0],
                        "prompt": "Upbeat pop",
                        "lyricsOptimizer": True,
                    },
                )
            )
        selected_model = explicit_model or self._default_model or None

        async def generate_and_save() -> str:
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
                aigc_watermark=kwargs.get("aigcWatermark", False),
                model=selected_model,
            )
            return saved_audio_result(
                self._api,
                result,
                save_dir=self._cache_dir,
                prefix="mmx_music",
                success_message="音乐生成完成",
                save_error_label="音乐保存失败",
            )

        task_id = schedule_audio_result_to_agent(
            context,
            tasks=self._tasks,
            label="音乐生成",
            tool_name=self.name,
            tool_args=dict(kwargs),
            max_wait_seconds=self._max_wait_seconds,
            poll_after_seconds=self._poll_after_seconds,
            work=generate_and_save,
        )
        if task_id:
            return tool_result(
                json.dumps(
                    {
                        "ok": True,
                        "background": True,
                        "task_id": task_id,
                        "status": "started",
                        "query_tool": "mmx_background_task_get",
                        "max_wait_seconds": self._max_wait_seconds,
                        "poll_after_seconds": self._poll_after_seconds,
                    },
                    ensure_ascii=False,
                )
            )

        try:
            return tool_result(await generate_and_save())
        except Exception as e:
            logger.error(f"[mmx] 音乐生成失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"音乐生成失败: {e}"}, ensure_ascii=False
                )
            )
