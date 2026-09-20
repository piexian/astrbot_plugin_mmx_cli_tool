"""语音转文字工具与指令共用入口。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from astrbot.api import FunctionTool, logger
from astrbot.core.message import components as Comp

from ..mmx.apis.transcription import TranscriptionOptions
from ..mmx.attachment_input import extract_first_audio_input
from ..mmx.errors import MiniMaxError
from ..mmx.transcription import TranscriptionService
from .result import tool_result
from .schema import boolean_param, object_parameters, string_param


async def transcribe_event(
    service: TranscriptionService,
    event,
    options: TranscriptionOptions,
    *,
    file: str | None = None,
    out: str | None = None,
) -> dict:
    service.validate(options, out)
    trusted = False
    if file is None and event is not None:
        file, _ = await extract_first_audio_input(
            event.get_messages(), record_type=Comp.Record, file_type=Comp.File,
            reply_type=Comp.Reply, event=event, prefer_url=True,
        )
        trusted = file is not None
    return await service.run(file, options, trusted_attachment=trusted, out=out)


@dataclass
class SpeechTranscribeTool(FunctionTool):
    """LLM 工具：转写允许目录或聊天附件中的音频。"""

    def __init__(self, service: TranscriptionService):
        super().__init__(
            name="mmx_speech_transcribe",
            description=(
                "Transcribe audio to text, timestamps/speakers, or SRT/WebVTT subtitles. "
                "Max 50 MB and 500 seconds. Omit file to use a current/quoted audio attachment. "
                "Streaming is collected into a complete result, not individual chat messages."
            ),
            parameters=object_parameters({
                "file": string_param("Audio path inside plugin data or allowed AstrBot temp directory; URLs are not accepted. Omit for attachments."),
                "model": string_param("ASR model; defaults to default_transcription_model (asr-1.0)."),
                "language": string_param("BCP-47 language hint, e.g. zh or en; omit for automatic detection."),
                "responseFormat": string_param("json, verbose_json (timestamps and speakers), srt, or vtt."),
                "timestampLevel": string_param("sentence or word; ignored by the API for json."),
                "stream": boolean_param("Receive SSE and return the complete transcript; json only, incompatible with out."),
                "out": string_param("Optional relative output file inside plugin data. Other output files use the temporary cache."),
            }),
        )
        self._service = service

    async def call(self, context, **kwargs):
        options = TranscriptionOptions(
            model=kwargs.get("model") or self._service.default_model,
            response_format=kwargs.get("responseFormat", "json"),
            language=kwargs.get("language"), timestamp_level=kwargs.get("timestampLevel"),
            stream=kwargs.get("stream", False),
        )
        event = getattr(getattr(context, "context", None), "event", None)
        try:
            result = await transcribe_event(
                self._service, event, options, file=kwargs.get("file"), out=kwargs.get("out")
            )
        except MiniMaxError as exc:
            result = {"ok": False, "error": str(exc), "category": exc.category.value, "retryable": exc.retryable}
        except (ValueError, OSError) as exc:
            result = {
                "ok": False, "error": str(exc), "category": "usage",
                "hint": "检查音频路径、50 MB 大小限制及参数组合；也可附带或引用音频后省略 file。",
                "example": {"file": "meeting.mp3", "language": "zh", "responseFormat": "json"},
            }
        except Exception as exc:
            logger.warning(f"[mmx] 转写失败: {exc}")
            result = {"ok": False, "error": "转写未完成或状态未确认，请勿自动重复提交计费请求。", "retryable": False}
        return tool_result(json.dumps(result, ensure_ascii=False))
