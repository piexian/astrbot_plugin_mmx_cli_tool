"""Matrix MCP 网关客户端 — agent.minimax.cn / agent.minimax.io 的 /mavis/api/v1/mcp/* 工具。

对齐 @mavis/agent-tools 桌面端：Authorization: Bearer <OAuth access token>，
User-Agent: MiniMaxAgent；生成类超时 10 分钟，其他 2 分钟。
响应兼容 base_resp.status_code 与 {code, message, ...} 两种错误包装。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .client import MiniMaxClient
from .endpoints import matrix_mcp_endpoint
from .errors import ErrorCategory, MiniMaxError

# 工具名 → 超时秒数（生成类 10 min / 其他 2 min，对齐 MATRIX_TOOL_TIMEOUTS）
_TOOL_TIMEOUTS: dict[str, float] = {
    "images_search_and_download": 600,
    "image_reverse_search": 600,
    "batch_text_to_music": 600,
    "batch_text_to_audio": 600,
    "get_upload_url": 120,
}
_DEFAULT_TIMEOUT = 120.0
# get_upload_url 大文件直传场景放宽（对齐上游 1_500_000 ms）
_UPLOAD_URL_TIMEOUT = 1500.0

#: 未登录 OAuth 且无可回退凭据时的指引
LOGIN_HINT = "该功能需要 MiniMax 账号授权，请管理员发送 /mmx login 完成绑定"


class MatrixAPI:
    """MiniMax Matrix MCP 工具调用（搜图、以图搜图、音乐音效生成、媒体上传）。"""

    def __init__(
        self,
        client: MiniMaxClient,
        creds_getter: Callable[[], Awaitable[tuple[str, str]]],
        token_refresher: Callable[[], Awaitable[tuple[str, str]]] | None = None,
    ) -> None:
        """creds_getter/token_refresher 均返回 (region, token)，供调用与 401 重试使用。"""
        self._client = client
        self._creds_getter = creds_getter
        self._token_refresher = token_refresher

    async def _request(
        self, url: str, tool: str, body: dict[str, Any], token: str, timeout: float | None
    ) -> dict[str, Any]:
        res = await self._client.request(
            "POST",
            url,
            body=body,
            headers={"User-Agent": "MiniMaxAgent"},
            api_key_override=token,
            timeout=timeout or _TOOL_TIMEOUTS.get(tool, _DEFAULT_TIMEOUT),
        )
        return MiniMaxClient.decode_json_response(res, url)

    async def _call(self, tool: str, body: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        """调用 Matrix 工具并展开业务错误，返回剥掉 base_resp/code 的负载。

        401/403 时若有 token_refresher 则强制刷新后重试一次，仍失败提示重新登录。
        """
        region, token = await self._creds_getter()
        if not token:
            raise MiniMaxError(ErrorCategory.AUTH, LOGIN_HINT)
        url = matrix_mcp_endpoint(region, tool)
        try:
            data = await self._request(url, tool, body, token, timeout)
        except MiniMaxError as exc:
            if exc.category is not ErrorCategory.AUTH or not self._token_refresher:
                raise
            region, token = await self._token_refresher()
            if not token:
                raise MiniMaxError(ErrorCategory.AUTH, LOGIN_HINT) from exc
            url = matrix_mcp_endpoint(region, tool)
            try:
                data = await self._request(url, tool, body, token, timeout)
            except MiniMaxError as retry_exc:
                if retry_exc.category is ErrorCategory.AUTH:
                    raise MiniMaxError(
                        ErrorCategory.AUTH, f"登录凭据已失效。{LOGIN_HINT}"
                    ) from retry_exc
                raise
        # 兼容 {code, message, ...payload} 包装
        code = data.pop("code", None)
        message = data.pop("message", None)
        if isinstance(code, int) and code != 0:
            if code == 402:
                raise MiniMaxError(
                    ErrorCategory.QUOTA,
                    "账号额度不足 (code 402)，请先充值后再试，不要重复提交相同请求。",
                )
            raise MiniMaxError(
                ErrorCategory.GENERAL, f"Matrix 工具调用被拒绝 (code {code}): {message or '未知错误'}"
            )
        return data
    async def images_search_and_download(
        self, query: str, prompt: str | None = None, providers: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """按关键词检索网络图片，返回 results[0].images 列表（含 image_url）。"""
        item: dict[str, Any] = {"query": query}
        if prompt:
            item["prompt"] = prompt
        body: dict[str, Any] = {"queries": [item]}
        if providers:
            body["providers"] = providers
        data = await self._call("images_search_and_download", body)
        results = data.get("results")
        return results if isinstance(results, list) else []

    async def image_reverse_search(self, image_url: str) -> list[dict[str, Any]]:
        """以图搜图，返回出处列表（title/link/source/image_url/snippet）。"""
        data = await self._call("image_reverse_search", {"image_url": image_url})
        items = data.get("data")
        return items if isinstance(items, list) else []

    async def batch_text_to_music(
        self,
        prompt: str,
        lyrics: str | None = None,
        audio_format: str = "mp3",
    ) -> dict[str, Any]:
        """文本生成音乐，返回 {output_url}；失败抛出 MiniMaxError。"""
        fmt = audio_format.lower()
        if fmt not in {"mp3", "wav"}:  # pcm 裸流平台无法播放，不下放
            fmt = "mp3"
        item: dict[str, Any] = {"prompt": prompt, "output_file": f"slot_0.{fmt}"}
        if lyrics:
            item["lyrics"] = lyrics
        if audio_format.lower() in {"mp3", "wav"}:
            item["format"] = fmt
        return await self._batch_audio("batch_text_to_music", [item])

    async def batch_text_to_audio(self, prompt: str) -> dict[str, Any]:
        """文本生成音效，返回 {output_url}；失败抛出 MiniMaxError。"""
        item: dict[str, Any] = {"prompt": prompt, "output_file": "slot_0.mp3"}
        return await self._batch_audio("batch_text_to_audio", [item])

    async def _batch_audio(self, tool: str, requests: list[dict[str, Any]]) -> dict[str, Any]:
        data = await self._call(tool, {"requests": requests})
        success = data.get("success_items")
        success_items = success if isinstance(success, list) else []
        for entry in success_items:
            if isinstance(entry, dict) and entry.get("output_url"):
                return entry
        failed = data.get("failed_items")
        failed_items = failed if isinstance(failed, list) else []
        reason = next(
            (
                str(entry.get("error_msg"))
                for entry in failed_items
                if isinstance(entry, dict) and entry.get("error_msg")
            ),
            "服务端未返回生成结果",
        )
        raise MiniMaxError(ErrorCategory.GENERAL, f"生成失败: {reason}")

    async def get_upload_url(
        self, filename: str, mime_type: str, size_bytes: int
    ) -> dict[str, Any]:
        """获取 OSS 预签名上传地址，返回 {put_url, cdn_url, content_type?, oss_key?}。"""
        data = await self._call(
            "get_upload_url",
            {"filename": filename, "mime_type": mime_type, "size_bytes": size_bytes},
            timeout=_UPLOAD_URL_TIMEOUT,
        )
        if not data.get("put_url") or not data.get("cdn_url"):
            raise MiniMaxError(ErrorCategory.GENERAL, "get_upload_url 未返回 put_url")
        return data
