"""MiniMax API 错误分类与映射。"""

from __future__ import annotations

from enum import Enum
from typing import Any

import httpx


class ErrorCategory(Enum):
    """错误类别枚举。"""
    AUTH = "auth"                       # 鉴权失败
    QUOTA = "quota"                     # 额度不足/限流
    TIMEOUT = "timeout"                 # 超时
    CONTENT_FILTER = "content_filter"   # 内容审核拦截
    USAGE = "usage"                     # 参数错误
    GENERAL = "general"                 # 一般错误
    NETWORK = "network"                 # 网络错误


class MiniMaxError(Exception):
    """MiniMax API 结构化异常。"""

    def __init__(
        self,
        category: ErrorCategory,
        message: str,
        http_status: int | None = None,
        api_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.category = category
        self.http_status = http_status
        self.api_code = api_code
        self.retryable = retryable
        super().__init__(message)


def _extract_api_code(body: dict[str, Any] | None) -> int | None:
    """从响应体中提取 base_resp.status_code。"""
    if not body:
        return None
    base_resp = body.get("base_resp")
    if isinstance(base_resp, dict):
        code = base_resp.get("status_code")
        if isinstance(code, int):
            return code
    return None


def classify_error(
    http_status: int,
    response: httpx.Response | None = None,
    path: str = "",
    api_body: dict[str, Any] | None = None,
) -> MiniMaxError:
    """将 HTTP 状态码 + API 响应体映射为 MiniMaxError。"""
    body: dict[str, Any] | None = None
    if response is not None:
        try:
            body = response.json()
        except Exception:
            body = None
    if api_body is not None:
        body = api_body

    api_code = _extract_api_code(body)

    status_msg = ""
    if body:
        base_resp = body.get("base_resp")
        if isinstance(base_resp, dict):
            status_msg = base_resp.get("status_msg", "")

    # HTTP 层错误
    if http_status in (401, 403):
        return MiniMaxError(
            ErrorCategory.AUTH,
            f"API Key 无效或已过期 ({http_status})。请检查配置中的 api_key。",
            http_status=http_status,
            retryable=False,
        )
    if http_status == 429:
        return MiniMaxError(
            ErrorCategory.QUOTA,
            f"请求过于频繁或额度不足 ({http_status})。{status_msg}".strip(),
            http_status=http_status,
            retryable=True,
        )
    if http_status in (408, 504):
        return MiniMaxError(
            ErrorCategory.TIMEOUT,
            f"请求超时 ({http_status})。请稍后重试。",
            http_status=http_status,
            retryable=True,
        )

    # API 层错误（通过 base_resp.status_code）
    if api_code is not None and api_code != 0:
        # 内容审核拦截
        if api_code in (1002, 1039):
            return MiniMaxError(
                ErrorCategory.CONTENT_FILTER,
                f"内容被审核拦截 (code={api_code})。{status_msg}".strip(),
                api_code=api_code,
                retryable=False,
            )
        # 额度/计划限制
        if api_code in (1028, 1030, 2061):
            return MiniMaxError(
                ErrorCategory.QUOTA,
                f"额度不足或模型不可用 (code={api_code})。{status_msg}".strip(),
                api_code=api_code,
                retryable=False,
            )

        return MiniMaxError(
            ErrorCategory.GENERAL,
            f"API 错误 (code={api_code}): {status_msg}".strip(),
            http_status=http_status,
            api_code=api_code,
        )

    # 通用 HTTP 服务端错误
    if http_status >= 500:
        return MiniMaxError(
            ErrorCategory.GENERAL,
            f"MiniMax 服务器错误 ({http_status})。请稍后重试。",
            http_status=http_status,
            retryable=True,
        )

    msg = f"请求失败 ({http_status})"
    if status_msg:
        msg += f": {status_msg}"
    return MiniMaxError(
        ErrorCategory.GENERAL,
        msg,
        http_status=http_status,
    )


def friendly_message(err: MiniMaxError) -> str:
    """根据错误类别返回用户友好的中文提示。"""
    messages = {
        ErrorCategory.AUTH: "❌ API Key 无效，请检查插件配置。",
        ErrorCategory.QUOTA: "⏳ 额度不足或请求过多，请稍后重试。",
        ErrorCategory.TIMEOUT: "⏱️ 请求超时，请稍后重试。",
        ErrorCategory.CONTENT_FILTER: "🚫 内容被安全审核拦截，请修改提示词后重试。",
        ErrorCategory.NETWORK: "🌐 网络连接失败，请检查网络。",
    }
    return messages.get(err.category, f"❌ {err}")
