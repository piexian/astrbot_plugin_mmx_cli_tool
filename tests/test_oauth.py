"""OAuth 设备码流程与 Token 生命周期测试（MockTransport，无真实网络）。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import time
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mmx_cli_tool.mmx.oauth import (
    DeviceAuthInfo,
    OAuthClient,
    OAuthError,
    OAuthToken,
    TokenStore,
    generate_pkce,
)


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _make_client(handler, sleep=None) -> OAuthClient:
    transport = httpx.MockTransport(handler)
    return OAuthClient(http=httpx.AsyncClient(transport=transport), sleep=sleep or _no_sleep)


async def _no_sleep(seconds: float) -> None:
    return None


class PKCETests(unittest.TestCase):
    def test_pkce_s256(self):
        verifier, challenge = generate_pkce()
        self.assertEqual(43, len(verifier))
        self.assertNotIn("=", verifier)
        self.assertNotIn("=", challenge)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        self.assertEqual(expected, challenge)

    def test_pkce_random(self):
        self.assertNotEqual(generate_pkce(), generate_pkce())


class DeviceAuthorizationTests(unittest.TestCase):
    def test_standard_flow_request_and_parse(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(_form(request))
            self.assertEqual(
                "https://account.minimax.cn/oauth2/device/code", str(request.url)
            )
            return httpx.Response(
                200,
                json={
                    "device_code": "dc-123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://account.minimax.cn/device",
                    "verification_uri_complete": "https://account.minimax.cn/device?code=ABCD-EFGH",
                    "expires_in": 600,
                    "interval": 5,
                },
            )

        client = _make_client(handler)
        info = asyncio.run(client.start_device_authorization("cn"))
        self.assertEqual("mcode-public", seen["client_id"])
        self.assertEqual("agent.default", seen["scope"])
        self.assertEqual("agent-backend", seen["audience"])
        self.assertEqual("S256", seen["code_challenge_method"])
        self.assertTrue(seen["code_challenge"])
        self.assertEqual("dc-123", info.device_code)
        self.assertEqual("ABCD-EFGH", info.user_code)
        self.assertEqual(600, info.expires_in)
        self.assertEqual(5.0, info.interval)
        self.assertFalse(info.use_user_code_polling)
        self.assertEqual(
            "https://account.minimax.cn/device?code=ABCD-EFGH",
            info.verification_uri_complete,
        )

    def test_global_region_endpoint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                "https://account.minimax.io/oauth2/device/code", str(request.url)
            )
            return httpx.Response(400, json={"error": "unauthorized"})

        client = _make_client(handler)
        with self.assertRaises(OAuthError) as ctx:
            asyncio.run(client.start_device_authorization("global"))
        self.assertEqual("unauthorized", ctx.exception.code)

    def test_account_user_code_variant(self):
        """无 device_code 且带 expired_in(ms) 时切换 user_code 轮询，interval 为毫秒。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "user_code": "XY-99",
                    "verification_url": "https://account.minimax.cn/active",
                    "expired_in": 300000,
                    "interval": 2000,
                },
            )

        client = _make_client(handler)
        info = asyncio.run(client.start_device_authorization("cn"))
        self.assertTrue(info.use_user_code_polling)
        self.assertEqual("XY-99", info.device_code)
        self.assertEqual("XY-99", info.user_code)
        self.assertEqual(300000, info.expires_in)  # 相对值按秒直取，对齐上游行为
        self.assertEqual(2.0, info.interval)
        self.assertEqual("https://account.minimax.cn/active", info.verification_uri)

    def test_account_variant_absolute_ms_timestamp(self):
        """expired_in 为绝对毫秒时间戳时换算为剩余秒数。"""
        future_ms = time.time() * 1000 + 120_000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "user_code": "TS-1",
                    "verification_url": "https://account.minimax.cn/active",
                    "expired_in": future_ms,
                },
            )

        client = _make_client(handler)
        info = asyncio.run(client.start_device_authorization("cn"))
        self.assertTrue(info.use_user_code_polling)
        self.assertIn(info.expires_in, (119, 120))

    def test_invalid_response_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"user_code": "only"})

        client = _make_client(handler)
        with self.assertRaises(OAuthError) as ctx:
            asyncio.run(client.start_device_authorization("cn"))
        self.assertEqual("invalid_device_authorization_response", ctx.exception.code)


def _auth_info(**overrides) -> DeviceAuthInfo:
    base = dict(
        device_code="dc-1",
        user_code="UC-1",
        code_verifier="verifier-1",
        verification_uri="https://example.com/device",
        verification_uri_complete="https://example.com/device?code=UC-1",
        expires_in=600,
        interval=1.0,
    )
    base.update(overrides)
    return DeviceAuthInfo(**base)


_TOKEN_BODY = {
    "access_token": "mmoat_abc",
    "refresh_token": "mmort_xyz",
    "token_type": "Bearer",
    "expires_in": 3600,
    "scope": "agent.default",
}


class PollDeviceTokenTests(unittest.TestCase):
    def test_pending_then_success(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(_form(request))
            if len(calls) < 3:
                return httpx.Response(200, json={"status": "pending"})
            return httpx.Response(200, json=_TOKEN_BODY)

        client = _make_client(handler)
        token = asyncio.run(client.poll_device_token(_auth_info(), "cn"))
        self.assertEqual("mmoat_abc", token.access_token)
        self.assertEqual("mmort_xyz", token.refresh_token)
        self.assertEqual("cn", token.region)
        self.assertGreater(token.expires_at, time.time())
        self.assertEqual(3, len(calls))
        for form in calls:
            self.assertEqual(
                "urn:ietf:params:oauth:grant-type:device_code", form["grant_type"]
            )
            self.assertEqual("dc-1", form["device_code"])
            self.assertEqual("verifier-1", form["code_verifier"])

    def test_user_code_polling_parameter(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(_form(request))
            return httpx.Response(200, json=_TOKEN_BODY)

        client = _make_client(handler)
        info = _auth_info(use_user_code_polling=True)
        asyncio.run(client.poll_device_token(info, "cn"))
        self.assertEqual("UC-1", calls[0]["user_code"])
        self.assertNotIn("device_code", calls[0])

    def test_slow_down_increases_interval(self):
        sleeps: list[float] = []
        state = {"n": 0}

        async def record_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        def handler(request: httpx.Request) -> httpx.Response:
            state["n"] += 1
            if state["n"] == 1:
                return httpx.Response(200, json={"status": "slow_down"})
            return httpx.Response(200, json=_TOKEN_BODY)

        client = _make_client(handler, sleep=record_sleep)
        asyncio.run(client.poll_device_token(_auth_info(interval=2.0), "cn"))
        self.assertEqual([7.0], sleeps)

    def test_denied_and_expired(self):
        for status, code in (("denied", "access_denied"), ("expired", "expired_token")):
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"status": status})

            client = _make_client(handler)
            with self.assertRaises(OAuthError) as ctx:
                asyncio.run(client.poll_device_token(_auth_info(), "cn"))
            self.assertEqual(code, ctx.exception.code)

    def test_oauth_error_style_pending_then_success(self):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json=_TOKEN_BODY)

        client = _make_client(handler)
        token = asyncio.run(client.poll_device_token(_auth_info(), "cn"))
        self.assertEqual("mmoat_abc", token.access_token)

    def test_timeout_raises_expired(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "pending"})

        client = _make_client(handler)
        with self.assertRaises(OAuthError) as ctx:
            asyncio.run(
                client.poll_device_token(_auth_info(expires_in=3600), "cn", max_wait=0)
            )
        self.assertEqual("expired_token", ctx.exception.code)

    def test_invalid_token_response_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"access_token": "x", "token_type": "Bearer"})

        client = _make_client(handler)
        with self.assertRaises(OAuthError) as ctx:
            asyncio.run(client.poll_device_token(_auth_info(), "cn"))
        self.assertEqual("invalid_token_response", ctx.exception.code)


class RefreshTests(unittest.TestCase):
    def test_refresh_keeps_old_token_when_not_rotated(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(_form(request))
            return httpx.Response(
                200,
                json={
                    "access_token": "mmoat_new",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                },
            )

        client = _make_client(handler)
        token = asyncio.run(client.refresh("mmort_old", "cn"))
        self.assertEqual("refresh_token", seen["grant_type"])
        self.assertEqual("mmort_old", seen["refresh_token"])
        self.assertEqual("mmoat_new", token.access_token)
        self.assertEqual("mmort_old", token.refresh_token)


class TokenStoreTests(unittest.TestCase):
    def test_save_load_clear_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            store = TokenStore(path)
            self.assertIsNone(store.load())
            token = OAuthToken(
                access_token="a", refresh_token="r", expires_at=9999999999.0,
                region="global", scopes=["agent.default"],
            )
            store.save(token)
            if os.name != "nt":
                self.assertEqual(0o600, path.stat().st_mode & 0o777)
            loaded = store.load()
            self.assertIsNotNone(loaded)
            self.assertEqual("a", loaded.access_token)
            self.assertEqual("global", loaded.region)
            self.assertEqual(["agent.default"], loaded.scopes)
            store.clear()
            self.assertIsNone(store.load())
            store.clear()  # 幂等

    def test_load_invalid_json_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "auth.json"
            path.write_text("not json", encoding="utf-8")
            self.assertIsNone(TokenStore(path).load())
            path.write_text(json.dumps({"access_token": "only"}), encoding="utf-8")
            self.assertIsNone(TokenStore(path).load())

    def test_token_expiry_margin(self):
        token = OAuthToken("a", "r", expires_at=time.time() + 30)
        self.assertTrue(token.is_expired())
        self.assertFalse(token.is_expired(margin=10))


if __name__ == "__main__":
    unittest.main()
