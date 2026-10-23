"""MiniMax AI 音乐与音效生成 FunctionTool（Matrix MCP batch_text_to_music / batch_text_to_audio）。"""

from __future__ import annotations

import json
import time as _time
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.matrix import MatrixAPI
from ..mmx.media_transfer import MatrixMediaClient
from .result import tool_result
from .schema import object_parameters, string_param


async def _generate_and_download(
    generate,
    media: MatrixMediaClient,
    cache_dir: str,
    prefix: str,
    ext: str,
) -> dict:
    """调用生成接口并把 output_url 下载到本地，返回结果字典。"""
    item = await generate()
    output_url = str(item.get("output_url") or "")
    if not output_url:
        return {"ok": False, "error": "生成成功但服务端未返回音频地址"}
    dest = Path(cache_dir) / f"{prefix}_{int(_time.time() * 1000)}.{ext}"
    try:
        path = await media.download_to_file(output_url, dest, allowed_dir=cache_dir)
    except Exception as e:
        logger.warning(f"[mmx] 音频下载失败，回退远程链接: {e}")
        return {"ok": True, "audio_url": output_url, "hint": "音频下载到本地失败，可使用远程链接。"}
    return {
        "ok": True,
        "file_path": str(path),
        "audio_url": output_url,
        "hint": "音频已保存到本地。发送语音时必须使用 file_path 中的本地路径。",
    }


@dataclass
class GenerateMusicTool(FunctionTool):
    """LLM 工具：文本生成音乐（可带歌词）。"""

    def __init__(self, api: MatrixAPI, media: MatrixMediaClient, cache_dir: str):
        super().__init__(
            name="mmx_generate_music",
            description=(
                "Generate music from a text prompt using MiniMax AI (requires account login via /mmx login). "
                "Supports optional lyrics and mp3/wav/pcm formats. The result includes file_path "
                "(local path) — always send audio via file_path."
            ),
            parameters=object_parameters(
                {
                    "prompt": string_param(
                        "Music description: style, mood, instruments, tempo, e.g. '轻快的 lo-fi 嘻哈，钢琴与萨克斯'"
                    ),
                    "lyrics": string_param("Optional lyrics text for the song"),
                    "format": string_param("Audio format: mp3 (default) or wav"),
                },
                required=["prompt"],
            ),
        )
        self._api = api
        self._media = media
        self._cache_dir = cache_dir

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 prompt 参数",
                        "hint": "请提供 prompt 描述想生成的音乐风格与内容",
                        "example": {"prompt": "史诗感管弦乐，鼓点渐强"},
                    },
                    ensure_ascii=False,
                )
            )
        audio_format = str(kwargs.get("format") or "mp3").strip().lower()
        if audio_format not in {"mp3", "wav"}:
            audio_format = "mp3"
        lyrics = str(kwargs.get("lyrics") or "").strip() or None
        try:
            resp = await _generate_and_download(
                lambda: self._api.batch_text_to_music(prompt, lyrics, audio_format),
                self._media,
                self._cache_dir,
                "mmx_music",
                audio_format,
            )
        except Exception as e:
            logger.error(f"[mmx] 音乐生成失败: {e}")
            return tool_result(
                json.dumps({"ok": False, "error": f"音乐生成失败: {e}"}, ensure_ascii=False)
            )
        return tool_result(json.dumps(resp, ensure_ascii=False))


@dataclass
class GenerateSoundEffectTool(FunctionTool):
    """LLM 工具：文本生成音效。"""

    def __init__(self, api: MatrixAPI, media: MatrixMediaClient, cache_dir: str):
        super().__init__(
            name="mmx_generate_sound_effect",
            description=(
                "Generate a short sound effect from a text description using MiniMax AI "
                "(requires account login via /mmx login). The result includes file_path "
                "(local path) — always send audio via file_path."
            ),
            parameters=object_parameters(
                {
                    "prompt": string_param(
                        "Sound effect description, e.g. '雷雨夜远处的雷声与雨声'"
                    ),
                },
                required=["prompt"],
            ),
        )
        self._api = api
        self._media = media
        self._cache_dir = cache_dir

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        prompt = str(kwargs.get("prompt") or "").strip()
        if not prompt:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 prompt 参数",
                        "hint": "请提供 prompt 描述想生成的音效",
                        "example": {"prompt": "玻璃碎裂声"},
                    },
                    ensure_ascii=False,
                )
            )
        try:
            resp = await _generate_and_download(
                lambda: self._api.batch_text_to_audio(prompt),
                self._media,
                self._cache_dir,
                "mmx_sfx",
                "mp3",
            )
        except Exception as e:
            logger.error(f"[mmx] 音效生成失败: {e}")
            return tool_result(
                json.dumps({"ok": False, "error": f"音效生成失败: {e}"}, ensure_ascii=False)
            )
        return tool_result(json.dumps(resp, ensure_ascii=False))
