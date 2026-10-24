"""MiniMax 账号 OAuth 客户端 — RFC 8628 Device Authorization Grant + PKCE S256。

对齐 @mavis/oauth-core：client_id=mcode-public，scope=agent.default，
audience=agent-backend；兼容账号服务返回的 user_code 轮询变体
（无 device_code、expired_in/interval 为毫秒）。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .endpoints import (
    OAUTH_AUDIENCE,
    OAUTH_CLIENT_ID,
    OAUTH_DEVICE_GRANT,
    OAUTH_SCOPE,
    oauth_device_code_endpoint,
    oauth_revoke_endpoint,
    oauth_token_endpoint,
)


class OAuthError(Exception):
    """OAuth 协议错误，code 对齐上游（access_denied/expired_token 等）。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or f"OAuth 请求被拒绝 ({code})")
        self.code = code


@dataclass
class DeviceAuthInfo:
    """设备授权发起结果。"""

    device_code: str
    user_code: str
    code_verifier: str
    verification_uri: str
    verification_uri_complete: str
    expires_in: int
    interval: float = 5.0
    use_user_code_polling: bool = False


@dataclass
class OAuthToken:
    """OAuth 访问令牌及本次登录的会话命名空间。"""

    access_token: str
    refresh_token: str
    expires_at: float
    region: str = "cn"
    scopes: list[str] = field(default_factory=list)
    login_epoch: str = ""

    def is_expired(self, margin: float = 60.0) -> bool:
        return time.time() >= self.expires_at - margin

    def ensure_login_epoch(self) -> bool:
        """旧登录态仅迁移一次；返回是否生成了新 epoch。"""
        if self.login_epoch.strip():
            return False
        self.login_epoch = secrets.token_hex(16)
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "region": self.region,
            "scopes": self.scopes,
            "login_epoch": self.login_epoch,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthToken | None":
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires_at = data.get("expires_at")
        if not access or not refresh or not isinstance(expires_at, (int, float)):
            return None
        scopes = data.get("scopes")
        return cls(
            access_token=str(access),
            refresh_token=str(refresh),
            expires_at=float(expires_at),
            region=str(data.get("region") or "cn"),
            scopes=[str(s) for s in scopes] if isinstance(scopes, list) else [],
            login_epoch=str(data.get("login_epoch") or "").strip(),
        )


def generate_pkce() -> tuple[str, str]:
    """生成 PKCE code_verifier 与 S256 code_challenge。"""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _read_str(body: dict[str, Any], key: str) -> str | None:
    value = body.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _read_positive_number(body: dict[str, Any], key: str) -> float | None:
    value = body.get(key)
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    return None


def _parse_scopes(value: Any) -> list[str]:
    if isinstance(value, str):
        return value.split()
    if isinstance(value, list):
        return [str(s) for s in value]
    return []


def _normalize_account_expiry_seconds(
    expired_in: float | None, *, milliseconds: bool = False
) -> int | None:
    """账号服务 expired_in 可能是剩余量或绝对毫秒时间戳；user_code 变体相对值为毫秒。"""
    if expired_in is None:
        return None
    if milliseconds and expired_in < 1_000_000_000_000:
        expired_in = expired_in / 1000
    if expired_in < 1_000_000_000_000:
        return max(1, int(expired_in))
    remaining_ms = expired_in - time.time() * 1000
    return int(remaining_ms / 1000 + 0.5) if remaining_ms > 0 else None

class OAuthClient:
    """MiniMax 账号 OAuth 设备码客户端。"""

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        *,
        timeout: float = 30,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._owns_http = http is None
        self._sleep = sleep or asyncio.sleep

    async def close(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def _post_form(self, url: str, values: dict[str, str]) -> tuple[bool, int, dict[str, Any]]:
        """POST 表单，返回 (ok, status, body)；body 带 error 字段时 ok=False。"""
        try:
            res = await self._http.post(
                url,
                data=values,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            )
        except httpx.HTTPError as exc:
            raise OAuthError("network_error", f"OAuth 网络请求失败: {exc}") from exc
        try:
            body = res.json()
        except ValueError as exc:
            raise OAuthError("invalid_json_response") from exc
        if not isinstance(body, dict):
            raise OAuthError("invalid_json_response")
        error = _read_str(body, "error")
        return (res.is_success and not error), res.status_code, body

    async def start_device_authorization(self, region: str = "cn") -> DeviceAuthInfo:
        """发起设备授权，返回验证码与授权链接。"""
        code_verifier, code_challenge = generate_pkce()
        ok, _, body = await self._post_form(
            oauth_device_code_endpoint(region),
            {
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_SCOPE,
                "audience": OAUTH_AUDIENCE,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
            },
        )
        if not ok:
            raise OAuthError(_read_str(body, "error") or "device_authorization_failed")

        user_code = _read_str(body, "user_code")
        device_code = _read_str(body, "device_code")
        expired_in_ms = _read_positive_number(body, "expired_in")
        # 账号服务变体：无 device_code，用 user_code 轮询，时间单位为毫秒
        use_user_code_polling = not device_code and bool(user_code) and expired_in_ms is not None
        if use_user_code_polling:
            device_code = user_code
        verification_uri = _read_str(body, "verification_uri") or _read_str(body, "verification_url")
        expires_in_value = _read_positive_number(body, "expires_in")
        expires_in = (
            int(expires_in_value)
            if expires_in_value
            else _normalize_account_expiry_seconds(
                expired_in_ms, milliseconds=use_user_code_polling
            )
        )
        raw_interval = _read_positive_number(body, "interval")
        interval = (
            raw_interval / 1000.0
            if use_user_code_polling and raw_interval is not None
            else (raw_interval or 5.0)
        )
        if not device_code or not user_code or not verification_uri or not expires_in:
            raise OAuthError("invalid_device_authorization_response")
        return DeviceAuthInfo(
            device_code=device_code,
            user_code=user_code,
            code_verifier=code_verifier,
            verification_uri=verification_uri,
            verification_uri_complete=_read_str(body, "verification_uri_complete") or verification_uri,
            expires_in=int(expires_in),
            interval=interval,
            use_user_code_polling=use_user_code_polling,
        )

    async def poll_device_token(
        self,
        auth_info: DeviceAuthInfo,
        region: str = "cn",
        *,
        max_wait: float | None = None,
    ) -> OAuthToken:
        """轮询设备授权结果，直到成功、拒绝或超时。"""
        deadline = time.monotonic() + min(
            float(auth_info.expires_in), max_wait if max_wait is not None else float(auth_info.expires_in)
        )
        interval = auth_info.interval
        polling = (
            {"user_code": auth_info.user_code}
            if auth_info.use_user_code_polling
            else {"device_code": auth_info.device_code}
        )
        while time.monotonic() < deadline:
            ok, status, body = await self._post_form(
                oauth_token_endpoint(region),
                {
                    "grant_type": OAUTH_DEVICE_GRANT,
                    **polling,
                    "client_id": OAUTH_CLIENT_ID,
                    "code_verifier": auth_info.code_verifier,
                },
            )
            polling_status = _read_str(body, "status")
            if ok and polling_status == "pending":
                await self._sleep(interval)
                continue
            if ok and polling_status == "slow_down":
                interval += 5.0
                await self._sleep(interval)
                continue
            if ok and polling_status in {"denied", "access_denied"}:
                raise OAuthError("access_denied")
            if ok and polling_status in {"expired", "expired_token"}:
                raise OAuthError("expired_token")
            if ok:
                return self._parse_token_grant(body, region)
            error = _read_str(body, "error") or "device_authorization_failed"
            if error == "authorization_pending":
                await self._sleep(interval)
                continue
            if error == "slow_down":
                interval += 5.0
                await self._sleep(interval)
                continue
            if error in {"access_denied", "expired_token"}:
                raise OAuthError(error)
            raise OAuthError(error, f"OAuth 轮询失败 ({error}, HTTP {status})")
        raise OAuthError("expired_token", "OAuth 设备授权已超时")

    def _parse_token_grant(
        self,
        body: dict[str, Any],
        region: str,
        previous_refresh_token: str | None = None,
        previous_login_epoch: str = "",
    ) -> OAuthToken:
        access_token = _read_str(body, "access_token")
        refresh_token = _read_str(body, "refresh_token") or previous_refresh_token
        token_type = _read_str(body, "token_type")
        expires_in = _read_positive_number(body, "expires_in")
        scopes = _parse_scopes(body.get("scope"))
        if (
            not access_token
            or not refresh_token
            or (token_type or "").lower() != "bearer"
            or not expires_in
        ):
            raise OAuthError("invalid_token_response")
        return OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=time.time() + expires_in,
            region=region,
            scopes=scopes,
            login_epoch=previous_login_epoch or secrets.token_hex(16),
        )

    async def refresh(
        self, refresh_token: str, region: str = "cn", *, login_epoch: str = ""
    ) -> OAuthToken:
        """刷新访问令牌并继承登录 epoch。"""
        ok, _, body = await self._post_form(
            oauth_token_endpoint(region),
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
                "scope": OAUTH_SCOPE,
                "audience": OAUTH_AUDIENCE,
            },
        )
        if not ok:
            raise OAuthError(_read_str(body, "error") or "refresh_failed")
        return self._parse_token_grant(
            body,
            region,
            previous_refresh_token=refresh_token,
            previous_login_epoch=login_epoch,
        )


    async def revoke(self, refresh_token: str, region: str = "cn") -> None:
        """撤销 refresh_token；服务端不支持或失败时不抛错（本地仍会清除）。"""
        try:
            await self._post_form(
                oauth_revoke_endpoint(region),
                {
                    "token": refresh_token,
                    "token_type_hint": "refresh_token",
                    "client_id": OAUTH_CLIENT_ID,
                },
            )
        except OAuthError:
            pass


class TokenStore:
    """OAuth Token 持久化（auth.json，权限 0600）。"""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> OAuthToken | None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return OAuthToken.from_dict(data)

    def save(self, token: OAuthToken) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(self._path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(token.to_dict(), ensure_ascii=False, indent=2))
        os.replace(tmp, self._path)
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass
    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass
