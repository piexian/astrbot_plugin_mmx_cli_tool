"""Public SDK v1 facade. No client/config/property passthrough."""

from __future__ import annotations

import asyncio
import copy
import inspect
import math
import uuid
from functools import wraps
from pathlib import Path

from .apis.transcription import TranscriptionOptions
from .utils import (
    is_url,
    resolve_data_path,
    resolve_image,
    resolve_local_input_path,
    resolve_subject_reference,
)
from .video_business import validate_h3_subject, validate_video_mode

FEATURES = (
    "image.generate",
    "image.search",
    "image.reverse_search",
    "video.generate",
    "video.tasks",
    "audio.synthesize",
    "audio.transcribe",
    "audio.voices",
    "audio.music_generate",
    "audio.sound_effect",
    "web.search",
    "image.describe",
    "file.upload",
    "file.list",
    "file.delete",
    "quota.query",
)


class ServiceError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _text(value, name: str, *, max_length: int | None = None):
    if (
        not isinstance(value, str)
        or not value.strip()
        or (max_length and len(value) > max_length)
    ):
        raise ValueError(
            f"{name} 必须为非空字符串"
            + (f"，最长 {max_length} 字符" if max_length else "")
        )
    return value


def _timeout(value):
    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError("timeout 必须为有限非负秒数或 None")


def _optional_texts(options, names):
    for name in names:
        if options.get(name) is not None and not isinstance(options[name], str):
            raise ValueError(f"{name} 必须为字符串")


def _options(method, options: dict, **fixed):
    """Reject unknown/duplicate kwargs before any paid request or background admission."""
    inspect.signature(method).bind(**fixed, **options)


def _business(method):
    @wraps(method)
    async def call(self, *args, **kwargs):
        self._check()
        operation = (asyncio.current_task(), asyncio.Event())
        self._active.add(operation)
        try:
            return copy.deepcopy(await method(self, *args, **kwargs))
        finally:
            self._active.discard(operation)
            operation[1].set()

    return call


class MiniMaxService:
    def __init__(self, plugin, *, configured: bool):
        self._plugin = plugin
        self._instance_id = uuid.uuid4().hex
        self._configured = configured
        self._state = "initializing"
        self._reason = None
        self._changed = asyncio.Event()
        self._closing = asyncio.Event()
        self._active: set[tuple[asyncio.Task, asyncio.Event]] = set()

    def get_status(self) -> dict:
        return {
            "api_version": 1,
            "instance_id": self._instance_id,
            "state": self._state,
            "ready": self._state == "ready",
            "reason": self._reason,
        }

    def capabilities(self) -> dict:
        return {"api_version": 1, "features": list(FEATURES)}

    def _initialize(self):
        if self._state != "initializing":
            return
        self._state = "ready" if self._configured else "unavailable"
        self._reason = None if self._configured else "missing_api_key"
        self._changed.set()

    def _check_closed(self):
        if self._state in ("closing", "closed"):
            raise ServiceError("service_closed")

    def _check(self):
        self._check_closed()
        if self._state != "ready":
            raise ServiceError("not_ready")

    async def wait_ready(self, timeout=None) -> dict:
        self._check_closed()
        _timeout(timeout)

        async def wait():
            while True:
                self._check_closed()
                if self._state == "ready":
                    return self.get_status()
                self._changed.clear()
                await self._changed.wait()

        try:
            if self._state == "ready":
                return self.get_status()
            return await asyncio.wait_for(wait(), timeout=timeout)
        except TimeoutError:
            self._check_closed()
            raise

    def _begin_close(self):
        if self._state == "closed":
            return
        self._state, self._reason = "closing", "service_closed"
        self._closing.set()
        self._changed.set()

    async def _drain(self):
        current = asyncio.current_task()
        operations = [
            (task, done) for task, done in self._active if task is not current
        ]
        for task, _ in operations:
            task.cancel()
        # Consumers may continue after cancellation; wait only for their SDK calls.
        await asyncio.gather(
            *(done.wait() for _, done in operations), return_exceptions=True
        )

    def _finish_close(self):
        self._state, self._reason = "closed", "service_closed"
        self._closing.set()
        self._changed.set()

    @_business
    async def generate_image(self, prompt, *, subject_ref=None, **options) -> dict:
        p = self._plugin
        _text(prompt, "prompt")
        # Raw subject_reference is not a trusted-local input; normalize each image below.
        refs = options.pop("subject_reference", None)
        if refs and subject_ref:
            raise ValueError("subject_ref 与 subject_reference 不能同时使用")
        options["model"] = options.get("model") or p._default_image_model or None
        _options(p._image.generate, options, prompt=prompt, subject_reference=None)
        _optional_texts(options, ("model", "aspect_ratio"))
        if subject_ref is not None:
            _text(subject_ref, "subject_ref")
        if options.get("seed") is not None and type(options["seed"]) is not int:
            raise ValueError("seed 必须为整数")
        n = options.get("n", 1)
        if type(n) is not int or not 1 <= n <= 9:
            raise ValueError("n 必须为 1-9 的整数")
        width, height = options.get("width"), options.get("height")
        if (width is None) != (height is None):
            raise ValueError("width 和 height 必须同时提供")
        for size in (width, height):
            if size is not None and (
                type(size) is not int or not 512 <= size <= 2048 or size % 8
            ):
                raise ValueError("尺寸必须为 512-2048 且为 8 的倍数")
        if options.get("response_format", "url") not in ("url", "base64"):
            raise ValueError("response_format 必须为 url 或 base64")
        for name in ("prompt_optimizer", "aigc_watermark"):
            if name in options and type(options[name]) is not bool:
                raise ValueError(f"{name} 必须为布尔值")
        if subject_ref:
            refs = await resolve_subject_reference(
                subject_ref,
                data_dir=str(p._plugin_data_dir),
                extra_allowed_dirs=p._extra_dirs,
            )
        elif refs:
            refs = await self._image_refs(refs)
        self._check()
        return await p._image.generate(prompt, subject_reference=refs, **options)

    async def _image_refs(self, refs):
        if not isinstance(refs, list):
            raise ValueError("subject_reference 必须为字典列表")
        resolved = []
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) - {
                "type",
                "image",
                "image_url",
                "image_file",
            }:
                raise ValueError("subject_reference 项无效")
            item = {"type": ref.get("type", "character")}
            fields = [k for k in ("image", "image_url", "image_file") if k in ref]
            if len(fields) != 1:
                raise ValueError("subject_reference 项需要一个图片字段")
            key = fields[0]
            value = ref[key]
            if isinstance(value, list):
                item[key] = [await self._resolve_image(v) for v in value]
            else:
                item[key] = await self._resolve_image(value)
            resolved.append(item)
        return resolved

    async def _resolve_image(self, value):
        _text(value, "image")
        p = self._plugin
        return await resolve_image(
            value, data_dir=str(p._plugin_data_dir), extra_allowed_dirs=p._extra_dirs
        )

    @_business
    async def generate_video(self, prompt, *, subject_image=None, **options) -> dict:
        p = self._plugin
        _text(prompt, "prompt")
        refs = options.pop("subject_reference", None)
        first, last = options.get("first_frame_image"), options.get("last_frame_image")
        validate_video_mode(first, last, subject_image or refs, options.get("model"))
        if subject_image and refs:
            raise ValueError("subject_image 与 subject_reference 不能同时使用")
        options["model"] = (
            options.get("model")
            or (
                p._default_video_sef_model
                if first and last
                else p._default_video_subject_model
                if subject_image or refs
                else p._default_video_model
            )
            or None
        )
        model = options["model"]
        _options(p._video.generate, options, prompt=prompt, subject_reference=None)
        _optional_texts(options, ("model", "ratio", "callback_url"))
        validate_video_mode(first, last, subject_image or refs, model)
        validate_h3_subject(model, subject_image or refs)
        v2_names = (
            "reference_images",
            "reference_videos",
            "reference_audios",
            "duration",
            "ratio",
        )
        if model != "MiniMax-H3" and any(
            options.get(name) is not None for name in v2_names
        ):
            raise ValueError("参考媒体、duration 和 ratio 仅支持 MiniMax-H3")
        if options.get("duration") is not None and type(options["duration"]) is not int:
            raise ValueError("duration 必须为整数")
        for name in ("first_frame_image", "last_frame_image"):
            if options.get(name):
                options[name] = await self._resolve_image(options[name])
        for name in ("reference_images", "reference_videos", "reference_audios"):
            if options.get(name) is not None and not isinstance(options[name], list):
                raise ValueError(f"{name} 必须为列表")
        if options.get("reference_images"):
            options["reference_images"] = [
                await self._resolve_image(v) for v in options["reference_images"]
            ]
        for name in ("reference_videos", "reference_audios"):
            for value in options.get(name) or []:
                if not isinstance(value, str) or not (
                    is_url(value) or value.startswith("mm_file://")
                ):
                    raise ValueError(f"{name} 仅支持 URL 或 mm_file:// ID")
        if subject_image:
            refs = [
                {
                    "type": "character",
                    "image": [await self._resolve_image(subject_image)],
                }
            ]
        elif refs:
            refs = await self._image_refs(refs)
        self._check()
        return await p._video.generate(prompt, subject_reference=refs, **options)

    @_business
    async def get_video_task(self, task_id, model=None) -> dict:
        _text(task_id, "task_id")
        _optional_texts({"model": model}, ("model",))
        p = self._plugin
        return await p._video.get_task(
            task_id, model=model or p._default_video_model or None
        )

    @_business
    async def wait_video_task(
        self, task_id, *, poll_interval=None, timeout=None, model=None
    ) -> dict:
        _text(task_id, "task_id")
        _optional_texts({"model": model}, ("model",))
        p = self._plugin
        interval = p._video_poll_interval if poll_interval is None else poll_interval
        deadline = p._video_timeout if timeout is None else timeout
        _timeout(deadline)
        if (
            isinstance(interval, bool)
            or not isinstance(interval, (int, float))
            or not math.isfinite(interval)
            or interval <= 0
        ):
            raise ValueError("poll_interval 必须为有限正数")
        return await p._video.wait_for_completion(
            task_id,
            poll_interval=interval,
            timeout=deadline,
            model=model or p._default_video_model or None,
        )

    @_business
    async def download_video(self, file_id, *, out=None, video_url=None) -> dict:
        p = self._plugin
        if not video_url:
            _text(file_id, "file_id")
        elif not isinstance(video_url, str) or not is_url(video_url):
            raise ValueError("video_url 必须为 HTTP(S) URL")
        if out is None:
            path = p._cache_dir / f"mmx_video_{uuid.uuid4().hex}.mp4"
        else:
            _text(out, "out")
            if Path(out).is_absolute() or ":" in out or ".." in Path(out).parts:
                raise ValueError("out 必须为插件数据目录内相对路径")
            path = resolve_data_path(str(p._plugin_data_dir), out)
            if path is None or path.is_dir():
                raise ValueError("out 必须为插件数据目录内文件")
        path.parent.mkdir(parents=True, exist_ok=True)
        saved = await p._video.download(file_id, str(path), video_url=video_url)
        return {"ok": True, "file_path": saved}

    @_business
    async def synthesize_speech(self, text, **options) -> dict:
        _text(text, "text", max_length=10000)
        p = self._plugin
        options["model"] = options.get("model") or p._default_speech_model or None
        options["voice"] = (
            options.get("voice")
            or p._default_speech_voice
            or "English_expressive_narrator"
        )
        _options(p._speech.synthesize, options, text=text)
        _optional_texts(options, ("model", "voice", "emotion", "language"))
        if options.get("audio_format", "mp3") not in (
            "mp3",
            "pcm",
            "flac",
            "wav",
            "pcmu_raw",
            "pcmu_wav",
            "opus",
        ):
            raise ValueError("audio_format 无效")
        for name in ("text_normalization", "latex_read", "subtitles"):
            if name in options and type(options[name]) is not bool:
                raise ValueError(f"{name} 必须为布尔值")
        for name in ("sample_rate", "bitrate", "channels"):
            if name in options and (
                type(options[name]) is not int or options[name] <= 0
            ):
                raise ValueError(f"{name} 必须为正整数")
        for name in ("speed", "volume", "pitch"):
            value = options.get(name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError(f"{name} 必须为有限数值")
        return await p._speech.synthesize(text, **options)

    @_business
    async def list_voices(self) -> dict:
        return await self._plugin._speech.list_voices()

    @_business
    async def transcribe(
        self,
        file,
        *,
        model=None,
        response_format="json",
        language=None,
        timestamp_level=None,
        stream=False,
        out=None,
    ) -> dict:
        p = self._plugin
        options = TranscriptionOptions(
            model=model or p._transcription.default_model,
            response_format=response_format,
            language=language,
            timestamp_level=timestamp_level,
            stream=stream,
        )
        return await p._transcription.run(file, options, out=out)

    @_business
    async def search(self, query) -> dict:
        _text(query, "query")
        return await self._plugin._search.query(query)

    @_business
    async def describe_image(
        self, image=None, prompt="Describe the image.", file_id=None
    ) -> dict:
        p = self._plugin
        _text(prompt, "prompt")
        if image and file_id:
            raise ValueError("image 与 file_id 不能同时使用")
        if image is not None:
            _text(image, "image")
        if file_id is not None:
            _text(file_id, "file_id")
        return await p._vision.describe(
            image,
            prompt,
            file_id=file_id,
            data_dir=str(p._plugin_data_dir),
            extra_allowed_dirs=p._extra_dirs,
        )

    @_business
    async def get_quota(self) -> dict:
        p = self._plugin
        # Same configured keys as the Tool/command; never reveal keys in the result.
        keys = (
            [s.key for s in p._key_pool._states] if p._key_pool else [p._client.api_key]
        )
        requests = [asyncio.create_task(p._quota.info(key)) for key in keys]
        try:
            results = await asyncio.gather(*requests)
            return {
                "accounts": [{"index": i + 1, "data": r} for i, r in enumerate(results)]
            }
        finally:
            for request in requests:
                if not request.done():
                    request.cancel()
            await asyncio.gather(*requests, return_exceptions=True)

    @_business
    async def upload_file(self, file_path, purpose="retrieval") -> dict:
        p = self._plugin
        _text(file_path, "file_path")
        _text(purpose, "purpose")
        path = resolve_local_input_path(
            file_path,
            data_dir=str(p._plugin_data_dir),
            extra_allowed_dirs=p._extra_dirs,
        )
        return await p._files.upload(str(path), purpose=purpose)

    @_business
    async def list_files(self) -> dict:
        return await self._plugin._files.list()

    @_business
    async def delete_file(self, file_id) -> dict:
        if isinstance(file_id, bool) or not isinstance(file_id, (str, int)):
            raise ValueError("file_id 必须为数字")
        return await self._plugin._files.delete(file_id)
