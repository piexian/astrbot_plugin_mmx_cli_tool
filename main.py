"""MiniMax 多模态工具 — AstrBot 插件入口。

提供 LLM 工具和指令，覆盖 MiniMax 图片/视频/音乐生成、联网搜索、视觉理解和额度查询。

"""

from __future__ import annotations

import html
import re
import time as _time
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message import components as Comp
from astrbot.core.message.components import Record, Video
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from .mmx import MiniMaxClient
from .mmx.apis.image import ImageAPI
from .mmx.apis.video import VideoAPI
from .mmx.apis.music import MusicAPI
from .mmx.apis.search import SearchAPI
from .mmx.apis.vision import VisionAPI
from .mmx.apis.quota import QuotaAPI
from .mmx.apis.speech import SpeechAPI
from .mmx.files import FileAPI
from .mmx.attachment_input import extract_first_audio_input
from .mmx.vision_input import extract_image_input, extract_image_inputs
from .mmx.direct_command_args import (
    DirectCommandError,
    parse_file_command,
    parse_image_command,
    parse_music_cover_command,
    parse_speech_command,
    parse_video_command,
)
from .mmx.music_command import MusicCommandError, parse_music_command
from .mmx.errors import MiniMaxError, friendly_message
from .mmx.keypool import KeyPool
from .mmx.quota_usage import (
    is_video_quota_model,
    normalize_quota_models,
    resolve_used_percent,
)
from .mmx.utils import (
    get_shared_temp_dir,
    is_url,
    resolve_existing_data_path,
    resolve_image,
    resolve_subject_reference,
)
from .tools import (
    GenerateImageTool,
    GenerateVideoTool,
    QueryVideoTaskTool,
    DownloadVideoTool,
    GenerateMusicTool,
    MusicCoverTool,
    QueryBackgroundTaskTool,
    WebSearchTool,
    DescribeImageTool,
    CheckQuotaTool,
    SpeechSynthesizeTool,
    ListVoicesTool,
    UploadFileTool,
    ListFilesTool,
    DeleteFileTool,
)


def _clean_display_text(value: object, *, limit: int | None = None) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _extract_vision_text(result: dict) -> str:
    for key in ("content", "description", "text"):
        text = _clean_display_text(result.get(key))
        if text:
            return text

    choices = result.get("choices", [])
    if choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        text = _clean_display_text(message.get("content"))
        if text:
            return text

    data = result.get("data")
    if isinstance(data, dict):
        for key in ("content", "description", "text"):
            text = _clean_display_text(data.get(key))
            if text:
                return text

    return _clean_display_text(result, limit=2000)


def _file_size_text(value: object) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "未知大小"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _file_payload(result: dict) -> dict:
    item = result.get("file")
    return item if isinstance(item, dict) else result


def _quota_number_text(value: object) -> str:
    if isinstance(value, int):
        return str(value)
    return "未知"


def _format_video_quota_window(window: dict) -> str:
    has_counts = any(
        isinstance(window.get(key), int) for key in ("used", "remaining", "total")
    )
    if not has_counts:
        return "未知"
    used = _quota_number_text(window.get("used"))
    remaining = _quota_number_text(window.get("remaining"))
    total = _quota_number_text(window.get("total"))
    return f"{used} / {remaining}（{total}）"


def _format_quota_window(label: str, window: dict, *, is_video: bool = False) -> str:
    if window.get("unlimited") is True:
        return f"{label}: ∞"
    if is_video:
        text = f"{label}: {_format_video_quota_window(window)}"
    else:
        percent = resolve_used_percent(window)
        if isinstance(percent, int):
            text = f"{label}: 已用{percent}%"
        else:
            text = f"{label}: 未知"
    reset = _format_quota_reset_time(window.get("remains_time"))
    if reset:
        text += f"（{reset}后重置）"
    return text


def _format_quota_reset_time(value: object) -> str | None:
    if not isinstance(value, int) or value <= 0:
        return None
    total_minutes = value // 60000
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{hours}小时{minutes}分钟"
    if hours > 0:
        return f"{hours}小时"
    return f"{minutes}分钟"


def _merge_quota_window(target: dict, source: dict) -> None:
    for key in ("total", "used", "remaining"):
        value = source.get(key)
        if isinstance(value, int):
            target[key] = (target.get(key) or 0) + value
    reset_time = source.get("remains_time")
    if isinstance(reset_time, int):
        current_reset_time = target.get("remains_time")
        target["remains_time"] = (
            min(current_reset_time, reset_time)
            if isinstance(current_reset_time, int)
            else reset_time
        )
    if source.get("unlimited") is True:
        target["unlimited"] = True


def _finalize_merged_quota_window(window: dict) -> dict:
    if window.get("unlimited") is True:
        window["remaining_percent"] = 100
        return window
    total = window.get("total")
    remaining = window.get("remaining")
    if isinstance(total, int) and total > 0 and isinstance(remaining, int):
        window["remaining_percent"] = max(0, min(100, int(remaining / total * 100)))
    return window


def _merge_quota_models(models: list[dict]) -> dict[str, dict]:
    merged: dict[str, dict] = {}
    for model in models:
        name = str(model.get("model", "unknown"))
        target = merged.setdefault(name, {"current": {}, "weekly": {}})
        _merge_quota_window(target["current"], model["current"])
        _merge_quota_window(target["weekly"], model["weekly"])

    for model in merged.values():
        _finalize_merged_quota_window(model["current"])
        _finalize_merged_quota_window(model["weekly"])
    return merged


def _normalize_quota_command_args(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    parts = text.split(maxsplit=2)
    first = parts[0].lstrip("/").lower()
    if first == "mmx":
        if len(parts) >= 2 and parts[1].lower() == "quota":
            return parts[2].strip() if len(parts) > 2 else ""
        rest = text.split(maxsplit=1)
        return rest[1].strip() if len(rest) > 1 else ""
    if first == "quota":
        rest = text.split(maxsplit=1)
        return rest[1].strip() if len(rest) > 1 else ""
    return text


@filter.command_group("mmx")
def mmx_group() -> None:
    """MiniMax 多模态工具指令组"""


class Main(star.Star):
    """MiniMax multi-modal plugin — image, video, music, search, vision, quota."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)

        # 读取配置
        raw_keys = config.get("api_key", [])
        keys: list[str] = (
            [str(k).strip() for k in raw_keys if str(k).strip()]
            if isinstance(raw_keys, list)
            else []
        )
        region = str(config.get("region", "cn"))
        base_url_override = str(config.get("base_url", "")).strip() or None
        timeout = float(config.get("timeout", 300))
        self._video_poll_interval = int(config.get("video_poll_interval", 5))
        self._video_timeout = int(config.get("video_timeout", 600))
        self._default_image_model = str(config.get("default_image_model", "")).strip()
        self._default_video_model = str(config.get("default_video_model", "")).strip()
        self._default_video_sef_model = str(
            config.get("default_video_sef_model", "")
        ).strip()
        self._default_video_subject_model = str(
            config.get("default_video_subject_model", "")
        ).strip()
        self._default_speech_model = str(config.get("default_speech_model", "")).strip()
        self._default_music_model = str(config.get("default_music_model", "")).strip()
        self._default_music_cover_model = str(
            config.get("default_music_cover_model", "")
        ).strip()

        # 插件数据目录
        _plugin_name = getattr(self, "name", None) or "astrbot_plugin_mmx_cli_tool"
        self._plugin_data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / _plugin_name
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 缓存目录：生成的媒体文件只作中转，统一放 AstrBot 临时目录，无需自行清理
        self._cache_dir = Path(get_shared_temp_dir())
        # 是否允许 LLM 工具/直接指令读取 AstrBot 临时目录（可在配置中收紧）
        self._allow_temp_dir_reads = bool(config.get("allow_astrbot_temp_dir", True))
        self._extra_dirs = [str(self._cache_dir)] if self._allow_temp_dir_reads else []

        # 创建客户端 — 支持单 Key 和多 Key 池两种模式
        if not keys:
            logger.warning("[mmx] api_key 未配置，插件将无法调用 API")
            self._key_pool = None
            client_kwargs: dict = {
                "api_key": "",
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }
        elif len(keys) > 1:
            self._key_pool = KeyPool(keys, region)
            logger.info(f"[mmx] 多 Key 模式已启用，共 {len(keys)} 个 Key")
            client_kwargs = {
                "key_getter": self._key_pool.get_key,
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }
        else:
            self._key_pool = None
            client_kwargs = {
                "api_key": keys[0],
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }

        self._client = MiniMaxClient(**client_kwargs)

        self._image = ImageAPI(self._client)
        self._video = VideoAPI(self._client)
        self._music = MusicAPI(self._client)
        self._search = SearchAPI(self._client)
        self._vision = VisionAPI(self._client)
        self._quota = QuotaAPI(self._client)
        self._speech = SpeechAPI(self._client)
        self._files = FileAPI(self._client)

        # 插件数据目录路径（字符串）
        _data_dir = str(self._plugin_data_dir)
        _cache_dir = str(self._cache_dir)
        # AstrBot 临时目录：聊天图片下载后存放于此，允许 LLM 工具读取
        _extra_dirs = self._extra_dirs

        # 注册 LLM 工具
        context.add_llm_tools(
            GenerateImageTool(
                self._image,
                self._default_image_model,
                _data_dir,
                extra_allowed_dirs=_extra_dirs,
            ),
            GenerateVideoTool(
                self._video,
                self._video_poll_interval,
                self._video_timeout,
                self._default_video_model,
                self._default_video_sef_model,
                self._default_video_subject_model,
                _data_dir,
                extra_allowed_dirs=_extra_dirs,
            ),
            QueryVideoTaskTool(self._video),
            DownloadVideoTool(self._video, _data_dir, cache_dir=_cache_dir),
            GenerateMusicTool(
                self._music, _data_dir, self._default_music_model, cache_dir=_cache_dir
            ),
            MusicCoverTool(
                self._music,
                _data_dir,
                self._default_music_cover_model,
                cache_dir=_cache_dir,
                extra_allowed_dirs=_extra_dirs,
            ),
            QueryBackgroundTaskTool(),
            WebSearchTool(self._search),
            DescribeImageTool(self._vision, _data_dir, extra_allowed_dirs=_extra_dirs),
            CheckQuotaTool(self._quota, keys),
            SpeechSynthesizeTool(
                self._speech,
                _data_dir,
                self._default_speech_model,
                cache_dir=_cache_dir,
            ),
            ListVoicesTool(self._speech),
            UploadFileTool(self._files, _data_dir, extra_allowed_dirs=_extra_dirs),
            ListFilesTool(self._files),
            DeleteFileTool(self._files),
        )

    async def terminate(self) -> None:
        if self._client:
            await self._client.close()

    @mmx_group.command("speech")
    async def mmx_speech(self, event: AstrMessageEvent, *, text: str = ""):
        """语音合成。用法: /mmx speech <文本> [--voice <音色>] [--format mp3]"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = text or (parts[1] if len(parts) > 1 else "")
        try:
            args = parse_speech_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx speech <文本> [--voice <音色>] [--format mp3]\n"
                "例如: /mmx speech 你好世界 --speed 1.1 --subtitles"
            )
            return
        try:
            result = await self._speech.synthesize(
                text=args.text,
                model=args.model or self._default_speech_model,
                voice=args.voice,
                speed=args.speed,
                volume=args.volume,
                pitch=args.pitch,
                audio_format=args.audio_format,
                sample_rate=args.sample_rate,
                bitrate=args.bitrate,
                channels=args.channels,
                language=args.language,
                subtitles=args.subtitles,
                pronunciation=args.pronunciation,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Speech error: {e}")
            yield event.plain_result(f"语音合成失败: {e}")
            return

        out_path = (
            self._cache_dir
            / f"mmx_speech_{int(_time.time() * 1000)}.{args.audio_format}"
        )
        try:
            saved_path = self._speech.save(result, str(out_path))
        except Exception as e:
            logger.warning(f"[mmx] 语音保存失败: {e}")
            yield event.plain_result("语音合成完成，但保存音频失败。")
            return

        if saved_path:
            yield event.chain_result([Record(file=saved_path)])

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mmx_group.command("file")
    async def mmx_file(self, event: AstrMessageEvent, *, args: str = ""):
        """文件管理。用法: /mmx file upload|list|delete"""
        raw_args = args or event.message_str
        try:
            parsed = parse_file_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return

        try:
            if parsed.action == "upload":
                safe_file = resolve_existing_data_path(
                    str(self._plugin_data_dir),
                    parsed.file or "",
                    self._extra_dirs,
                )
                if safe_file is None:
                    allowed = [f"- {self._plugin_data_dir}"]
                    allowed.extend(f"- {d}" for d in self._extra_dirs)
                    yield event.plain_result(
                        "file 必须位于以下目录内：\n" + "\n".join(allowed)
                    )
                    return
                if not safe_file.is_file():
                    yield event.plain_result(f"文件不存在: {parsed.file}")
                    return
                result = await self._files.upload(
                    str(safe_file),
                    purpose=parsed.purpose,
                )
                item = _file_payload(result)
                file_id = item.get("file_id") or item.get("id") or "未知"
                filename = item.get("filename") or item.get("name") or parsed.file
                lines = [
                    "文件上传完成。",
                    f"file_id: {file_id}",
                    f"filename: {filename}",
                    f"purpose: {item.get('purpose') or parsed.purpose}",
                ]
                if "bytes" in item:
                    lines.append(f"size: {_file_size_text(item.get('bytes'))}")
                yield event.plain_result("\n".join(lines))
                return

            if parsed.action == "list":
                result = await self._files.list()
                files = result.get("files", [])
                if not files:
                    yield event.plain_result("暂无已上传文件。")
                    return
                lines = ["MiniMax 文件列表:"]
                for item in files[:20]:
                    file_id = item.get("file_id") or item.get("id") or "未知"
                    filename = item.get("filename") or item.get("name") or "未知文件"
                    purpose = item.get("purpose") or "-"
                    size = _file_size_text(item.get("bytes"))
                    lines.append(f"- {file_id} | {filename} | {purpose} | {size}")
                if len(files) > 20:
                    lines.append(f"... 还有 {len(files) - 20} 个文件未显示")
                yield event.plain_result("\n".join(lines))
                return

            result = await self._files.delete(parsed.file_id or "")
            file_id = result.get("file_id") or parsed.file_id
            yield event.plain_result(f"文件已删除: {file_id}")
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
        except Exception as e:
            logger.error(f"[mmx] File command error: {e}")
            yield event.plain_result(f"文件操作失败: {e}")

    @mmx_group.command("image")
    async def mmx_image(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成图片。用法: /mmx image <描述> [--aspect-ratio 16:9] [--seed 42]"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = prompt or (parts[1] if len(parts) > 1 else "")
        try:
            args = parse_image_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx image <描述> [--aspect-ratio 16:9] [--seed 42]\n"
                "例如: /mmx image a cute cat --aigc-watermark"
            )
            return
        try:
            messages = event.get_messages()
            subject_ref_value = args.subject_ref
            subject_ref_trusted = False
            if subject_ref_value == "":
                subject_refs, _ = await extract_image_inputs(
                    messages,
                    image_type=Comp.Image,
                    reply_type=Comp.Reply,
                    event=event,
                    limit=1,
                )
                subject_ref_value = subject_refs[0] if subject_refs else None
                subject_ref_trusted = bool(subject_ref_value)
                if not subject_ref_value:
                    yield event.plain_result(
                        "请为 --subject-ref 提供图片 URL 或插件数据目录内路径，或附带/引用一张图片后重试。"
                    )
                    return
            subject_reference = (
                await resolve_subject_reference(
                    subject_ref_value,
                    data_dir=None
                    if subject_ref_trusted
                    else str(self._plugin_data_dir),
                    allow_trusted_local_path=subject_ref_trusted,
                    extra_allowed_dirs=self._extra_dirs,
                )
                if subject_ref_value
                else None
            )
            result = await self._image.generate(
                prompt=args.prompt,
                model=args.model or self._default_image_model or None,
                n=args.n,
                aspect_ratio=args.aspect_ratio,
                width=args.width,
                height=args.height,
                response_format=args.response_format,
                prompt_optimizer=args.prompt_optimizer,
                aigc_watermark=args.aigc_watermark,
                subject_reference=subject_reference,
                seed=args.seed,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Image error: {e}")
            yield event.plain_result(f"图片生成失败: {e}")
            return

        data = result.get("data", {})
        urls = data.get("image_urls", [])
        base64_images = data.get("image_base64", [])
        if not urls and not base64_images:
            yield event.plain_result("图片生成失败：未返回链接。")
            return

        saved = []
        try:
            saved = await self._image.save(result, out_dir=str(self._cache_dir))
        except Exception as e:
            logger.warning(f"[mmx] 图片下载失败，将尝试直接发送远程图片: {e}")

        if saved:
            yield event.image_result(saved[0])
            return

        if urls:
            yield event.image_result(urls[0])

    @mmx_group.command("video")
    async def mmx_video(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成视频。用法: /mmx video <描述> [--first-frame <图片>] [--no-wait]"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = prompt or (parts[1] if len(parts) > 1 else "")
        try:
            args = parse_video_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx video <描述> [--first-frame <图片>] [--no-wait]"
            )
            return
        try:
            first_frame_ref = args.first_frame
            last_frame_ref = args.last_frame
            subject_image_ref = args.subject_image
            first_frame_trusted = False
            last_frame_trusted = False
            subject_image_trusted = False
            frame_flags_present = (
                args.first_frame is not None or args.last_frame is not None
            )
            image_refs: list[str] = []
            if any(
                value == ""
                for value in (args.first_frame, args.last_frame, args.subject_image)
            ):
                image_refs, _ = await extract_image_inputs(
                    event.get_messages(),
                    image_type=Comp.Image,
                    reply_type=Comp.Reply,
                    event=event,
                    limit=2,
                )
            if args.subject_image == "":
                if frame_flags_present:
                    yield event.plain_result(
                        "--subject-image 不能与 --first-frame/--last-frame 同时使用"
                    )
                    return
                if image_refs:
                    subject_image_ref = image_refs.pop(0)
                    subject_image_trusted = True
                else:
                    yield event.plain_result(
                        "请为 --subject-image 提供图片 URL 或插件数据目录内路径，或附带/引用图片后重试。"
                    )
                    return
            if args.first_frame == "":
                if image_refs:
                    first_frame_ref = image_refs.pop(0)
                    first_frame_trusted = True
                if not first_frame_ref:
                    yield event.plain_result(
                        "请为 --first-frame 提供图片 URL 或插件数据目录内路径，或附带/引用图片后重试。"
                    )
                    return
            if args.last_frame == "":
                if image_refs:
                    last_frame_ref = image_refs.pop(0)
                    last_frame_trusted = True
                if not last_frame_ref:
                    yield event.plain_result(
                        "请为 --last-frame 提供图片 URL 或插件数据目录内路径，或附带/引用图片后重试。"
                    )
                    return
            if args.subject_image is not None and frame_flags_present:
                yield event.plain_result(
                    "--subject-image 不能与 --first-frame/--last-frame 同时使用"
                )
                return
            if last_frame_ref and not first_frame_ref:
                yield event.plain_result("--last-frame 需要同时提供 --first-frame")
                return
            first_frame = (
                await resolve_image(
                    first_frame_ref,
                    data_dir=None
                    if first_frame_trusted
                    else str(self._plugin_data_dir),
                    allow_trusted_local_path=first_frame_trusted,
                    extra_allowed_dirs=self._extra_dirs,
                )
                if first_frame_ref
                else None
            )
            last_frame = (
                await resolve_image(
                    last_frame_ref,
                    data_dir=None
                    if last_frame_trusted
                    else str(self._plugin_data_dir),
                    allow_trusted_local_path=last_frame_trusted,
                    extra_allowed_dirs=self._extra_dirs,
                )
                if last_frame_ref
                else None
            )
            subject_reference = (
                [
                    {
                        "type": "character",
                        "image": [
                            await resolve_image(
                                subject_image_ref,
                                data_dir=None
                                if subject_image_trusted
                                else str(self._plugin_data_dir),
                                allow_trusted_local_path=subject_image_trusted,
                                extra_allowed_dirs=self._extra_dirs,
                            )
                        ],
                    }
                ]
                if subject_image_ref
                else None
            )
            selected_model = (
                args.model
                or (
                    self._default_video_sef_model
                    if first_frame and last_frame
                    else self._default_video_subject_model
                    if subject_reference
                    else self._default_video_model
                )
                or None
            )
            is_v2_cmd = selected_model == "MiniMax-H3"
            if is_v2_cmd:
                result = await self._video.generate(
                    prompt=args.prompt,
                    model=selected_model,
                    first_frame_image=first_frame,
                    last_frame_image=last_frame,
                    duration=args.duration,
                    ratio=args.ratio,
                    callback_url=args.callback_url,
                )
            else:
                result = await self._video.generate(
                    prompt=args.prompt,
                    model=selected_model,
                    first_frame_image=first_frame,
                    last_frame_image=last_frame,
                    subject_reference=subject_reference,
                    callback_url=args.callback_url,
                )
            task_id = result.get("task_id", "")
            if not task_id:
                yield event.plain_result("视频任务提交失败：未返回任务 ID。")
                return
            if args.no_wait:
                yield event.plain_result(
                    f"视频任务已提交。task_id={task_id}，可稍后查询生成结果。"
                )
                return

            yield event.plain_result(f"⏳ 视频生成中（最长 {self._video_timeout}s）...")
            try:
                final = await self._video.wait_for_completion(
                    task_id,
                    poll_interval=args.poll_interval or self._video_poll_interval,
                    timeout=self._video_timeout,
                    model=selected_model,
                )
                fid = final.get("file_id", "")
                if not fid:
                    yield event.plain_result("视频生成完成，但未返回可下载文件。")
                    return

                try:
                    video_path = str(self._cache_dir / f"mmx_video_{task_id}.mp4")
                    saved = await self._video.download(fid, video_path)
                    yield event.chain_result([Video(file=saved)])
                except Exception as e:
                    logger.warning(f"[mmx] 视频下载失败: {e}")
                    yield event.plain_result(
                        "视频生成完成，但下载失败，无法直接发送视频。"
                    )
            except TimeoutError:
                yield event.plain_result(
                    f"⏰ 视频生成超时。task_id={task_id}，请前往 MiniMax 控制台查看。"
                )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
        except Exception as e:
            logger.error(f"[mmx] Video error: {e}")
            yield event.plain_result(f"视频生成失败: {e}")

    @mmx_group.command("music")
    async def mmx_music(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成音乐。用法: /mmx music <描述> (--lyrics <歌词> | --instrumental | --lyrics-optimizer)"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = prompt or (parts[1] if len(parts) > 1 else "")
        stripped = raw_args.strip()
        if stripped == "cover" or stripped.startswith("cover "):
            async for result in self._handle_music_cover(event, raw_args):
                yield result
            return
        try:
            args = parse_music_command(raw_args)
        except MusicCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx music <描述> (--lyrics <歌词> | --instrumental | --lyrics-optimizer)\n"
                "例如: /mmx music 欢乐电子乐 --lyrics-optimizer\n"
                "例如: /mmx music 电影感管弦乐 --instrumental"
            )
            return
        try:
            result = await self._music.generate(
                prompt=args.prompt,
                lyrics=args.lyrics,
                is_instrumental=args.instrumental,
                lyrics_optimizer=args.lyrics_optimizer,
                vocals=args.vocals,
                genre=args.genre,
                mood=args.mood,
                instruments=args.instruments,
                tempo=args.tempo,
                bpm=args.bpm,
                key=args.key,
                avoid=args.avoid,
                use_case=args.use_case,
                structure=args.structure,
                references=args.references,
                extra=args.extra,
                model=args.model or self._default_music_model or None,
                output_format=args.output_format,
                audio_format=args.audio_format,
                sample_rate=args.sample_rate,
                bitrate=args.bitrate,
                aigc_watermark=args.aigc_watermark,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Music error: {e}")
            yield event.plain_result(f"音乐生成失败: {e}")
            return

        saved_path = None
        out_path = (
            self._cache_dir
            / f"mmx_music_{int(_time.time() * 1000)}.{args.audio_format}"
        )
        try:
            saved_path = self._music.save(result, str(out_path))
        except Exception as e:
            logger.warning(f"[mmx] 音乐保存失败: {e}")
            audio_url = result.get("data", {}).get("audio_url", "")
            if audio_url:
                yield event.chain_result([Record(file=audio_url)])
                return
            yield event.plain_result("音乐生成完成，但未返回可发送音频。")
            return

        if saved_path:
            yield event.chain_result([Record(file=saved_path)])

    async def _handle_music_cover(self, event: AstrMessageEvent, raw_args: str):
        text = raw_args.strip()
        if text == "cover":
            cover_args_text = ""
        else:
            cover_args_text = text[5:].strip() if text.startswith("cover ") else ""
        try:
            args = parse_music_cover_command(cover_args_text)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        try:
            audio_ref = args.audio
            audio_file_ref = args.audio_file
            audio_file_trusted = False
            if not audio_ref and not audio_file_ref:
                attachment_ref, _ = await extract_first_audio_input(
                    event.get_messages(),
                    record_type=Comp.Record,
                    file_type=Comp.File,
                    reply_type=Comp.Reply,
                    event=event,
                )
                if attachment_ref:
                    if is_url(attachment_ref):
                        audio_ref = attachment_ref
                    else:
                        audio_file_ref = attachment_ref
                        audio_file_trusted = True
            if not audio_ref and not audio_file_ref:
                yield event.plain_result(
                    "请提供 --audio <URL>，或附带/引用一个音频文件后使用 /mmx music cover。"
                )
                return
            result = await self._music.cover(
                model=args.model or self._default_music_cover_model or None,
                prompt=args.prompt,
                audio=audio_ref,
                audio_file=audio_file_ref,
                data_dir=None
                if audio_file_trusted
                else str(self._plugin_data_dir),
                allow_trusted_local_path=audio_file_trusted,
                extra_allowed_dirs=self._extra_dirs,
                lyrics=args.lyrics,
                seed=args.seed,
                audio_format=args.audio_format,
                sample_rate=args.sample_rate,
                bitrate=args.bitrate,
                channel=args.channel,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Music cover error: {e}")
            yield event.plain_result(f"翻唱生成失败: {e}")
            return

        saved_path = None
        out_path = (
            self._cache_dir
            / f"mmx_music_cover_{int(_time.time() * 1000)}.{args.audio_format}"
        )
        try:
            saved_path = self._music.save(result, str(out_path))
        except Exception as e:
            logger.warning(f"[mmx] 翻唱保存失败: {e}")
            audio_url = result.get("data", {}).get("audio_url", "")
            if audio_url:
                yield event.chain_result([Record(file=audio_url)])
                return
            yield event.plain_result("翻唱生成完成，但未返回可发送音频。")
            return

        if saved_path:
            yield event.chain_result([Record(file=saved_path)])

    @mmx_group.command("search")
    async def mmx_search(self, event: AstrMessageEvent, *, query: str = ""):
        """联网搜索。用法: /mmx search <查询>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        query = query or (parts[1] if len(parts) > 1 else "")
        if not query:
            yield event.plain_result(
                "用法: /mmx search <查询>。例如: /mmx search 今天天气"
            )
            return
        try:
            result = await self._search.query(query)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Search error: {e}")
            yield event.plain_result(f"搜索失败: {e}")
            return

        items = result.get("organic", result.get("data", result.get("results", [])))
        if not items:
            yield event.plain_result("🔍 未找到相关结果。")
            return

        lines = ["🔍 搜索结果:"]
        for i, item in enumerate(items[:5], 1):
            if isinstance(item, dict):
                title = _clean_display_text(item.get("title"), limit=80) or f"结果 {i}"
                url = _clean_display_text(item.get("url") or item.get("link"))
                snippet = _clean_display_text(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("content")
                    or item.get("description"),
                    limit=180,
                )
                lines.append(f"\n{i}. **{title}**")
                if url:
                    lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet[:200]}")
        yield event.plain_result("\n".join(lines))

    @mmx_group.command("vision")
    async def mmx_vision(self, event: AstrMessageEvent, *, prompt: str = ""):
        """图片理解。用法: /mmx vision（支持当前消息带图或引用图片）"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "Describe the image.")

        image_input, saw_image = await extract_image_input(
            event.get_messages(),
            image_type=Comp.Image,
            reply_type=Comp.Reply,
            event=event,
        )
        if not image_input:
            if saw_image:
                yield event.plain_result(
                    "检测到了图片，但当前无法在本地解析该图片。请改为直接发送图片，或引用一张仍可访问的图片后重试。"
                )
                return
            yield event.plain_result(
                "请附带一张图片，或引用一张图片后再发送 /mmx vision 指令。"
            )
            return

        try:
            result = await self._vision.describe(
                image=image_input,
                prompt=prompt,
                allow_trusted_local_path=True,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Vision error: {e}")
            yield event.plain_result(f"图片理解失败: {e}")
            return

        desc = _extract_vision_text(result)
        yield event.plain_result(f"🖼️ 图片分析:\n\n{desc}")

    @mmx_group.command("quota")
    async def mmx_quota(self, event: AstrMessageEvent, *, index: str = ""):
        """查询 MiniMax API 额度。用法: /mmx quota [序号] 或 /mmx quota page <页码>
        不带参数分页显示 Key 详情。带序号（如 /mmx quota 1）显示指定 Key 详情。
        """
        # 收集所有 Key
        all_keys: list[tuple[int, str]] = []  # (index, key)
        if self._key_pool is not None:
            for s in self._key_pool._states:
                all_keys.append((s.index, s.key))
        else:
            all_keys.append((0, self._client._api_key or ""))

        page_size = 3
        raw_args = _normalize_quota_command_args(index or event.message_str)
        page = 1
        selected_index: int | None = None

        if raw_args:
            parts = raw_args.split()
            command = parts[0].lower()
            if command in {"page", "p"}:
                if len(parts) > 2:
                    yield event.plain_result("用法: /mmx quota page <页码>")
                    return
                if len(parts) == 2:
                    try:
                        page = int(parts[1])
                    except ValueError:
                        yield event.plain_result("页码无效，请输入数字。如 /mmx quota page 2")
                        return
                if page < 1:
                    yield event.plain_result("页码必须大于 0。")
                    return
            else:
                try:
                    selected_index = int(raw_args) - 1
                except ValueError:
                    yield event.plain_result(
                        "序号无效，请输入数字。如 /mmx quota 1；翻页用 /mmx quota page 2"
                    )
                    return

        # 指定序号则只查该 Key
        if selected_index is not None:
            filtered = [(i, k) for i, k in all_keys if i == selected_index]
            if not filtered:
                yield event.plain_result(
                    f"Key 序号 {selected_index + 1} 不存在，共 {len(all_keys)} 个 Key。"
                )
                return
            keys_to_check = filtered
        else:
            total_pages = max(1, (len(all_keys) + page_size - 1) // page_size)
            if page > total_pages:
                yield event.plain_result(
                    f"页码 {page} 不存在，共 {total_pages} 页，{len(all_keys)} 个 Key。"
                )
                return
            start = (page - 1) * page_size
            keys_to_check = all_keys[start : start + page_size]

        import asyncio

        async def _fetch(api_key: str):
            try:
                result = await self._quota.info(api_key)
                return normalize_quota_models(result.get("model_remains", []))
            except Exception as e:
                logger.warning(f"[mmx] 额度查询失败: {e}")
            return []

        tasks = [_fetch(k) for _, k in keys_to_check]
        results = await asyncio.gather(*tasks)

        lines: list[str] = []
        paged_multi_key = selected_index is None and len(all_keys) > 1
        if paged_multi_key:
            total_pages = max(1, (len(all_keys) + page_size - 1) // page_size)
            lines.append(
                f"💰 MiniMax Key 额度（第 {page}/{total_pages} 页，"
                f"每页最多 {page_size} 个，共 {len(all_keys)} 个 Key）:"
            )

        for (ki, key), model_remains in zip(keys_to_check, results):
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
            if paged_multi_key:
                lines.append("")
                lines.append(f"Key [{ki + 1}] {masked}:")
            else:
                lines.append(f"💰 Key [{ki + 1}] {masked} 额度:")
            if not model_remains:
                lines.append("查询失败或无额度信息。")
            else:
                for m in model_remains:
                    is_video = is_video_quota_model(m["model"])
                    lines.append(f"- {m['model']}")
                    lines.append(
                        f"  {_format_quota_window('五小时额度', m['current'], is_video=is_video)}"
                    )
                    lines.append(
                        f"  {_format_quota_window('周额度', m['weekly'], is_video=is_video)}"
                    )
        if paged_multi_key:
            total_pages = max(1, (len(all_keys) + page_size - 1) // page_size)
            if page < total_pages:
                lines.append(f"\n下一页: /mmx quota page {page + 1}")
            if page > 1:
                lines.append(f"上一页: /mmx quota page {page - 1}")
        yield event.plain_result("\n".join(lines))
