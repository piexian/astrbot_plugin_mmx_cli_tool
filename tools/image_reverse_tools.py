"""MiniMax 以图搜图 FunctionTool（Matrix MCP image_reverse_search）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.matrix import MatrixAPI
from ..mmx.media_transfer import IMAGE_EXTENSIONS, MatrixMediaClient, MediaTransferError
from ..mmx.utils import is_url, resolve_existing_data_path
from .result import tool_result
from .schema import object_parameters, string_param


_IMAGE_EXTS = IMAGE_EXTENSIONS

def format_reverse_results(items: list[dict], limit: int = 3) -> str:
    """格式化以图搜图结果为 Markdown 卡片（Top N）。"""
    lines = ["🔍 以图搜图结果:"]
    for i, item in enumerate(items[:limit], 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "(无标题)").strip()
        lines.append(f"\n{i}. **{title}**")
        source = str(item.get("source") or "").strip()
        if source:
            lines.append(f"   来源: {source}")
        link = str(item.get("link") or "").strip()
        if link:
            lines.append(f"   链接: {link}")
        image_url = str(item.get("image_url") or "").strip()
        if image_url:
            lines.append(f"   缩略图: {image_url}")
        snippet = str(item.get("snippet") or "").strip()
        if snippet:
            lines.append(f"   摘要: {snippet[:200]}")
    return "\n".join(lines)


@dataclass
class ImageReverseSearchTool(FunctionTool):
    """LLM 工具：以图搜图，查找图片来源与相似图片。"""

    def __init__(
        self,
        api: MatrixAPI,
        media: MatrixMediaClient,
        data_dir: str,
        extra_allowed_dirs: list[str] | None = None,
    ):
        super().__init__(
            name="mmx_image_reverse_search",
            description=(
                "Reverse image search: find the source and similar pages of an image. "
                "Accepts an image URL or a local path inside the plugin data/AstrBot temp directory. "
                "Returns matching websites with titles, sources, links and thumbnails."
            ),
            parameters=object_parameters(
                {
                    "image": string_param(
                        "Image URL or local file path (plugin data dir / AstrBot temp dir)"
                    ),
                },
                required=["image"],
            ),
        )
        self._api = api
        self._media = media
        self._data_dir = data_dir
        self._extra_allowed_dirs = extra_allowed_dirs

    async def resolve_image_url(self, image: str, *, trusted_local: bool = False) -> str:
        """URL 直传；本地文件先经 OSS 上传换取 cdn_url。

        本地文件仅限常见图片扩展名（防止 auth.json 等敏感文件被注入上传）；
        trusted_local=True 用于聊天提取的已落盘图片，跳过沙箱目录校验。
        """
        if is_url(image):
            return image
        if trusted_local:
            local = Path(image)
        else:
            local = resolve_existing_data_path(self._data_dir, image, self._extra_allowed_dirs)
        if local is None or not local.is_file():
            raise MediaTransferError(
                "file_not_found",
                f"图片不存在或不在允许目录内: {image}（允许：{self._data_dir}）",
            )
        if local.suffix.lower() not in _IMAGE_EXTS:
            raise MediaTransferError(
                "invalid_type",
                f"仅支持图片文件（{'/'.join(sorted(_IMAGE_EXTS))}），拒绝上传: {local.name}",
            )
        uploaded = await self._media.upload_file(local, force_remote_url=True)
        if not uploaded.cdn_url:
            raise MediaTransferError("upload_failed", "图片上传未返回可访问 URL")
        return uploaded.cdn_url
    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        image = str(kwargs.get("image") or "").strip()
        if not image:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 image 参数",
                        "hint": "请提供图片 URL 或本地路径（插件数据目录 / AstrBot 临时目录内）",
                    },
                    ensure_ascii=False,
                )
            )
        try:
            image_url = await self.resolve_image_url(image)
            items = await self._api.image_reverse_search(image_url)
        except Exception as e:
            logger.error(f"[mmx] 以图搜图失败: {e}")
            return tool_result(
                json.dumps({"ok": False, "error": f"以图搜图失败: {e}"}, ensure_ascii=False)
            )
        if not items:
            return tool_result(
                json.dumps({"ok": True, "count": 0, "text": "未找到相似图片或出处。"}, ensure_ascii=False)
            )
        return tool_result(
            json.dumps(
                {
                    "ok": True,
                    "count": len(items),
                    "results": items[:10],
                    "text": format_reverse_results(items),
                },
                ensure_ascii=False,
            )
        )
