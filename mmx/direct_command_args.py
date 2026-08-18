"""Parsers for direct ``/mmx`` commands."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .model_options import MUSIC_COVER_MODELS
from .utils import split_command_tokens


class DirectCommandError(ValueError):
    """Raised when direct command arguments are invalid."""


@dataclass(frozen=True)
class ImageCommandArgs:
    prompt: str
    model: str | None = None
    aspect_ratio: str | None = None
    n: int = 1
    seed: int | None = None
    width: int | None = None
    height: int | None = None
    prompt_optimizer: bool = True
    aigc_watermark: bool = False
    subject_ref: str | None = None
    response_format: str = "url"


@dataclass(frozen=True)
class SpeechCommandArgs:
    text: str
    model: str | None = None
    voice: str = "English_expressive_narrator"
    speed: float | None = None
    volume: float | None = None
    pitch: float | None = None
    audio_format: str = "mp3"
    sample_rate: int = 32000
    bitrate: int = 128000
    channels: int = 1
    language: str | None = None
    subtitles: bool = False
    pronunciation: tuple[str, ...] = ()


@dataclass(frozen=True)
class VideoCommandArgs:
    prompt: str
    model: str | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    subject_image: str | None = None
    callback_url: str | None = None
    no_wait: bool = False
    poll_interval: int | None = None
    # V2 (MiniMax-H3)
    duration: int | None = None
    ratio: str | None = None
    reference_images: tuple[str, ...] = ()
    reference_videos: tuple[str, ...] = ()
    reference_audios: tuple[str, ...] = ()


@dataclass(frozen=True)
class MusicCoverCommandArgs:
    prompt: str
    audio: str | None = None
    audio_file: str | None = None
    lyrics: str | None = None
    seed: int | None = None
    model: str | None = None
    audio_format: str = "mp3"
    sample_rate: int = 44100
    bitrate: int = 256000
    channel: int = 2


@dataclass(frozen=True)
class FileCommandArgs:
    action: str
    file: str | None = None
    purpose: str = "retrieval"
    file_id: str | None = None


def parse_image_command(raw: str) -> ImageCommandArgs:
    values, positional = _parse_cli_args(
        raw,
        bool_flags={
            "--prompt-optimizer": "prompt_optimizer",
            "--promptOptimizer": "prompt_optimizer",
            "--aigc-watermark": "aigc_watermark",
            "--aigcWatermark": "aigc_watermark",
        },
        value_flags={
            "--prompt": "prompt",
            "--model": "model",
            "--aspect-ratio": "aspect_ratio",
            "--aspectRatio": "aspect_ratio",
            "--n": "n",
            "--seed": "seed",
            "--width": "width",
            "--height": "height",
            "--subject-ref": "subject_ref",
            "--subjectRef": "subject_ref",
            "--response-format": "response_format",
            "--responseFormat": "response_format",
        },
        optional_value_flags={"--subject-ref", "--subjectRef"},
        unsupported={
            "--out": "AstrBot 会自动保存并发送图片，不支持 --out 指定服务器路径。",
            "--out-dir": "AstrBot 会自动保存并发送图片，不支持 --out-dir。",
            "--out-prefix": "AstrBot 会自动保存并发送图片，不支持 --out-prefix。",
        },
        usage=_image_usage(),
    )
    prompt = str(values.get("prompt") or " ".join(positional)).strip()
    if not prompt:
        raise DirectCommandError(_image_usage())
    values["prompt"] = prompt
    _coerce_ints(values, ("n", "seed", "width", "height"))
    args = ImageCommandArgs(**values)
    if args.n < 1 or args.n > 9:
        raise DirectCommandError("--n 必须在 1 到 9 之间")
    if (args.width is None) != (args.height is None):
        raise DirectCommandError("--width 和 --height 必须同时提供")
    for name, value in {"width": args.width, "height": args.height}.items():
        if value is None:
            continue
        if value < 512 or value > 2048 or value % 8 != 0:
            raise DirectCommandError(f"--{name} 必须在 512-2048 之间且为 8 的倍数")
    if args.response_format not in {"url", "base64"}:
        raise DirectCommandError("--response-format 只支持 url 或 base64")
    return args


def parse_speech_command(raw: str) -> SpeechCommandArgs:
    values, positional = _parse_cli_args(
        raw,
        bool_flags={"--subtitles": "subtitles"},
        value_flags={
            "--text": "text",
            "--model": "model",
            "--voice": "voice",
            "--speed": "speed",
            "--volume": "volume",
            "--pitch": "pitch",
            "--format": "audio_format",
            "--sample-rate": "sample_rate",
            "--sampleRate": "sample_rate",
            "--bitrate": "bitrate",
            "--channels": "channels",
            "--language": "language",
            "--pronunciation": "pronunciation",
        },
        array_value_flags={"--pronunciation"},
        unsupported={
            "--text-file": "AstrBot 直接指令不读取服务器本地文本文件，请直接发送文本。",
            "--out": "AstrBot 会自动保存并发送语音，不支持 --out 指定服务器路径。",
            "--stream": "AstrBot 消息不支持 mmx CLI 的 --stream 原始音频流模式。",
        },
        usage=_speech_usage(),
    )
    text = str(values.get("text") or " ".join(positional)).strip()
    if not text:
        raise DirectCommandError(_speech_usage())
    values["text"] = text
    _coerce_numbers(values, ("speed", "volume", "pitch"))
    _coerce_ints(values, ("sample_rate", "bitrate", "channels"))
    if "pronunciation" in values:
        raw_pronunciation = values["pronunciation"]
        if isinstance(raw_pronunciation, list):
            values["pronunciation"] = tuple(str(v) for v in raw_pronunciation if v)
        elif raw_pronunciation:
            values["pronunciation"] = (str(raw_pronunciation),)
    args = SpeechCommandArgs(**values)
    if args.audio_format not in {
        "mp3",
        "pcm",
        "flac",
        "wav",
        "pcmu_raw",
        "pcmu_wav",
        "opus",
    }:
        raise DirectCommandError("--format 参数不支持")
    return args


def parse_music_cover_command(raw: str) -> MusicCoverCommandArgs:
    values, positional = _parse_cli_args(
        raw,
        bool_flags={},
        value_flags={
            "--prompt": "prompt",
            "--audio": "audio",
            "--audio-file": "audio_file",
            "--audioFile": "audio_file",
            "--lyrics": "lyrics",
            "--seed": "seed",
            "--model": "model",
            "--format": "audio_format",
            "--sample-rate": "sample_rate",
            "--sampleRate": "sample_rate",
            "--bitrate": "bitrate",
            "--channel": "channel",
        },
        optional_value_flags={"--audio", "--audio-file", "--audioFile"},
        unsupported={
            "--lyrics-file": "AstrBot 直接指令不读取服务器本地歌词文件，请使用 --lyrics 直接传歌词。",
            "--out": "AstrBot 会自动保存并发送翻唱音频，不支持 --out 指定服务器路径。",
            "--stream": "AstrBot 消息不支持 mmx CLI 的 --stream 原始音频流模式。",
        },
        usage=_music_cover_usage(),
    )
    prompt = str(values.get("prompt") or " ".join(positional)).strip()
    if not prompt:
        raise DirectCommandError(_music_cover_usage())
    values["prompt"] = prompt
    _coerce_ints(values, ("seed", "sample_rate", "bitrate", "channel"))
    args = MusicCoverCommandArgs(**values)
    if args.audio is not None and args.audio_file is not None:
        raise DirectCommandError("--audio 和 --audio-file 不能同时使用")
    if args.audio_format not in {"mp3", "wav", "pcm"}:
        raise DirectCommandError("--format 只支持 mp3、wav 或 pcm")
    if args.sample_rate <= 0 or args.bitrate <= 0:
        raise DirectCommandError("--sample-rate 和 --bitrate 必须大于 0")
    if args.channel not in {1, 2}:
        raise DirectCommandError("--channel 只支持 1 或 2")
    if args.model and args.model not in MUSIC_COVER_MODELS:
        raise DirectCommandError(
            f"--model 只支持: {', '.join(MUSIC_COVER_MODELS)}"
        )
    return args


def parse_file_command(raw: str) -> FileCommandArgs:
    text = _normalize_file_command_args(raw)
    if not text:
        raise DirectCommandError(_file_usage())

    action, _, rest = text.partition(" ")
    action = action.strip().lower()
    rest = rest.strip()

    if action == "upload":
        values, positional = _parse_cli_args(
            rest,
            bool_flags={},
            value_flags={"--file": "file", "--purpose": "purpose"},
            unsupported={},
            usage=_file_usage(),
        )
        file_path = str(values.get("file") or " ".join(positional)).strip()
        if not file_path:
            raise DirectCommandError("upload 需要 --file <路径>。\n\n" + _file_usage())
        return FileCommandArgs(
            action="upload",
            file=file_path,
            purpose=str(values.get("purpose") or "retrieval").strip() or "retrieval",
        )

    if action == "list":
        if rest:
            raise DirectCommandError("list 不需要额外参数。\n\n" + _file_usage())
        return FileCommandArgs(action="list")

    if action == "delete":
        values, positional = _parse_cli_args(
            rest,
            bool_flags={},
            value_flags={"--file-id": "file_id", "--fileId": "file_id"},
            unsupported={},
            usage=_file_usage(),
        )
        file_id = str(values.get("file_id") or " ".join(positional)).strip()
        if not file_id:
            raise DirectCommandError(
                "delete 需要 --file-id <id>。\n\n" + _file_usage()
            )
        return FileCommandArgs(action="delete", file_id=file_id)

    raise DirectCommandError(f"不支持的 file 子命令: {action}\n\n{_file_usage()}")


def _normalize_file_command_args(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    parts = text.split(maxsplit=2)
    first = parts[0].lstrip("/").lower()
    if first == "mmx":
        if len(parts) >= 2 and parts[1].lower() == "file":
            return parts[2].strip() if len(parts) > 2 else ""
        rest = text.split(maxsplit=1)
        return rest[1].strip() if len(rest) > 1 else ""
    if first == "file":
        rest = text.split(maxsplit=1)
        return rest[1].strip() if len(rest) > 1 else ""
    return text


def parse_video_command(raw: str) -> VideoCommandArgs:
    values, positional = _parse_cli_args(
        raw,
        bool_flags={"--no-wait": "no_wait", "--async": "no_wait"},
        value_flags={
            "--prompt": "prompt",
            "--model": "model",
            "--first-frame": "first_frame",
            "--firstFrame": "first_frame",
            "--last-frame": "last_frame",
            "--lastFrame": "last_frame",
            "--subject-image": "subject_image",
            "--subjectImage": "subject_image",
            "--callback-url": "callback_url",
            "--callbackUrl": "callback_url",
            "--poll-interval": "poll_interval",
            "--pollInterval": "poll_interval",
            "--duration": "duration",
            "--ratio": "ratio",
        },
        optional_value_flags={
            "--first-frame",
            "--firstFrame",
            "--last-frame",
            "--lastFrame",
            "--subject-image",
            "--subjectImage",
        },
        unsupported={
            "--download": "AstrBot 会自动下载并发送视频，不支持 --download 指定服务器路径。",
        },
        usage=_video_usage(),
    )
    prompt = str(values.get("prompt") or " ".join(positional)).strip()
    if not prompt:
        raise DirectCommandError(_video_usage())
    values["prompt"] = prompt
    _coerce_ints(values, ("poll_interval", "duration"))
    args = VideoCommandArgs(**values)
    if args.last_frame and not args.first_frame:
        raise DirectCommandError("--last-frame 需要同时提供 --first-frame")
    if args.subject_image and (args.first_frame or args.last_frame):
        raise DirectCommandError(
            "--subject-image 不能与 --first-frame/--last-frame 同时使用"
        )
    return args


def _parse_cli_args(
    raw: str,
    *,
    bool_flags: dict[str, str],
    value_flags: dict[str, str],
    unsupported: dict[str, str],
    usage: str,
    optional_value_flags: set[str] | None = None,
    array_value_flags: set[str] | None = None,
) -> tuple[dict[str, object], list[str]]:
    optional_value_flags = optional_value_flags or set()
    array_value_flags = array_value_flags or set()
    all_flags = [*bool_flags, *value_flags, *unsupported]
    if optional_value_flags:
        all_flags.extend(optional_value_flags)
    names = sorted(
        (flag.removeprefix("--") for flag in all_flags), key=len, reverse=True
    )
    pattern = re.compile(rf"(?<!\s)(--(?:{'|'.join(map(re.escape, names))})\b)")
    text = pattern.sub(r" \1", raw.strip())
    try:
        tokens = split_command_tokens(text)
    except ValueError as exc:
        raise DirectCommandError(f"参数解析失败: {exc}\n\n{usage}") from exc

    values: dict[str, object] = {}
    positional: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        flag, inline_value = _split_inline_value(token)
        if not flag.startswith("--"):
            positional.append(token)
            index += 1
            continue
        if flag in unsupported:
            raise DirectCommandError(unsupported[flag])
        if flag in bool_flags:
            values[bool_flags[flag]] = (
                _parse_bool(inline_value) if inline_value else True
            )
            index += 1
            continue
        if flag not in value_flags:
            raise DirectCommandError(f"不支持的参数: {flag}\n\n{usage}")
        attr = value_flags[flag]
        if inline_value is None:
            parts: list[str] = []
            index += 1
            while index < len(tokens) and not tokens[index].startswith("--"):
                parts.append(tokens[index])
                index += 1
            if not parts:
                if flag in optional_value_flags:
                    values[attr] = ""
                    continue
                raise DirectCommandError(f"{flag} 需要一个值。\n\n{usage}")
            value = " ".join(parts)
        else:
            value = inline_value
            index += 1
        value = value.strip()
        if flag in array_value_flags:
            existing = values.get(attr)
            if isinstance(existing, list):
                existing.append(value)
            else:
                values[attr] = [value]
        else:
            values[attr] = value
    return values, positional


def _split_inline_value(token: str) -> tuple[str, str | None]:
    if token.startswith("--") and "=" in token:
        flag, value = token.split("=", 1)
        return flag, value
    return token, None


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise DirectCommandError(f"布尔参数值无效: {value}")


def _coerce_ints(values: dict[str, object], names: tuple[str, ...]) -> None:
    for name in names:
        if name not in values:
            continue
        try:
            values[name] = int(str(values[name]))
        except ValueError as exc:
            raise DirectCommandError(f"{name} 必须是整数: {values[name]}") from exc


def _coerce_numbers(values: dict[str, object], names: tuple[str, ...]) -> None:
    for name in names:
        if name not in values:
            continue
        try:
            values[name] = float(str(values[name]))
        except ValueError as exc:
            raise DirectCommandError(f"{name} 必须是数字: {values[name]}") from exc


def _image_usage() -> str:
    return (
        "用法: /mmx image <描述> [--aspect-ratio 16:9] [--seed 42] "
        "[--width 1024 --height 1024] [--aigc-watermark]"
    )


def _speech_usage() -> str:
    return (
        "用法: /mmx speech <文本> [--voice <音色>] [--speed 1.0] "
        "[--format mp3] [--sample-rate 32000] [--pronunciation 文本/读音]"
    )


def _video_usage() -> str:
    return (
        "用法: /mmx video <描述> [--first-frame <图片>] [--last-frame <图片>] "
        "[--subject-image <图片>] [--no-wait]"
    )


def _music_cover_usage() -> str:
    return (
        "用法: /mmx music cover <风格描述> (--audio <URL> | --audio-file <路径>) "
        "[--lyrics <歌词>] [--format mp3]"
    )


def _file_usage() -> str:
    return (
        "用法:\n"
        "/mmx file upload --file <路径> [--purpose retrieval]\n"
        "/mmx file list\n"
        "/mmx file delete --file-id <id>"
    )
