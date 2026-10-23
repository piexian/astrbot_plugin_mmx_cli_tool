"""MiniMax Code 账户接口 — 身份、积分、套餐余量与签到。

签名与请求参数复刻自 new-api 的 minimaxcode 适配（逆向官方客户端 transport.js）：
yy = md5(encodeURIComponent(path+query) + '_' + (body 或 '{}') + md5(nowMs) + 'ooui')，
x-signature = md5(秒 + 盐 + body)。信封以 base_resp.status_code 与 statusInfo.code
双零判成功；user/info 有 data 包装，其余接口业务字段在顶层（缺失 data 时回退整体 body）。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from .client import MiniMaxClient
from .errors import ErrorCategory, MiniMaxError

#: 签名盐与 yy 后缀（new-api 实测值，来自官方客户端 transport.js）
_SIGNATURE_SALT = "I*7Cf%WZ#S&%1RlZJ&C2"
_YY_SUFFIX = "ooui"

#: 账户域：matrix（身份/签到/积分明细）与 open platform（余量/积分池）
_MATRIX_ORIGINS = {
    "cn": "https://agent.minimax.cn",
    "global": "https://agent.minimax.io",
}
_OPEN_ORIGINS = {
    "cn": "https://www.minimaxi.com",
    "global": "https://platform.minimax.io",
}

_TIMEOUT = 15.0

#: 未登录 OAuth 时的指引（与 matrix.LOGIN_HINT 一致）
LOGIN_HINT = "该功能需要 MiniMax 账号授权，请管理员发送 /mmx login 完成绑定"


def _md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _js_encode_uri_component(text: str) -> str:
    """对齐 new-api 实测实现：字母数字与 -_.!~*'()/ 之外百分号转义（保留 '/'）。"""
    return quote(text, safe="-_.!~*'()/")


def _timezone_offset_seconds() -> int:
    offset = time.localtime().tm_gmtoff
    return int(offset)


@dataclass
class AccountIdentity:
    """账号身份与个人分组（积分余额取个人 workspace）。"""

    real_user_id: str = ""
    user_id: str = ""
    user_name: str = ""
    email: str = ""
    group_id: str = ""
    credit_balance: str = ""


def _amount_string(value: Any) -> str:
    """积分金额保留原始小数位；上游可能返回数字或字符串。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return format(value, "g") if isinstance(value, float) else str(value)
    return ""


def _any_to_string(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(int(value))
    return ""


class MiniMaxCodeAccount:
    """MiniMax Code 账户操作（签到、积分、余量）。

    creds_getter/token_refresher 返回 (region, token)，语义与 MatrixAPI 一致：
    401/403 时强制刷新后重试一次。
    """

    def __init__(
        self,
        client: MiniMaxClient,
        creds_getter: Callable[[], Awaitable[tuple[str, str]]],
        token_refresher: Callable[[], Awaitable[tuple[str, str]]] | None = None,
    ) -> None:
        self._client = client
        self._creds_getter = creds_getter
        self._token_refresher = token_refresher
        # real_user_id 用于公共 query 的 user_id；未知时传 0（上游允许）
        self._user_id = ""

    # ---- 签名与请求 ----

    def _common_query(self, region: str) -> list[tuple[str, str]]:
        language = "en" if region == "global" else "zh"
        return sorted(
            [
                ("device_platform", "web"),
                ("biz_id", "3"),
                ("app_id", "3001"),
                ("version_code", "22201"),
                ("unix", str(int(time.time() * 1000))),
                ("timezone_offset", str(_timezone_offset_seconds())),
                ("sys_language", language),
                ("lang", language),
                ("device_id", "0"),
                ("user_id", self._user_id or "0"),
                ("client", "desktop"),
                ("region", region),
                ("is_desktop", "1"),
            ]
        )

    @staticmethod
    def _signed_headers(
        now_ms: int, path_with_query: str, body: bytes | None
    ) -> dict[str, str]:
        body_text = body.decode("utf-8") if body else ""
        second = str(now_ms // 1000)
        now_text = str(now_ms)
        # GET 的 yy 以 '{}' 参与拼接；x-signature 的 body 恒为实际请求体（GET 为空）
        yy_body = body_text or "{}"
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "MiniMaxAgent",
            "yy": _md5_hex(
                _js_encode_uri_component(path_with_query)
                + "_"
                + yy_body
                + _md5_hex(now_text)
                + _YY_SUFFIX
            ),
            "x-timestamp": second,
            "x-signature": _md5_hex(second + _SIGNATURE_SALT + body_text),
        }

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: dict[str, Any] | None,
        token: str,
    ) -> dict[str, Any]:
        raw = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        res = await self._client.request(
            method,
            url,
            body=raw,
            headers=headers,
            api_key_override=token,
            timeout=_TIMEOUT,
        )
        return self._decode(res)

    @staticmethod
    def _decode(res: Any) -> dict[str, Any]:
        """解析账户接口响应并校验信封。"""
        try:
            payload = res.json()
        except ValueError as exc:
            raise MiniMaxError(
                ErrorCategory.GENERAL, "账户接口返回了非 JSON 响应"
            ) from exc
        if not isinstance(payload, dict):
            raise MiniMaxError(ErrorCategory.GENERAL, "账户接口返回了无法解析的响应")
        return MiniMaxCodeAccount._unwrap(payload)

    @staticmethod
    def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
        """校验信封并解包业务数据；data 缺失时回退整体 body（实测坑位）。"""
        base_resp = payload.get("base_resp")
        status_code = (
            base_resp.get("status_code", 0) if isinstance(base_resp, dict) else 0
        )
        status_info = payload.get("statusInfo")
        code = status_info.get("code", 0) if isinstance(status_info, dict) else 0
        if status_code == 0 and code == 0:
            data = payload.get("data")
            return data if isinstance(data, dict) else payload
        if code != 0:
            message = status_info.get("msg") if isinstance(status_info, dict) else ""
        else:
            message = base_resp.get("status_msg") if isinstance(base_resp, dict) else ""
        message = message or f"status_code={status_code}" if status_code or code else ""
        raise MiniMaxError(
            ErrorCategory.GENERAL, f"账户接口错误: {message or '未知错误'}"
        )

    async def _signed_request(
        self,
        method: str,
        origin: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        with_query: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """带签名请求；with_query 控制是否拼接公共 query（open backend 接口不带）。"""
        region, token = await self._creds_getter()
        if not token:
            raise MiniMaxError(ErrorCategory.AUTH, LOGIN_HINT)
        try:
            return await self._signed_request_once(
                method, origin, path, region, token, body, with_query, extra_headers
            )
        except MiniMaxError as exc:
            if exc.category is not ErrorCategory.AUTH or not self._token_refresher:
                raise
            region, token = await self._token_refresher()
            if not token:
                raise MiniMaxError(
                    ErrorCategory.AUTH, "登录凭据已失效。" + LOGIN_HINT
                ) from exc
            # 刷新后若区域变化，matrix/open 域名同步重算，避免重试打旧区域
            if origin in _MATRIX_ORIGINS.values():
                origin = _MATRIX_ORIGINS.get(region, origin)
            elif origin in _OPEN_ORIGINS.values():
                origin = _OPEN_ORIGINS.get(region, origin)
            return await self._signed_request_once(
                method, origin, path, region, token, body, with_query, extra_headers
            )

    async def _signed_request_once(
        self,
        method: str,
        origin: str,
        path: str,
        region: str,
        token: str,
        body: dict[str, Any] | None,
        with_query: bool,
        extra_headers: dict[str, str] | None,
    ) -> dict[str, Any]:
        if with_query:
            query = urlencode(self._common_query(region))
            path_with_query = f"{path}?{query}"
            url = f"{origin}{path_with_query}"
        else:
            path_with_query = path
            url = f"{origin}{path}"
        raw = (
            json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if body is not None
            else None
        )
        headers = self._signed_headers(int(time.time() * 1000), path_with_query, raw)
        if extra_headers:
            headers.update(extra_headers)
        res = await self._client.request(
            method,
            url,
            body=raw,
            headers=headers,
            api_key_override=token,
            timeout=_TIMEOUT,
        )
        return self._decode(res)

    # ---- 高层账户操作 ----

    async def identity(self) -> AccountIdentity:
        """拉取身份与个人分组；extra_info 失败时仅缺分组与积分余额。"""
        return await self._identity()

    async def _identity(self) -> AccountIdentity:
        region, _ = await self._creds_getter()
        origin = _MATRIX_ORIGINS.get(region, _MATRIX_ORIGINS["cn"])
        info = await self._signed_request("GET", origin, "/v1/api/user/info")
        user_info = info.get("userInfo")
        identity = AccountIdentity()
        if isinstance(user_info, dict):
            identity.real_user_id = str(
                user_info.get("realUserID") or user_info.get("real_user_id") or ""
            )
            identity.user_id = _any_to_string(user_info.get("userID"))
            identity.user_name = str(
                user_info.get("userName") or user_info.get("user_name") or ""
            )
            identity.email = str(user_info.get("email") or "")
        if not identity.real_user_id:
            raise MiniMaxError(ErrorCategory.GENERAL, "账户接口未返回 real_user_id")
        if identity.real_user_id != self._user_id:
            self._user_id = identity.real_user_id

        # 区域可能在 user/info 的 401 刷新中切换，extra_info 前按最新区域重算域
        region, _ = await self._creds_getter()
        origin = _MATRIX_ORIGINS.get(region, _MATRIX_ORIGINS["cn"])
        try:
            extra = await self._signed_request(
                "POST", origin, "/matrix/api/v1/user/get_user_extra_info", body={}
            )
        except MiniMaxError:
            return identity
        workspaces = extra.get("workspaces")
        if not isinstance(workspaces, list):
            return identity
        for ws in workspaces:
            if not isinstance(ws, dict) or str(ws.get("workspace_type")) != "0":
                continue
            identity.group_id = str(ws.get("op_group_id") or "")
            summary = ws.get("op_credit_summary")
            summary_amount = (
                summary.get("total_remaining_amount")
                if isinstance(summary, dict)
                else None
            )
            identity.credit_balance = next(
                (
                    amount
                    for amount in (
                        _amount_string(summary_amount),
                        _amount_string(ws.get("opcredit_balance")),
                        _amount_string(ws.get("total_remains_credit")),
                    )
                    if amount
                ),
                "",
            )
            break
        return identity

    async def remains(self, group_id: str = "") -> dict[str, Any]:
        """套餐余量（open platform，仅 Bearer + X-Group-Id，无签名）。

        X-Group-Id 可为空，走默认个人分组；usage_count 实为剩余次数。
        """
        region, token = await self._creds_getter()
        if not token:
            raise MiniMaxError(ErrorCategory.AUTH, LOGIN_HINT)
        origin = _OPEN_ORIGINS.get(region, _OPEN_ORIGINS["cn"])
        try:
            return await self._send(
                "GET",
                f"{origin}/v1/api/openplatform/coding_plan/remains",
                headers={
                    "Accept": "application/json",
                    "X-Group-Id": group_id.strip(),
                },
                body=None,
                token=token,
            )
        except MiniMaxError as exc:
            if exc.category is not ErrorCategory.AUTH or not self._token_refresher:
                raise
            region, token = await self._token_refresher()
            if not token:
                raise MiniMaxError(
                    ErrorCategory.AUTH, "登录凭据已失效。" + LOGIN_HINT
                ) from exc
            origin = _OPEN_ORIGINS.get(region, _OPEN_ORIGINS["cn"])
            return await self._send(
                "GET",
                f"{origin}/v1/api/openplatform/coding_plan/remains",
                headers={
                    "Accept": "application/json",
                    "X-Group-Id": group_id.strip(),
                },
                body=None,
                token=token,
            )

    async def credit_pool(self) -> dict[str, Any]:
        """积分池（含 token_plan_credit_fallback_enabled，实测字段名）。"""
        region, _ = await self._creds_getter()
        origin = _OPEN_ORIGINS.get(region, _OPEN_ORIGINS["cn"])
        return await self._signed_request(
            "GET", origin, "/backend/account/token_plan_credit", with_query=False
        )

    async def credit_details(self) -> dict[str, Any]:
        """积分明细（credits 明细列表）。"""
        region, _ = await self._creds_getter()
        origin = _MATRIX_ORIGINS.get(region, _MATRIX_ORIGINS["cn"])
        return await self._signed_request(
            "GET", origin, "/minimax-cloud/api/v1/credit/details"
        )

    async def signin_status(self) -> dict[str, Any]:
        """签到状态（days 列表，含 is_today 与 status）。"""
        region, _ = await self._creds_getter()
        origin = _MATRIX_ORIGINS.get(region, _MATRIX_ORIGINS["cn"])
        return await self._signed_request(
            "GET", origin, "/minimax-cloud/api/v1/signin/status"
        )

    async def signin_claim(self) -> dict[str, Any]:
        """领取当日签到；上游按天幂等（重复领取返回当日同一 claim_id）。"""
        region, _ = await self._creds_getter()
        origin = _MATRIX_ORIGINS.get(region, _MATRIX_ORIGINS["cn"])
        return await self._signed_request(
            "POST", origin, "/minimax-cloud/api/v1/signin/claim", body={}
        )
