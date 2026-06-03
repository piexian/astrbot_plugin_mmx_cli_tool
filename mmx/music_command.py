"""Parser for the direct ``/mmx music`` command."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model_options import MUSIC_MODELS, model_options_text
from .utils import split_command_tokens


class MusicCommandError(ValueError):
    """Raised when direct music command arguments are invalid."""


@dataclass(frozen=True)
class MusicCommandArgs:
    prompt: str = ""
    lyrics: str | None = None
    lyrics_optimizer: bool = False
    instrumental: bool = False
    vocals: str | None = None
    genre: str | None = None
    mood: str | None = None
    instruments: str | None = None
    tempo: str | None = None
    bpm: int | None = None
    key: str | None = None
    avoid: str | None = None
    use_case: str | None = None
    structure: str | None = None
    references: str | None = None
    extra: str | None = None
    model: str | None = None
    output_format: str = "hex"
    audio_format: str = "mp3"
    sample_rate: int = 44100
    bitrate: int = 256000
    aigc_watermark: bool = False


_BOOL_FLAGS = {
    "--instrumental": "instrumental",
    "--lyrics-optimizer": "lyrics_optimizer",
    "--lyricsOptimizer": "lyrics_optimizer",
    "--aigc-watermark": "aigc_watermark",
    "--aigcWatermark": "aigc_watermark",
}

_VALUE_FLAGS = {
    "--prompt": "prompt",
    "--lyrics": "lyrics",
    "--vocals": "vocals",
    "--genre": "genre",
    "--mood": "mood",
    "--instruments": "instruments",
    "--tempo": "tempo",
    "--bpm": "bpm",
    "--key": "key",
    "--avoid": "avoid",
    "--use-case": "use_case",
    "--useCase": "use_case",
    "--structure": "structure",
    "--references": "references",
    "--extra": "extra",
    "--model": "model",
    "--output-format": "output_format",
    "--outputFormat": "output_format",
    "--format": "audio_format",
    "--sample-rate": "sample_rate",
    "--sampleRate": "sample_rate",
    "--bitrate": "bitrate",
}

_UNSUPPORTED_FLAGS = {
    "--lyrics-file": "AstrBot 直接指令不读取服务器本地歌词文件，请使用 --lyrics 直接传歌词。",
    "--out": "AstrBot 插件会自动保存并发送音频，不支持 --out 指定服务器路径。",
    "--stream": "AstrBot 消息不支持 mmx CLI 的 --stream 原始音频流模式。",
}

_FLAG_NAMES = sorted(
    {
        flag.removeprefix("--")
        for flag in [*_BOOL_FLAGS, *_VALUE_FLAGS, *_UNSUPPORTED_FLAGS]
    },
    key=len,
    reverse=True,
)
_EMBEDDED_FLAG_RE = re.compile(
    rf"(?<!\s)(--(?:{'|'.join(map(re.escape, _FLAG_NAMES))})\b)"
)


def parse_music_command(raw: str) -> MusicCommandArgs:
    """Parse arguments after ``/mmx music`` using mmx-cli-style flags."""
    text = _EMBEDDED_FLAG_RE.sub(r" \1", raw.strip())
    if not text:
        raise MusicCommandError(_usage())

    try:
        tokens = split_command_tokens(text)
    except ValueError as exc:
        raise MusicCommandError(f"参数解析失败: {exc}\n\n{_usage()}") from exc

    values: dict[str, object] = {}
    positional: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name, inline_value = _split_inline_value(token)

        if not name.startswith("--"):
            positional.append(token)
            index += 1
            continue

        if name in _UNSUPPORTED_FLAGS:
            raise MusicCommandError(_UNSUPPORTED_FLAGS[name])

        if name in _BOOL_FLAGS:
            attr = _BOOL_FLAGS[name]
            values[attr] = _parse_bool(inline_value) if inline_value else True
            index += 1
            continue

        if name not in _VALUE_FLAGS:
            raise MusicCommandError(f"不支持的音乐参数: {name}\n\n{_usage()}")

        attr = _VALUE_FLAGS[name]
        if inline_value is None:
            value_parts: list[str] = []
            index += 1
            while index < len(tokens) and not tokens[index].startswith("--"):
                value_parts.append(tokens[index])
                index += 1
            if not value_parts:
                raise MusicCommandError(f"{name} 需要一个值。\n\n{_usage()}")
            value = " ".join(value_parts)
        else:
            value = inline_value
            index += 1
        values[attr] = _coerce_value(attr, value)

    if "prompt" not in values:
        values["prompt"] = " ".join(positional).strip()

    args = MusicCommandArgs(**values)
    _validate(args)
    return args


def _split_inline_value(token: str) -> tuple[str, str | None]:
    if token.startswith("--") and "=" in token:
        name, value = token.split("=", 1)
        return name, value
    return token, None


def _coerce_value(attr: str, value: str) -> object:
    if attr in {"bpm", "sample_rate", "bitrate"}:
        try:
            parsed = int(value)
        except ValueError as exc:
            raise MusicCommandError(f"{attr} 必须是整数: {value}") from exc
        if parsed <= 0:
            raise MusicCommandError(f"{attr} 必须大于 0")
        return parsed
    return value.strip()


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise MusicCommandError(f"布尔参数值无效: {value}")


def _validate(args: MusicCommandArgs) -> None:
    has_lyrics = bool(args.lyrics and args.lyrics.strip())
    if args.instrumental and has_lyrics:
        raise MusicCommandError(
            "不能同时使用 --instrumental 和 --lyrics。\n"
            "纯音乐请用: /mmx music <描述> --instrumental"
        )
    if args.lyrics_optimizer and (has_lyrics or args.instrumental):
        raise MusicCommandError(
            "不能同时使用 --lyrics-optimizer 与 --lyrics 或 --instrumental。\n"
            "自动歌词请用: /mmx music <描述> --lyrics-optimizer"
        )

    if not (args.prompt or has_lyrics or args.instrumental or args.lyrics_optimizer):
        raise MusicCommandError(_usage())

    if not args.instrumental and not args.lyrics_optimizer and not has_lyrics:
        raise MusicCommandError(
            "带人声歌曲需要提供歌词，或改用 --lyrics-optimizer 自动生成歌词，"
            "或用 --instrumental 生成纯音乐。\n\n"
            f"{_usage()}"
        )

    if args.output_format not in {"hex", "url"}:
        raise MusicCommandError("--output-format 只支持 hex 或 url")
    if args.audio_format not in {"mp3", "wav", "pcm"}:
        raise MusicCommandError("--format 只支持 mp3、wav 或 pcm")

    if args.model and args.model not in MUSIC_MODELS:
        raise MusicCommandError(f"--model 只支持: {model_options_text(MUSIC_MODELS)}")


def _usage() -> str:
    return (
        "用法: /mmx music <描述> (--lyrics <歌词> | --instrumental | --lyrics-optimizer)\n"
        "例如: /mmx music 欢乐电子乐 --lyrics-optimizer\n"
        "例如: /mmx music 电影感管弦乐 --instrumental\n"
        '例如: /mmx music --prompt "Upbeat pop" --lyrics "[Verse] La la la"'
    )
