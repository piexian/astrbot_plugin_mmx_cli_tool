"""MiniMax 视频生成 API。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import (
    file_retrieve_endpoint,
    video_gen_endpoint,
    video_gen_v2_endpoint,
    video_task_endpoint,
    video_task_v2_endpoint,
)

VIDEO_V2_MODEL = "MiniMax-H3"
VIDEO_V2_RATIOS = frozenset(
    {"adaptive", "21:9", "16:9", "4:3", "1:1", "3:4", "9:16"}
)
VIDEO_V2_IMAGE_ROLES = frozenset({"first_frame", "last_frame", "reference_image"})


class VideoAPI:
    """MiniMax 视频生成接口，支持异步提交和轮询。"""

    def __init__(self, client: MiniMaxClient) -> None:
        self._client = client

    async def generate(
        self,
        prompt: str,
        *,
        model: str | None = None,
        first_frame_image: str | None = None,
        last_frame_image: str | None = None,
        subject_reference: list[dict[str, Any]] | None = None,
        callback_url: str | None = None,
        # V2 (MiniMax-H3) 参数
        reference_images: list[str] | None = None,
        reference_videos: list[str] | None = None,
        reference_audios: list[str] | None = None,
        duration: int | None = None,
        ratio: str | None = None,
    ) -> dict[str, Any]:
        """提交视频生成任务。

        model 为 MiniMax-H3 时走 V2 端点（content 数组结构），否则走 V1 legacy。
        V2 的图片/视频/音频输入由调用层预先解析为 URL 或 data URI。
        """
        if model == VIDEO_V2_MODEL:
            return await self._generate_v2(
                prompt=prompt,
                first_frame_image=first_frame_image,
                last_frame_image=last_frame_image,
                reference_images=reference_images,
                reference_videos=reference_videos,
                reference_audios=reference_audios,
                duration=duration,
                ratio=ratio,
                callback_url=callback_url,
            )

        # V1 legacy
        body: dict[str, Any] = {
            "prompt": prompt,
        }
        if model:
            body["model"] = model
        if first_frame_image:
            body["first_frame_image"] = first_frame_image
        if last_frame_image:
            body["last_frame_image"] = last_frame_image
        if subject_reference:
            body["subject_reference"] = subject_reference
        if callback_url:
            body["callback_url"] = callback_url

        return await self._client.request_json(
            "POST",
            video_gen_endpoint(self._client.base_url),
            body=body,
        )

    async def _generate_v2(
        self,
        *,
        prompt: str,
        first_frame_image: str | None = None,
        last_frame_image: str | None = None,
        reference_images: list[str] | None = None,
        reference_videos: list[str] | None = None,
        reference_audios: list[str] | None = None,
        duration: int | None = None,
        ratio: str | None = None,
        callback_url: str | None = None,
    ) -> dict[str, Any]:
        """构建 V2 (MiniMax-H3) 请求体并提交。对齐 mmx-cli buildVideoV2Request。"""
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]

        # 首帧/尾帧图片
        if first_frame_image:
            content.append({
                "type": "image_url",
                "image_url": {"url": first_frame_image},
                "role": "first_frame",
            })
        if last_frame_image:
            content.append({
                "type": "image_url",
                "image_url": {"url": last_frame_image},
                "role": "last_frame",
            })
        # 参考图片
        for url in reference_images or []:
            content.append({
                "type": "image_url",
                "image_url": {"url": url},
                "role": "reference_image",
            })
        # 参考视频
        for url in reference_videos or []:
            content.append({
                "type": "video_url",
                "video_url": {"url": url},
                "role": "reference_video",
            })
        # 参考音频
        for url in reference_audios or []:
            content.append({
                "type": "audio_url",
                "audio_url": {"url": url},
                "role": "reference_audio",
            })

        has_frame_input = any(
            item.get("role") in ("first_frame", "last_frame")
            for item in content
            if item.get("type") == "image_url"
        )
        has_reference_input = any(
            str(item.get("role", "")).startswith("reference_")
            for item in content
            if item.get("type") != "text"
        )

        # 对齐 CLI：有帧或参考输入时默认 adaptive，纯文本需具体比例
        if ratio is None:
            ratio = "adaptive" if (has_frame_input or has_reference_input) else "16:9"
        is_text_only = not (has_frame_input or has_reference_input)
        if is_text_only and (not ratio or ratio == "adaptive"):
            raise ValueError(
                "MiniMax-H3 纯文本生成需指定具体比例（如 16:9, 9:16），不能使用 adaptive"
            )
        body: dict[str, Any] = {
            "model": VIDEO_V2_MODEL,
            "content": content,
            "resolution": "2K",
            "duration": duration if duration is not None else 5,
            "ratio": ratio,
        }
        if callback_url:
            body["callback_url"] = callback_url

        return await self._client.request_json(
            "POST",
            video_gen_v2_endpoint(self._client.base_url),
            body=body,
        )
    async def get_task(
        self, task_id: str, *, model: str | None = None
    ) -> dict[str, Any]:
        """查询视频任务状态。model 为 MiniMax-H3 时走 V2 端点。"""
        if model == VIDEO_V2_MODEL:
            result = await self._client.request_json(
                "GET",
                video_task_v2_endpoint(self._client.base_url, task_id),
            )
            # V2 返回 {task: {...}}，归一化为与 V1 一致的扁平结构
            return result.get("task", result)
        return await self._client.request_json(
            "GET",
            video_task_endpoint(self._client.base_url, task_id),
        )

    async def wait_for_completion(
        self,
        task_id: str,
        *,
        poll_interval: int = 5,
        timeout: int = 600,
        model: str | None = None,
    ) -> dict[str, Any]:
        """轮询等待视频生成完成。"""
        is_v2 = model == VIDEO_V2_MODEL
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            result = await self.get_task(task_id, model=model)
            status = result.get("status", "Unknown")
            if is_v2:
                if status == "succeeded":
                    return result
                if status in ("failed", "cancelled", "expired"):
                    raise RuntimeError(f"视频生成失败: task_id={task_id} ({status})")
            else:
                if status == "Success":
                    return result
                if status == "Failed":
                    raise RuntimeError(f"视频生成失败: task_id={task_id}")
            await asyncio.sleep(poll_interval)

        raise TimeoutError(f"视频生成超时 ({timeout}s): task_id={task_id}")

    async def download(self, file_id: str, out_path: str) -> str:
        """根据 file_id 下载视频到本地。"""
        res = await self._client.request(
            "GET",
            file_retrieve_endpoint(self._client.base_url, file_id),
        )
        data: dict[str, Any] = res.json()
        url = data.get("file", {}).get("download_url", "")
        if not url:
            raise RuntimeError(f"未找到下载链接: file_id={file_id}")

        async with httpx.AsyncClient() as cl:
            r = await cl.get(url)
            r.raise_for_status()
            from pathlib import Path

            await asyncio.to_thread(Path(out_path).write_bytes, r.content)
        return out_path
