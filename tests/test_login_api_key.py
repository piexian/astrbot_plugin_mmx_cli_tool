"""登录自动获取开放平台 API Key 的 Main 层测试（MockTransport 与桩，无真实网络）。"""

from __future__ import annotations

import logging
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from support import Event, plugin_modules  # noqa: E402
from test_llm_provider import make_fake_context, provider_stubs  # noqa: E402

from astrbot_plugin_mmx_cli_tool.mmx.errors import (  # noqa: E402
    ErrorCategory,
    MiniMaxError,
)
from astrbot_plugin_mmx_cli_tool.mmx.oauth import (  # noqa: E402
    DeviceAuthInfo,
    OAuthToken,
)

CN_URL = "https://api.minimaxi.com"
GLOBAL_URL = "https://api.minimax.io"
KEY_1 = "sk-cp-fake-key-1"
KEY_2 = "sk-cp-fake-key-2"


class RecordingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


class LoginEvent(Event):
    """/mmx login 区域选择会话用的事件桩。"""

    def __init__(self, text: str = "") -> None:
        super().__init__(text)

    def get_sender_id(self) -> str:
        return "admin"

    async def send(self, message) -> None:
        return None


def fake_session_waiter(timeout):
    """直接把回复 "2"（国际站）喂给区域选择处理函数。"""

    def decorate(handler):
        async def run(event):
            controller = SimpleNamespace(keep=lambda *a, **k: None, stop=lambda: None)
            await handler(controller, LoginEvent("2"))

        return run

    return decorate


class LoginKeyTestCase(unittest.IsolatedAsyncioTestCase):
    """每个用例一个临时数据目录；fetch_api_key 默认返回 KEY_1。"""

    config: dict = {}

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.enterContext(provider_stubs())
        self.main, _ = self.enterContext(plugin_modules(self.root))

        self.log = RecordingHandler()
        logger = logging.getLogger("mmx-test")
        logger.addHandler(self.log)
        self.addCleanup(logger.removeHandler, self.log)

        self.plugin = self.make_plugin()

    def make_plugin(self):
        context, _, _ = make_fake_context()
        plugin = self.main.Main(context, dict(self.config))
        self.addAsyncCleanup(plugin.terminate)
        plugin._account.fetch_api_key = AsyncMock(return_value=KEY_1)
        return plugin

    async def asyncTearDown(self):
        # 安全约束：Key 不得出现在任何日志里（断言本身不回显日志内容）
        leaked = [m for m in self.log.messages if KEY_1 in m or KEY_2 in m]
        self.assertEqual(0, len(leaked), "API Key 出现在日志中")

    def login(self, plugin=None, *, epoch="epoch-1", region="cn") -> OAuthToken:
        token = OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=time.time() + 7200,
            region=region,
            login_epoch=epoch,
        )
        (plugin or self.plugin)._token_store.save(token)
        return token


class SyncLoginKeyTests(LoginKeyTestCase):
    async def test_not_logged_in_has_no_key_and_service_unavailable(self):
        await self.plugin.initialize()

        self.assertFalse(await self.plugin._sync_login_key())

        self.plugin._account.fetch_api_key.assert_not_awaited()
        self.assertEqual("", self.plugin._client.api_key)
        self.assertEqual(CN_URL, self.plugin._client.base_url)
        self.assertFalse(self.plugin._has_open_key())
        status = self.plugin._service.get_status()
        self.assertEqual("unavailable", status["state"])
        self.assertEqual("missing_api_key", status["reason"])

    async def test_first_sync_fetches_stores_and_follows_login_region(self):
        await self.plugin.initialize()
        self.login(region="global")

        self.assertTrue(await self.plugin._sync_login_key())

        self.plugin._account.fetch_api_key.assert_awaited_once()
        self.assertEqual(KEY_1, self.plugin._client.api_key)
        self.assertEqual(GLOBAL_URL, self.plugin._client.base_url)
        self.assertEqual(KEY_1, self.plugin._api_key_store.load("epoch-1"))
        self.assertTrue(self.plugin._has_open_key())
        status = self.plugin._service.get_status()
        self.assertEqual("ready", status["state"])
        self.assertIsNone(status["reason"])

    async def test_second_sync_reuses_stored_key_without_fetching(self):
        await self.plugin.initialize()
        self.login()
        await self.plugin._sync_login_key()
        self.plugin._client.set_credentials("", CN_URL)

        self.assertTrue(await self.plugin._sync_login_key())

        self.plugin._account.fetch_api_key.assert_awaited_once()
        self.assertEqual(KEY_1, self.plugin._client.api_key)

    async def test_refetch_replaces_stored_key(self):
        self.plugin._account.fetch_api_key.side_effect = [KEY_1, KEY_2]
        await self.plugin.initialize()
        self.login()
        await self.plugin._sync_login_key()

        self.assertTrue(await self.plugin._sync_login_key(refetch=True))

        self.assertEqual(2, self.plugin._account.fetch_api_key.await_count)
        self.assertEqual(KEY_2, self.plugin._client.api_key)
        self.assertEqual(KEY_2, self.plugin._api_key_store.load("epoch-1"))

    async def test_new_login_epoch_ignores_stored_key(self):
        self.plugin._account.fetch_api_key.side_effect = [KEY_1, KEY_2]
        await self.plugin.initialize()
        self.login(epoch="epoch-1", region="cn")
        await self.plugin._sync_login_key()

        self.login(epoch="epoch-2", region="global")
        self.assertTrue(await self.plugin._sync_login_key())

        self.assertEqual(2, self.plugin._account.fetch_api_key.await_count)
        self.assertEqual(KEY_2, self.plugin._client.api_key)
        self.assertEqual(GLOBAL_URL, self.plugin._client.base_url)
        self.assertEqual("", self.plugin._api_key_store.load("epoch-1"))
        self.assertEqual(KEY_2, self.plugin._api_key_store.load("epoch-2"))

    async def test_fetch_failure_clears_previous_key(self):
        await self.plugin.initialize()
        self.login(region="global")
        await self.plugin._sync_login_key()
        self.assertEqual("ready", self.plugin._service.get_status()["state"])

        self.plugin._account.fetch_api_key.side_effect = MiniMaxError(
            ErrorCategory.GENERAL, "账户接口未返回开放平台 API Key"
        )
        self.assertFalse(await self.plugin._sync_login_key(refetch=True))

        self.assertEqual("", self.plugin._client.api_key)
        self.assertEqual(CN_URL, self.plugin._client.base_url)
        status = self.plugin._service.get_status()
        self.assertEqual("unavailable", status["state"])
        self.assertEqual("missing_api_key", status["reason"])

    async def test_network_error_is_logged_not_raised(self):
        await self.plugin.initialize()
        self.login()
        self.plugin._account.fetch_api_key.side_effect = httpx.ConnectError("offline")

        self.assertFalse(await self.plugin._sync_login_key())

        self.assertEqual("", self.plugin._client.api_key)
        self.assertEqual("", self.plugin._api_key_store.load("epoch-1"))
        self.assertTrue(any("offline" in m for m in self.log.messages))

    async def test_initialize_reports_readiness_from_login_key(self):
        self.login(region="global")

        await self.plugin.initialize()

        self.assertEqual("ready", self.plugin._service.get_status()["state"])
        self.assertEqual(KEY_1, self.plugin._client.api_key)

    async def test_restart_reuses_stored_key_without_fetching(self):
        self.login(region="global")
        await self.plugin.initialize()

        restarted = self.make_plugin()
        await restarted.initialize()

        restarted._account.fetch_api_key.assert_not_awaited()
        self.assertEqual(KEY_1, restarted._client.api_key)
        self.assertEqual(GLOBAL_URL, restarted._client.base_url)
        self.assertEqual("ready", restarted._service.get_status()["state"])

    async def test_login_key_serves_open_platform_requests(self):
        """走真实 fetch_api_key：Key 与区域域名都来自账户接口和登录区域。"""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/backend/account/token_plan_credit":
                return httpx.Response(
                    200, json={"api_key": f" {KEY_1} ", "base_resp": {"status_code": 0}}
                )
            return httpx.Response(200, json={"base_resp": {"status_code": 0}})

        plugin = self.plugin
        del plugin._account.fetch_api_key  # 还原为真实的 fetch_api_key
        client = plugin._client
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        await plugin.initialize()
        self.login(plugin, region="global")

        self.assertTrue(await plugin._sync_login_key())
        await client.request("GET", "/v1/files/list")

        credit, files = requests
        self.assertEqual("platform.minimax.io", credit.url.host)
        self.assertEqual("api.minimax.io", files.url.host)
        self.assertEqual(f"Bearer {KEY_1}", files.headers["authorization"])


class LoginKeyCommandTests(LoginKeyTestCase):
    async def test_logout_clears_stored_key_and_client_key(self):
        await self.plugin.initialize()
        self.login(region="global")
        await self.plugin._sync_login_key()
        self.plugin._oauth.revoke = AsyncMock()

        replies = [r async for r in self.plugin.mmx_logout(Event())]

        self.assertIn("已退出", replies[0])
        self.assertIsNone(self.plugin._token_store.load())
        self.assertEqual("", self.plugin._api_key_store.load("epoch-1"))
        self.assertEqual("", self.plugin._client.api_key)
        self.assertEqual(CN_URL, self.plugin._client.base_url)
        self.assertEqual("unavailable", self.plugin._service.get_status()["state"])

    async def test_web_logout_clears_stored_key_and_client_key(self):
        await self.plugin.initialize()
        self.login(region="global")
        await self.plugin._sync_login_key()
        self.plugin._oauth.revoke = AsyncMock()

        result = await self.plugin._web_api._oauth_logout_payload()

        self.assertEqual({"ok": True}, result)
        self.assertEqual("", self.plugin._api_key_store.load("epoch-1"))
        self.assertEqual("", self.plugin._client.api_key)
        self.assertEqual("unavailable", self.plugin._service.get_status()["state"])

    async def run_login_flow(self, plugin) -> str:
        token = OAuthToken(
            "access",
            "refresh",
            time.time() + 7200,
            region="global",
            login_epoch="epoch-1",
        )
        auth = DeviceAuthInfo(
            device_code="device-code",
            user_code="ABCD-1234",
            code_verifier="verifier",
            verification_uri="https://account.minimax.io/oauth2/device",
            verification_uri_complete="https://account.minimax.io/oauth2/device?user_code=ABCD-1234",
            expires_in=600,
        )
        plugin._oauth.start_device_authorization = AsyncMock(return_value=auth)
        plugin._oauth.poll_device_token = AsyncMock(return_value=token)
        plugin._sync_provider_source = AsyncMock()
        plugin._provider_source_state = "disabled"

        with patch.object(self.main, "session_waiter", fake_session_waiter):
            replies = [r async for r in plugin._run_login_flow(LoginEvent("login"))]
        return replies[-1]

    async def test_login_reply_reports_auto_fetched_key(self):
        reply = await self.run_login_flow(self.plugin)

        self.assertIn("登录成功", reply)
        self.assertIn("已自动获取 API Key", reply)
        self.assertEqual(KEY_1, self.plugin._client.api_key)
        self.assertEqual(GLOBAL_URL, self.plugin._client.base_url)

    async def test_login_reply_reports_fetch_failure(self):
        self.plugin._account.fetch_api_key.side_effect = MiniMaxError(
            ErrorCategory.GENERAL, "账户接口未返回开放平台 API Key"
        )

        reply = await self.run_login_flow(self.plugin)

        self.assertIn("登录成功", reply)
        self.assertIn("未能自动获取 API Key", reply)
        self.assertIn("填写 api_key", reply)

    async def test_login_always_refetches_even_with_stored_key(self):
        self.login(epoch="epoch-1", region="global")
        self.plugin._api_key_store.save("epoch-1", KEY_2)

        await self.run_login_flow(self.plugin)

        self.plugin._account.fetch_api_key.assert_awaited_once()
        self.assertEqual(KEY_1, self.plugin._client.api_key)


class StaticKeyTests(LoginKeyTestCase):
    config = {"api_key": ["sk-static-fake"], "region": "global"}

    async def test_configured_key_is_never_replaced(self):
        await self.plugin.initialize()
        self.login(region="cn")

        self.assertTrue(await self.plugin._sync_login_key())
        self.assertTrue(await self.plugin._sync_login_key(refetch=True))

        self.plugin._account.fetch_api_key.assert_not_awaited()
        self.assertEqual("sk-static-fake", self.plugin._client.api_key)
        self.assertEqual(GLOBAL_URL, self.plugin._client.base_url)
        self.assertEqual("ready", self.plugin._service.get_status()["state"])
        self.assertTrue(self.plugin._has_open_key())

    async def test_login_reply_has_no_key_notice(self):
        self.assertEqual("", self.plugin._login_key_notice(True))
        self.assertEqual("", self.plugin._login_key_notice(False))

    async def test_logout_keeps_configured_key(self):
        await self.plugin.initialize()
        self.login()
        self.plugin._oauth.revoke = AsyncMock()

        [r async for r in self.plugin.mmx_logout(Event())]

        self.assertEqual("sk-static-fake", self.plugin._client.api_key)
        self.assertEqual("ready", self.plugin._service.get_status()["state"])


class KeyPoolTests(LoginKeyTestCase):
    config = {"api_key": ["sk-static-fake-1", "sk-static-fake-2"]}

    async def test_key_pool_counts_as_open_key_without_fetching(self):
        await self.plugin.initialize()
        self.login()

        self.assertTrue(await self.plugin._sync_login_key())

        self.plugin._account.fetch_api_key.assert_not_awaited()
        self.assertTrue(self.plugin._has_open_key())


class BaseUrlOverrideTests(LoginKeyTestCase):
    config = {"base_url": "https://proxy.example.com/"}

    async def test_base_url_override_beats_login_region(self):
        await self.plugin.initialize()
        self.login(region="global")

        self.assertTrue(await self.plugin._sync_login_key())

        self.assertEqual(KEY_1, self.plugin._client.api_key)
        self.assertEqual("https://proxy.example.com", self.plugin._client.base_url)

    async def test_failure_falls_back_to_override_not_region_default(self):
        await self.plugin.initialize()
        self.login(region="global")
        self.plugin._account.fetch_api_key.side_effect = MiniMaxError(
            ErrorCategory.GENERAL, "账户接口未返回开放平台 API Key"
        )

        self.assertFalse(await self.plugin._sync_login_key())

        self.assertEqual("https://proxy.example.com", self.plugin._client.base_url)


if __name__ == "__main__":
    unittest.main()
