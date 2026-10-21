"""MiniMax 搜图并下载 FunctionTool（Matrix MCP images_search_and_download）。"""

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
from ..mmx.media_transfer import MatrixMediaClient, MediaTransferError
from .result import tool_result
from .schema import integer_param, object_parameters, string_param


def _infer_ext(url: str) -> str:
    """从 URL 推断图片扩展名（对齐上游 inferExt，非法时回退 .jpg）。"""
    try:
        from urllib.parse import urlparse

        ext = Path(urlparse(url).path).suffix.lower()
        return ext if ext and len(ext) <= 5 else ".jpg"
    except Exception:
        return ".jpg"


@dataclass
class SearchImagesTool(FunctionTool):
    """LLM 工具：检索真实网络图片并下载到本地。"""

    def __init__(self, api: MatrixAPI, media: MatrixMediaClient, cache_dir: str):
        super().__init__(
            name="mmx_search_images",
            description=(
                "Search the web for real images and download them to the local AstrBot temp directory. "
                "Use this when the user asks for real-world pictures (photos of places, people, products, etc.). "
                "The result includes file_paths (local paths) — always send images via file_paths, "
                "do not pass remote image URLs to sending tools."
            ),
            parameters=object_parameters(
                {
                    "query": string_param("Image search keywords, e.g. '可爱的柴犬 照片'"),
                    "count": integer_param("Number of images to download (1-5, default 3)"),
                },
                required=["query"],
            ),
        )
        self._api = api
        self._media = media
        self._cache_dir = cache_dir

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        query = str(kwargs.get("query") or "").strip()
        if not query:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 query 参数",
                        "hint": "请提供 query 作为搜图关键词",
                        "example": {"query": "富士山 日出"},
                    },
                    ensure_ascii=False,
                )
            )
        try:
            count = int(kwargs.get("count") or 3)
        except (TypeError, ValueError):
            count = 3
        count = max(1, min(5, count))

        try:
            results = await self._api.images_search_and_download(query)
        except Exception as e:
            logger.error(f"[mmx] 搜图失败: {e}")
            return tool_result(
                json.dumps({"ok": False, "error": f"搜图失败: {e}"}, ensure_ascii=False)
            )

        images: list[str] = []
        if results and isinstance(results[0], dict):
            for item in results[0].get("images") or []:
                if isinstance(item, dict) and item.get("image_url"):
                    images.append(str(item["image_url"]))
                if len(images) >= count:
                    break
        if not images:
            error = results[0].get("error") if results and isinstance(results[0], dict) else None
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"未找到相关图片{f'（{error}）' if error else ''}"},
                    ensure_ascii=False,
                )
            )

        saved: list[str] = []
        failed = 0
        stamp = int(_time.time() * 1000)
        for index, url in enumerate(images):
            dest = Path(self._cache_dir) / f"mmx_search_image_{stamp}_{index}{_infer_ext(url)}"
            try:
                path = await self._media.download_to_file(
                    url, dest, allowed_dir=self._cache_dir
                )
                saved.append(str(path))
            except MediaTransferError as e:
                failed += 1
                logger.warning(f"[mmx] 搜图下载失败 {url}: {e}")
            except Exception as e:
                failed += 1
                logger.warning(f"[mmx] 搜图下载异常 {url}: {e}")

        resp: dict = {"ok": True, "query": query, "downloaded": len(saved), "failed": failed}
        if saved:
            resp["file_paths"] = saved
            resp["hint"] = (
                "图片已保存到本地。发送图片时必须使用 file_paths 中的本地路径，"
                "不要使用远程链接（接收端可能无法下载）。"
            )
        else:
            resp["image_urls"] = images
        return tool_result(json.dumps(resp, ensure_ascii=False))
