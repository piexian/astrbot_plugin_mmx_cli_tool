"""MiniMax API 错误分类与映射。"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

import httpx


class ErrorCategory(Enum):
    """错误类别枚举。"""

    AUTH = "auth"
    QUOTA = "quota"
    TIMEOUT = "timeout"
    CONTENT_FILTER = "content_filter"
    USAGE = "usage"
    GENERAL = "general"
    NETWORK = "network"


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


def _error_fields(body: Any) -> tuple[int | None, str, str]:
    if not isinstance(body, dict):
        return None, "", ""
    base = body.get("base_resp")
    error = body.get("error")
    base = base if isinstance(base, dict) else {}
    error = error if isinstance(error, dict) else {}
    code = base.get("status_code") or error.get("code")
    return (
        code if isinstance(code, int) and not isinstance(code, bool) else None,
        str(base.get("status_msg") or error.get("message") or ""),
        str(error.get("type") or ""),
    )


def classify_error(
    http_status: int,
    response: httpx.Response | None = None,
    path: str = "",
    api_body: dict[str, Any] | None = None,
) -> MiniMaxError:
    """将 HTTP 状态和两种 API 错误格式映射为结构化异常。"""
    body: Any = api_body
    if body is None and response is not None:
        try:
            body = response.json()
        except ValueError:
            pass
    code, message, error_type = _error_fields(body)
    category = ErrorCategory.GENERAL
    retryable = False

    if http_status in (401, 403):
        category = ErrorCategory.AUTH
        text = f"API Key 无效或已过期 ({http_status})。请检查配置中的 api_key。"
    elif http_status == 429:
        category, retryable = ErrorCategory.QUOTA, True
        text = f"请求过于频繁或额度不足 ({http_status})。{message}"
    elif http_status == 402 or error_type == "insufficient_balance_error":
        category = ErrorCategory.QUOTA
        text = f"账户余额不足。{message}"
    elif http_status in (408, 504):
        category, retryable = ErrorCategory.TIMEOUT, True
        text = f"请求超时 ({http_status})。请稍后重试。"
    elif http_status == 413 and "/speech_to_text" in path:
        category = ErrorCategory.USAGE
        text = f"音频超过 50 MB 限制，请压缩或分段后提交。{message}"
    elif http_status == 422 and "/speech_to_text" in path:
        category = ErrorCategory.CONTENT_FILTER
        text = f"输入音频被安全审核拦截。{message}"
    elif code in (1002, 1039, 1026) or (
        http_status == 422 and error_type == "unprocessable_entity_error"
        and re.search(r"sensitive content|(?:^|\D)1026(?:\D|$)", message, re.I)
    ):
        category = ErrorCategory.CONTENT_FILTER
        text = f"输入内容被安全审核拦截，请修改输入。{message}"
    elif code in (1028, 1030, 2061):
        category = ErrorCategory.QUOTA
        text = f"额度不足或模型不可用 (code={code})。{message}"
    elif code == 1027 or re.search(r"output.*sensitive", message, re.I):
        category = ErrorCategory.CONTENT_FILTER
        text = f"输出内容被安全审核拦截，请调整查询。{message}"
    elif code:
        text = f"API 错误 (code={code}): {message}"
    elif http_status >= 500:
        retryable = True
        text = f"MiniMax 服务器错误 ({http_status})。请稍后重试。{message}"
    else:
        text = f"请求失败 ({http_status})" + (f": {message}" if message else "")
    return MiniMaxError(category, text.strip(), http_status, code, retryable)


def friendly_message(err: MiniMaxError) -> str:
    """保留审核和参数错误的具体原因，其余类别给出简短提示。"""
    messages = {
        ErrorCategory.AUTH: "API Key 无效，请检查插件配置。",
        ErrorCategory.QUOTA: "额度不足或请求过多，请稍后重试。",
        ErrorCategory.TIMEOUT: "请求超时，请稍后重试。",
        ErrorCategory.NETWORK: "网络连接失败，请检查网络。",
    }
    return messages.get(err.category, str(err))
