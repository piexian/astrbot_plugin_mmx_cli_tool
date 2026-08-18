"""MiniMax 音乐翻唱（Cover）FunctionTool。"""

from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.apis.music import MusicAPI
from ..mmx.model_options import MUSIC_COVER_MODELS, model_options_text
from ..mmx.utils import resolve_data_path
from .audio_result import saved_audio_result, schedule_audio_result_to_agent
from .result import tool_result
from .schema import integer_param, object_parameters, string_param


@dataclass
class MusicCoverTool(FunctionTool):
    """LLM 工具：基于参考音频生成翻唱版本。"""

    def __init__(
        self,
        api: MusicAPI,
        data_dir: str = ".",
        default_model: str = "",
        cache_dir: str | None = None,
        extra_allowed_dirs: list[str] | None = None,
    ):
        super().__init__(
            name="mmx_music_cover",
            description=(
                "Start a MiniMax cover generation in the background. "
                "Provide a target style prompt and a reference audio URL or plugin-data file. "
                "Returns task_id, query_tool, max_wait_seconds, and poll_after_seconds when accepted."
            ),
            parameters=object_parameters(
                {
                    "prompt": string_param(
                        "Target cover style, e.g. 'Indie folk, acoustic guitar, warm male vocal'"
                    ),
                    "audio": string_param(
                        "URL of the reference audio (mp3, wav, flac, etc. - 6s to 6min, max 50MB)"
                    ),
                    "audioFile": string_param(
                        "Plugin data directory reference audio file path (auto base64-encoded)"
                    ),
                    "lyrics": string_param(
                        "Cover lyrics. If omitted, extracted from reference audio via ASR."
                    ),
                    "lyricsFile": string_param(
                        "Plugin data directory lyrics file path."
                    ),
                    "seed": integer_param(
                        "Random seed 0-1000000 for reproducible results"
                    ),
                    "model": string_param(
                        "Model override: music-cover or music-cover-free."
                    ),
                    "format": string_param("Audio format: mp3, wav, or pcm."),
                    "sampleRate": integer_param("Sample rate, e.g. 44100."),
                    "bitrate": integer_param("Bitrate, e.g. 256000."),
                    "channel": integer_param("Channels: 1 or 2."),
                },
                required=["prompt"],
            ),
        )
        self._api = api
        self._data_dir = data_dir
        self._default_model = default_model
        self._cache_dir = cache_dir or data_dir
        self._extra_allowed_dirs = extra_allowed_dirs
        self._tasks: set[asyncio.Task] = set()
        self._max_wait_seconds = 900
        self._poll_after_seconds = 60

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = kwargs.get("prompt", "")
        audio = kwargs.get("audio")
        audio_file = kwargs.get("audioFile")
        lyrics = kwargs.get("lyrics")
        lyrics_file = kwargs.get("lyricsFile")

        # prompt 必填（对齐 mmx-cli）
        if not prompt:
            return tool_result(
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
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "audio 和 audioFile 不能同时使用",
                        "hint": "请选择其一：audio 提供参考音频 URL，或 audioFile 提供插件数据目录内文件路径",
                        "example": {
                            "prompt": "Jazz piano cover",
                            "audio": "https://example.com/song.mp3",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/music-generation",
                    },
                    ensure_ascii=False,
                )
            )

        if lyrics and lyrics_file:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "lyrics 和 lyricsFile 不能同时使用",
                        "hint": "请只提供 lyrics（直接歌词）或 lyricsFile（本地歌词文件路径）",
                        "example": {
                            "prompt": "Jazz piano cover",
                            "audio": "https://example.com/song.mp3",
                            "lyrics": "[Verse] La la la",
                        },
                    },
                    ensure_ascii=False,
                )
            )

        # lyricsFile 路径必须位于受信任的数据目录内（防止 LLM 注入诱导任意文件读取）
        safe_lyrics_file: Path | None = None
        if lyrics_file:
            safe_lyrics_file = resolve_data_path(self._data_dir, str(lyrics_file))
            if safe_lyrics_file is None:
                return tool_result(
                    json.dumps(
                        {
                            "ok": False,
                            "error": "lyricsFile 路径不合法",
                            "hint": f"lyricsFile 必须位于数据目录内：{self._data_dir}，不允许绝对路径或 .. 穿越",
                        },
                        ensure_ascii=False,
                    )
                )

        # 至少需要一个音频来源
        if not audio and not audio_file:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少参考音频",
                        "hint": "请提供 audio（参考音频 URL）或 audioFile（插件数据目录内文件路径）",
                        "example": {
                            "prompt": "Indie folk, acoustic guitar, warm male vocal",
                            "audio": "https://example.com/song.mp3",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/music-generation",
                    },
                    ensure_ascii=False,
                )
            )
        explicit_model = kwargs.get("model")
        if explicit_model and explicit_model not in MUSIC_COVER_MODELS:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "翻唱模型不支持",
                        "hint": f"model 只支持: {model_options_text(MUSIC_COVER_MODELS)}",
                        "example": {
                            "model": MUSIC_COVER_MODELS[0],
                            "prompt": "Indie folk, acoustic guitar",
                            "audio": "https://example.com/song.mp3",
                        },
                        "docs": "https://platform.minimaxi.com/docs/api-reference/music-generation",
                    },
                    ensure_ascii=False,
                )
            )
        selected_model = explicit_model or self._default_model or None

        async def generate_and_save() -> str:
            resolved_lyrics = lyrics
            if safe_lyrics_file is not None:
                resolved_lyrics = await asyncio.to_thread(
                    safe_lyrics_file.read_text, encoding="utf-8"
                )
            result = await self._api.cover(
                model=selected_model,
                prompt=prompt,
                audio=audio,
                audio_file=audio_file,
                lyrics=resolved_lyrics,
                seed=kwargs.get("seed"),
                data_dir=self._data_dir,
                extra_allowed_dirs=self._extra_allowed_dirs,
                audio_format=kwargs.get("format", "mp3"),
                sample_rate=kwargs.get("sampleRate", 44100),
                bitrate=kwargs.get("bitrate", 256000),
                channel=kwargs.get("channel", 2),
            )
            return saved_audio_result(
                self._api,
                result,
                save_dir=self._cache_dir,
                prefix="mmx_music_cover",
                success_message="翻唱生成完成",
                save_error_label="翻唱保存失败",
                audio_format=kwargs.get("format", "mp3"),
            )

        task_id = schedule_audio_result_to_agent(
            context,
            tasks=self._tasks,
            label="翻唱生成",
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
            logger.error(f"[mmx] 翻唱生成失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"翻唱生成失败: {e}"}, ensure_ascii=False
                )
            )
