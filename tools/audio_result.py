"""Audio tool result helpers."""

from __future__ import annotations

import json
import asyncio
import time as _time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from astrbot.api import logger

from .background_tasks import BACKGROUND_TASKS


class AudioSaver(Protocol):
    def save(self, response: dict, out_path: str) -> str: ...


def saved_audio_result(
    api: AudioSaver,
    response: dict,
    *,
    save_dir: str,
    prefix: str,
    success_message: str,
    save_error_label: str,
    audio_format: str = "mp3",
) -> str:
    """Save API audio data and return a compact JSON tool result."""
    data = response.get("data", {})
    audio_url = data.get("audio_url", "")
    audio_hex = data.get("audio", "")
    if not audio_hex and not audio_url:
        return json.dumps(
            {"ok": False, "error": "生成完成，但未返回可用音频。"},
            ensure_ascii=False,
        )

    out_path = Path(save_dir) / f"{prefix}_{int(_time.time() * 1000)}.{audio_format}"
    try:
        saved = api.save(response, str(out_path))
    except Exception as e:
        logger.warning(f"[mmx] {save_error_label}: {e}")
        if audio_url:
            return json.dumps(
                {
                    "ok": True,
                    "audio_url": audio_url,
                    "message": f"{success_message}，但保存到本地失败，已返回远程音频链接。",
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"ok": False, "error": f"{save_error_label}: {e}"},
            ensure_ascii=False,
        )

    return json.dumps(
        {"ok": True, "file_path": saved, "message": success_message},
        ensure_ascii=False,
    )


def schedule_audio_result_to_agent(
    context: object,
    *,
    tasks: set[asyncio.Task],
    label: str,
    tool_name: str,
    tool_args: dict,
    max_wait_seconds: int,
    poll_after_seconds: int,
    work: Callable[[], Awaitable[str]],
) -> str | None:
    """Run audio generation in the background and wake the agent with its result."""
    agent_context = getattr(context, "context", None)
    if (
        agent_context is None
        or getattr(agent_context, "event", None) is None
        or getattr(agent_context, "context", None) is None
    ):
        return None

    record = BACKGROUND_TASKS.create(
        tool_name=tool_name,
        label=label,
        max_wait_seconds=max_wait_seconds,
        poll_after_seconds=poll_after_seconds,
    )
    task_id = record.task_id

    async def runner() -> None:
        try:
            result_text = await work()
        except Exception as e:
            logger.error(f"[mmx] {label}后台任务失败: {e}", exc_info=True)
            result_text = json.dumps(
                {"ok": False, "error": f"{label}失败: {e}"},
                ensure_ascii=False,
            )
        BACKGROUND_TASKS.complete(task_id, result_text)

        try:
            from astrbot.core.astr_agent_tool_exec import FunctionToolExecutor

            await FunctionToolExecutor._wake_main_agent_for_background_result(
                run_context=context,
                task_id=task_id,
                tool_name=tool_name,
                result_text=result_text,
                tool_args=tool_args,
                note=f"{label}已完成。",
                summary_name=tool_name,
            )
        except Exception as e:
            logger.error(f"[mmx] {label}完成后唤醒 Agent 失败: {e}", exc_info=True)

    task = asyncio.create_task(runner())
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return task_id
