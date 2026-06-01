"""MiniMax 视频生成 API。"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from ..client import MiniMaxClient
from ..endpoints import video_gen_endpoint, video_task_endpoint, file_retrieve_endpoint


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
    ) -> dict[str, Any]:
        """提交视频生成任务。模型由调用层按配置或显式参数传入。"""
        # 自动选择模型
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

    async def get_task(self, task_id: str) -> dict[str, Any]:
        """查询视频任务状态。"""
        return await self._client.request_json(
            "GET",
            video_task_endpoint(self._client.base_url, task_id),
        )

    async def wait_for_completion(
        self,
        task_id: str,
        poll_interval: int = 5,
        timeout: int = 600,
    ) -> dict[str, Any]:
        """轮询等待视频生成完成。"""
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            result = await self.get_task(task_id)
            status = result.get("status", "Unknown")
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
