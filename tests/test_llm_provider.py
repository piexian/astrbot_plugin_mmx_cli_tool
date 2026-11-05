"""MiniMax Code LLM Provider 测试（AstrBot/anthropic 桩，无真实网络）。"""

from __future__ import annotations

import hashlib
import re
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from support import plugin_modules


class ProviderMetaStub:
    def __init__(self, id, model, type, provider_type=None):
        self.id = id
        self.model = model
        self.type = type
        self.provider_type = provider_type


class ProviderTypeStub:
    CHAT_COMPLETION = "chat_completion"


class AsyncAnthropicStub:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class AuthenticationErrorStub(Exception):
    pass


PROVIDER_ANTHROPIC_BEHAVIOR = {
    "text_chat": None,  # (callable | None)
    "text_chat_stream": None,
}


def make_provider_anthropic_stub():
    class ProviderAnthropicStub:
        def __init__(
            self, provider_config, provider_settings, *, use_api_key: bool = True
        ):
            self.provider_config = provider_config
            self.used_api_key = use_api_key
            api_base = str(provider_config.get("api_base", "") or "").strip()
            self.base_url = (api_base or "https://api.anthropic.com").rstrip("/")
            self.base_url = self.base_url.removesuffix("/v1")
            self.timeout = provider_config.get("timeout", 120)
            self.request_headers = {}
            self.custom_headers = self.request_headers
            self.captured_queries = []
            self.thinking_config = provider_config.get("anth_thinking_config", {})
            self.set_model(provider_config.get("model", "unknown"))

        def set_model(self, model_name):
            self.model_name = model_name

        def get_model(self):
            return self.model_name

        def _create_http_client(self, provider_config):
            return None

        def resolve_request_headers(self, **kwargs):
            return {}

        def _stub_extra_body(self):
            extra = self.provider_config.get("custom_extra_body")
            return dict(extra) if isinstance(extra, dict) else {}

        def _apply_thinking_config(self, payloads):
            thinking_type = self.thinking_config.get("type", "")
            if thinking_type == "adaptive":
                payloads["thinking"] = {"type": "adaptive"}
                effort = self.thinking_config.get("effort", "")
                output_cfg = dict(payloads.get("output_config", {}))
                if effort:
                    output_cfg["effort"] = effort
                if output_cfg:
                    payloads["output_config"] = output_cfg
            elif not thinking_type and self.thinking_config.get("budget"):
                payloads["thinking"] = {
                    "budget_tokens": self.thinking_config.get("budget"),
                    "type": "enabled",
                }

        async def _query(self, payloads, tools, **kwargs):
            self._apply_thinking_config(payloads)
            self.captured_queries.append(
                {
                    "payloads": payloads,
                    "extra_body": self._stub_extra_body(),
                    "tools": tools,
                    "kwargs": kwargs,
                }
            )
            return SimpleNamespace(content=[], id="stub", usage=None)

        async def _query_stream(self, payloads, tools, **kwargs):
            self._apply_thinking_config(payloads)
            self.captured_queries.append(
                {
                    "payloads": payloads,
                    "extra_body": self._stub_extra_body(),
                    "tools": tools,
                    "kwargs": kwargs,
                }
            )
            yield "chunk"

        async def terminate(self):
            await self.client.kwargs["http_client"].aclose()

        async def text_chat(self, *args, **kwargs):
            behavior = PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"]
            if behavior is None:
                raise NotImplementedError
            return await behavior(*args, **kwargs)

        async def text_chat_stream(self, *args, **kwargs):
            behavior = PROVIDER_ANTHROPIC_BEHAVIOR["text_chat_stream"]
            if behavior is None:
                raise NotImplementedError
                yield  # pragma: no cover
            async for chunk in behavior(*args, **kwargs):
                yield chunk

    return ProviderAnthropicStub


@contextmanager
def provider_stubs():
    """补齐 llm_provider 依赖的 astrbot/anthropic 模块桩。"""
    entities = ModuleType("astrbot.core.provider.entities")
    entities.ProviderMeta = ProviderMetaStub
    entities.ProviderType = ProviderTypeStub
    sources_pkg = ModuleType("astrbot.core.provider.sources")
    anthropic_source = ModuleType("astrbot.core.provider.sources.anthropic_source")
    anthropic_source.ProviderAnthropic = make_provider_anthropic_stub()
    register_mod = ModuleType("astrbot.core.provider.register")
    register_mod.provider_cls_map = {}
    register_mod.provider_registry = []

    def _fake_register_adapter(
        type_name,
        desc,
        provider_type=None,
        default_config_tmpl=None,
        provider_display_name=None,
    ):
        def decorator(cls):
            if default_config_tmpl is not None:
                default_config_tmpl.setdefault("type", type_name)
            meta = SimpleNamespace(
                cls_type=cls,
                type=type_name,
                desc=desc,
                default_config_tmpl=default_config_tmpl,
                provider_display_name=provider_display_name,
            )
            register_mod.provider_registry.append(meta)
            register_mod.provider_cls_map[type_name] = meta
            return cls

        return decorator

    register_mod.register_provider_adapter = _fake_register_adapter
    anthropic_mod = ModuleType("anthropic")
    anthropic_mod.AsyncAnthropic = AsyncAnthropicStub
    anthropic_mod.AuthenticationError = AuthenticationErrorStub
    anthropic_base_client = ModuleType("anthropic._base_client")
    anthropic_base_client.httpx = httpx
    anthropic_mod._base_client = anthropic_base_client
    names = (
        "astrbot",
        "astrbot.core",
        "astrbot.core.provider",
    )
    modules = {name: ModuleType(name) for name in names}
    modules.update(
        {
            "astrbot.core.provider.entities": entities,
            "astrbot.core.provider.sources": sources_pkg,
            "astrbot.core.provider.sources.anthropic_source": anthropic_source,
            "astrbot.core.provider.register": register_mod,
            "anthropic": anthropic_mod,
            "anthropic._base_client": anthropic_base_client,
        }
    )
    saved_behavior = dict(PROVIDER_ANTHROPIC_BEHAVIOR)
    try:
        with patch.dict(sys.modules, modules):
            sys.modules.pop("astrbot_plugin_mmx_cli_tool.mmx.llm_provider", None)
            import astrbot_plugin_mmx_cli_tool.mmx.llm_provider as llm_provider

            yield llm_provider, anthropic_source.ProviderAnthropic
    finally:
        PROVIDER_ANTHROPIC_BEHAVIOR.clear()
        PROVIDER_ANTHROPIC_BEHAVIOR.update(saved_behavior)
        sys.modules.pop("astrbot_plugin_mmx_cli_tool.mmx.llm_provider", None)


class StableSessionIdTests(unittest.TestCase):
    def test_matches_independent_construction(self):
        with provider_stubs() as (llm_provider, _):
            seed = "astrbot-mmx\0login-1\0conv-1"
            raw = bytearray(hashlib.sha256(seed.encode()).digest()[:16])
            raw[6] = (raw[6] & 0x0F) | 0x40
            raw[8] = (raw[8] & 0x3F) | 0x80
            expected = "mvs_" + raw.hex()
            actual = llm_provider.stable_session_id("login-1", "conv-1")
            self.assertEqual(actual, expected)
            self.assertEqual(
                actual, llm_provider.stable_session_id("login-1", "conv-1")
            )
            self.assertNotEqual(
                actual, llm_provider.stable_session_id("login-1", "conv-2")
            )
            self.assertNotEqual(
                actual, llm_provider.stable_session_id("login-2", "conv-1")
            )
            self.assertRegex(actual, r"^mvs_[0-9a-f]{32}$")


class ProviderBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stubs = provider_stubs()
        self.llm_provider, self.base_cls = self.enterContext(stubs)
        self.provider = self.llm_provider.MiniMaxCodeProvider(
            region="cn",
            token_getter=lambda: "tok-1",
            credentials_getter=lambda: ("tok-1", "login-1"),
        )

    async def test_provider_construction_headers_and_meta(self):
        # 构造与区域：固定 api_base、auth_token 走 Bearer、默认模型
        self.assertEqual(
            self.provider.base_url, "https://agent.minimax.cn/mavis/api/v1/llm"
        )
        self.assertFalse(self.provider.used_api_key)
        self.assertEqual(self.provider.client.kwargs["auth_token"], "tok-1")
        self.assertEqual(
            self.provider.client.kwargs["base_url"], self.provider.base_url
        )
        self.assertEqual(self.provider.client.kwargs["default_headers"], {})
        hooks = self.provider.client.kwargs["http_client"].event_hooks["request"]
        self.assertEqual(len(hooks), 1)
        self.assertEqual(self.provider.get_model(), "MiniMax-M3.1-Flash")
        global_provider = self.llm_provider.MiniMaxCodeProvider(
            region="global", token_getter=lambda: "t"
        )
        self.assertEqual(
            global_provider.base_url, "https://agent.minimax.io/mavis/api/v1/llm"
        )

        # 每请求注入 Mavis 头与当前 Bearer
        headers = self.provider.resolve_request_headers(
            conversation_id="conv-meta",
            umo=None,
            request_id="req-1",
            model="MiniMax-M3",
            default_headers=None,
        )
        self.assertEqual(headers["Authorization"], "Bearer tok-1")
        self.assertEqual(headers["User-Agent"], "MiniMaxAgent")
        self.assertEqual(headers["X-Mavis-Agent-Id"], "main")
        self.assertTrue(re.fullmatch(r"-?\d+", headers["X-Mavis-Timezone-Offset"]))
        self.assertEqual(
            headers["X-Mavis-Session-Id"],
            self.llm_provider.stable_session_id("login-1", "conv-meta"),
        )

        # meta/密钥与默认模型列表
        meta = self.provider.meta()
        self.assertEqual(meta.id, "mmx_minimax_code")
        self.assertEqual(meta.provider_type, "chat_completion")
        self.assertEqual(self.provider.get_current_key(), "tok-1")
        self.provider.set_key("other")  # OAuth 令牌不可外部改写
        self.assertEqual(self.provider.get_current_key(), "tok-1")
        self.assertEqual(
            await self.provider.get_models(), self.llm_provider.DEFAULT_MODELS
        )

    @staticmethod
    def _outgoing_request(conversation_id="conv-1", *, stream=False):
        headers = {
            "User-Agent": "astrbot/4.29.0",
            "Authorization": "Bearer stale",
            "X-Api-Key": "stale-key",
            "X-Stainless-Lang": "python",
            "X-Custom-Unknown": "drop",
            "anthropic-beta": "unsupported",
            "Cookie": "drop",
        }
        if conversation_id is not None:
            headers["x-astrbot-conversation-id"] = conversation_id
            headers["x-astrbot-request-id"] = "drop"
        return httpx.Request(
            "POST",
            "https://agent.minimax.cn/mavis/api/v1/llm/v1/messages",
            headers=headers,
            json={"messages": [{"role": "user", "content": "hi"}], "stream": stream},
        )

    async def test_same_instance_follows_region_switch(self):
        """同一实例随登录区域切换，下一次请求即改写目标主机，无需重载。"""
        region = {"value": "cn"}
        provider = self.llm_provider.MiniMaxCodeProvider(
            token_getter=lambda: "tok-1",
            credentials_getter=lambda: ("tok-1", "login-1"),
            region_getter=lambda: region["value"],
        )
        for value, host in (
            ("cn", "agent.minimax.cn"),
            ("global", "agent.minimax.io"),
            ("", "agent.minimax.cn"),  # 未登录：兜底 cn
            ("bogus", "agent.minimax.cn"),  # 非法区域：兜底 cn
        ):
            region["value"] = value
            request = self._outgoing_request()
            await provider._rewrite_outgoing_headers(request)
            self.assertEqual(request.url.host, host, value)
            self.assertEqual(request.url.scheme, "https")
            self.assertEqual(request.headers["Host"], host)
            self.assertEqual(request.url.path, "/mavis/api/v1/llm/v1/messages")

    async def test_invalid_fallback_region_normalized(self):
        provider = self.llm_provider.MiniMaxCodeProvider(
            region="weird",
            token_getter=lambda: "tok-1",
            credentials_getter=lambda: ("tok-1", "login-1"),
            region_getter=lambda: "",
        )
        self.assertEqual(provider._region, "cn")
        request = self._outgoing_request()
        await provider._rewrite_outgoing_headers(request)
        self.assertEqual(request.url.host, "agent.minimax.cn")

    async def test_region_rewrite_reaches_transport(self):
        """改写后的目标主机必须真正到达传输层。"""
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"ok": True}, request=request)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http_client.aclose)
        with patch.object(
            self.base_cls, "_create_http_client", return_value=http_client
        ):
            provider = self.llm_provider.MiniMaxCodeProvider(
                token_getter=lambda: "tok-1",
                credentials_getter=lambda: ("tok-1", "login-1"),
                region_getter=lambda: "global",
            )
        await provider.client.kwargs["http_client"].send(self._outgoing_request())
        self.assertEqual(captured[0].url.host, "agent.minimax.io")
        self.assertEqual(captured[0].headers["Host"], "agent.minimax.io")
        self.assertEqual(captured[0].url.path, "/mavis/api/v1/llm/v1/messages")

    async def test_get_models_uses_login_region(self):
        region = {"value": "global"}
        provider = self.llm_provider.MiniMaxCodeProvider(
            token_getter=lambda: "tok-1",
            region_getter=lambda: region["value"],
        )
        calls: list[str] = []

        async def fake_fetch(value, transport=None):
            calls.append(value)
            return [f"model-{value}"]

        self.addCleanup(self.llm_provider.clear_token_source)
        with patch.object(self.llm_provider, "fetch_available_models", fake_fetch):
            self.assertEqual(await provider.get_models(), ["model-global"])
            self.assertEqual(await provider.get_models(), ["model-global"])  # 命中缓存
            region["value"] = "cn"
            self.assertEqual(await provider.get_models(), ["model-cn"])
        self.assertEqual(calls, ["global", "cn"])
        self.assertEqual(self.llm_provider._models_cache_region, "cn")

    async def test_outgoing_headers_are_whitelisted_per_conversation(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200, json={"ok": True}, request=request)

        http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(http_client.aclose)
        with patch.object(
            self.base_cls, "_create_http_client", return_value=http_client
        ):
            provider = self.llm_provider.MiniMaxCodeProvider(
                region="cn",
                token_getter=lambda: "tok-1",
                credentials_getter=lambda: ("tok-1", "login-1"),
            )
        configured_client = provider.client.kwargs["http_client"]
        self.assertIs(configured_client, http_client)
        for outgoing in (
            self._outgoing_request(),
            self._outgoing_request(),
            self._outgoing_request("conv-2"),
        ):
            await configured_client.send(outgoing)

        self.assertEqual(len(captured), 3)
        request, same, other = captured
        expected_session = self.llm_provider.stable_session_id("login-1", "conv-1")
        self.assertEqual(request.headers["X-Mavis-Session-Id"], expected_session)
        self.assertEqual(request.headers["Authorization"], "Bearer tok-1")
        self.assertEqual(request.headers["User-Agent"], "MiniMaxAgent")
        self.assertEqual(request.headers["Accept"], "application/json")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        allowed = {
            "accept-encoding",
            "connection",
            "content-length",
            "host",
            "authorization",
            "user-agent",
            "x-mavis-session-id",
            "x-mavis-agent-id",
            "x-mavis-timezone-offset",
            "anthropic-version",
            "content-type",
            "accept",
        }
        self.assertLessEqual(set(request.headers.keys()), allowed)
        self.assertNotIn("x-astrbot-conversation-id", request.headers)
        self.assertNotIn("x-stainless-lang", request.headers)
        self.assertNotIn("x-custom-unknown", request.headers)
        self.assertEqual(same.headers["X-Mavis-Session-Id"], expected_session)
        self.assertNotEqual(other.headers["X-Mavis-Session-Id"], expected_session)

    async def test_outgoing_stream_without_conversation(self):
        request = self._outgoing_request(None, stream=True)
        await self.provider._rewrite_outgoing_headers(request)
        self.assertEqual(request.headers["Accept"], "text/event-stream")
        self.assertRegex(request.headers["X-Mavis-Session-Id"], r"^mvs_[0-9a-f]{32}$")

    async def test_positional_session_id_is_mapped_for_text_and_stream(self):
        sessions = []

        async def capture(outgoing):
            del outgoing.headers["x-astrbot-conversation-id"]
            await self.provider._rewrite_outgoing_headers(outgoing)
            sessions.append(outgoing.headers["X-Mavis-Session-Id"])

        async def text_behavior(*args, **kwargs):
            await capture(self._outgoing_request())
            return "ok"

        async def stream_behavior(*args, **kwargs):
            await capture(self._outgoing_request())
            yield "chunk"

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"] = text_behavior
        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat_stream"] = stream_behavior
        self.assertEqual(await self.provider.text_chat("hi", "conv-positional"), "ok")
        chunks = [
            chunk
            async for chunk in self.provider.text_chat_stream(
                "hi", "conv-stream-positional"
            )
        ]
        self.assertEqual(chunks, ["chunk"])
        self.assertEqual(
            sessions,
            [
                self.llm_provider.stable_session_id("login-1", "conv-positional"),
                self.llm_provider.stable_session_id(
                    "login-1", "conv-stream-positional"
                ),
            ],
        )

    async def test_no_conversation_requests_are_isolated_and_retry_stable(self):
        captured = []

        async def behavior(*args, **kwargs):
            request = self._outgoing_request(None)
            await self.provider._rewrite_outgoing_headers(request)
            captured.append(request)
            if len(captured) == 1:
                raise AuthenticationErrorStub("401")
            return "ok"

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"] = behavior
        self.provider._auth_refresher = AsyncMock()
        self.assertEqual(await self.provider.text_chat(prompt="hi"), "ok")
        self.assertEqual(await self.provider.text_chat(prompt="hi"), "ok")
        self.assertEqual(len(captured), 3)
        first, retry, independent = captured
        self.assertEqual(
            retry.headers["X-Mavis-Session-Id"],
            first.headers["X-Mavis-Session-Id"],
        )
        self.assertNotEqual(
            independent.headers["X-Mavis-Session-Id"],
            first.headers["X-Mavis-Session-Id"],
        )

    async def test_token_refresh_preserves_conversation_session(self):
        state = {"token": "tok-1"}
        provider = self.llm_provider.MiniMaxCodeProvider(
            region="cn",
            token_getter=lambda: state["token"],
            credentials_getter=lambda: (state["token"], "login-1"),
        )
        first = self._outgoing_request()
        await provider._rewrite_outgoing_headers(first)
        state["token"] = "tok-2"
        second = self._outgoing_request()
        await provider._rewrite_outgoing_headers(second)
        self.assertEqual(second.headers["Authorization"], "Bearer tok-2")
        self.assertEqual(
            second.headers["X-Mavis-Session-Id"],
            first.headers["X-Mavis-Session-Id"],
        )
        await provider.client.kwargs["http_client"].aclose()

    async def test_account_change_blocks_outgoing_request(self):
        from astrbot_plugin_mmx_cli_tool.mmx.errors import MiniMaxError

        state = {"token": "tok-1", "epoch": "login-1"}
        provider = self.llm_provider.MiniMaxCodeProvider(
            region="cn",
            token_getter=lambda: state["token"],
            credentials_getter=lambda: (state["token"], state["epoch"]),
        )

        async def behavior(*args, **kwargs):
            state["epoch"] = "login-2"
            await provider._rewrite_outgoing_headers(self._outgoing_request())
            return "unexpected"

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"] = behavior
        with self.assertRaises(MiniMaxError):
            await provider.text_chat(prompt="hi")
        await provider.client.kwargs["http_client"].aclose()

    async def test_auth_retry_does_not_cross_login(self):
        state = {"calls": 0, "refreshes": 0, "epoch": "login-1"}
        provider = self.llm_provider.MiniMaxCodeProvider(
            region="cn",
            token_getter=lambda: "tok-1",
            credentials_getter=lambda: ("tok-1", state["epoch"]),
        )

        async def behavior(*args, **kwargs):
            state["calls"] += 1
            raise AuthenticationErrorStub("401")

        async def refresher():
            state["refreshes"] += 1
            state["epoch"] = "login-2"

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"] = behavior
        provider._auth_refresher = refresher
        with self.assertRaises(AuthenticationErrorStub):
            await provider.text_chat(prompt="hi")
        self.assertEqual(state["calls"], 1)
        self.assertEqual(state["refreshes"], 1)
        await provider.client.kwargs["http_client"].aclose()

    async def test_outgoing_uses_conversation_kwargs_when_header_absent(self):
        captured = []

        async def behavior(*args, **kwargs):
            request = self._outgoing_request()
            del request.headers["x-astrbot-conversation-id"]
            await self.provider._rewrite_outgoing_headers(request)
            captured.append(request)
            return "ok"

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"] = behavior
        result = await self.provider.text_chat(prompt="hi", conversation_id="conv-kwargs")
        self.assertEqual(result, "ok")
        self.assertEqual(
            captured[0].headers["X-Mavis-Session-Id"],
            self.llm_provider.stable_session_id("login-1", "conv-kwargs"),
        )

    async def test_fetch_available_models_parses_catalog(self):
        import httpx

        with provider_stubs() as (llm_provider, _):
            captured = []

            def handler(request: httpx.Request) -> httpx.Response:
                captured.append(request)
                return httpx.Response(
                    200,
                    json={
                        "version": "v1",
                        "ttlSeconds": 300,
                        "providers": [
                            {"providerId": "other", "config": {"models": {"X": {}}}},
                            {
                                "providerId": "minimax",
                                "config": {
                                    "models": {
                                        "MiniMax-M3": {"limit": {"context": 512000}},
                                        "MiniMax-M3.1-Flash": {},
                                    },
                                    "model_order": ["MiniMax-M3.1-Flash", "MiniMax-M3"],
                                },
                            },
                        ],
                    },
                )

            models = await llm_provider.fetch_available_models(
                "cn", transport=httpx.MockTransport(handler)
            )
            self.assertEqual(models, ["MiniMax-M3.1-Flash", "MiniMax-M3"])
            self.assertIn("region=cn", str(captured[0].url))
            self.assertIn("buildEnv=prod", str(captured[0].url))

            def fail(request: httpx.Request) -> httpx.Response:
                return httpx.Response(500)

            self.assertEqual(
                await llm_provider.fetch_available_models(
                    "cn", transport=httpx.MockTransport(fail)
                ),
                [],
            )

    async def test_text_chat_retries_once_on_auth_error(self):
        state = {"calls": 0, "refreshed": False}

        async def behavior(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise AuthenticationErrorStub("401")
            return "ok"

        async def refresher():
            state["refreshed"] = True

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat"] = behavior
        self.provider._auth_refresher = refresher
        result = await self.provider.text_chat(prompt="hi")
        self.assertEqual(result, "ok")
        self.assertTrue(state["refreshed"])
        self.assertEqual(state["calls"], 2)

    async def test_text_chat_stream_retries_before_first_chunk(self):
        state = {"calls": 0}

        async def behavior(*args, **kwargs):
            state["calls"] += 1
            if state["calls"] == 1:
                raise AuthenticationErrorStub("401")
                yield  # pragma: no cover
            yield "a"
            yield "b"

        async def refresher():
            return None

        PROVIDER_ANTHROPIC_BEHAVIOR["text_chat_stream"] = behavior
        self.provider._auth_refresher = refresher
        chunks = [chunk async for chunk in self.provider.text_chat_stream(prompt="hi")]
        self.assertEqual(chunks, ["a", "b"])
        self.assertEqual(state["calls"], 2)


class PayloadPreparationTests(unittest.IsolatedAsyncioTestCase):
    """最终 SDK 请求体回归：M3.1 effort 档位与 M3 thinking 开关。"""

    async def asyncSetUp(self):
        stubs = provider_stubs()
        self.llm_provider, self.base_cls = self.enterContext(stubs)
        self.effort_holder = {"value": "default"}
        self.llm_provider.set_token_source(
            lambda: "tok-1",
            effort_getter=lambda: self.effort_holder["value"],
        )
        self.addCleanup(self.llm_provider.clear_token_source)
        self.provider = self.llm_provider.MiniMaxCodeProvider(
            region="cn",
            token_getter=lambda: "tok-1",
            credentials_getter=lambda: ("tok-1", "login-1"),
        )

    def _config_provider(self, provider_config):
        return self.llm_provider.MiniMaxCodeProvider(
            region="cn",
            provider_config=provider_config,
            token_getter=lambda: "tok-1",
            credentials_getter=lambda: ("tok-1", "login-1"),
        )

    async def _captured_final(self, provider=None, *, stream=False, payloads=None):
        provider = provider or self.provider
        payloads = payloads if payloads is not None else {
            "messages": [{"role": "user", "content": "hi"}],
            "model": provider.get_model(),
        }
        if stream:
            chunks = [chunk async for chunk in provider._query_stream(payloads, None)]
            self.assertTrue(chunks)
        else:
            await provider._query(payloads, None)
        captured = provider.captured_queries[-1]
        return {**captured["payloads"], **captured["extra_body"]}, captured

    async def test_m31_default_sends_effort_without_thinking(self):
        final, _ = await self._captured_final()
        self.assertEqual(final["output_config"], {"effort": "default"})
        self.assertNotIn("thinking", final)
        self.assertNotIn("reasoning_effort", final)
        final_stream, _ = await self._captured_final(stream=True)
        self.assertEqual(final_stream["output_config"], {"effort": "default"})
        self.assertNotIn("thinking", final_stream)

    async def test_workbench_effort_applies_immediately_per_request(self):
        for level in ("low", "medium", "high", "xhigh", "max"):
            self.effort_holder["value"] = level
            final, _ = await self._captured_final()
            self.assertEqual(final["output_config"], {"effort": level})
        # 运行中改档：下一个请求读取新值
        self.effort_holder["value"] = "low"
        final, _ = await self._captured_final(stream=True)
        self.assertEqual(final["output_config"], {"effort": "low"})

    async def test_legacy_reasoning_effort_alias_maps_to_output_config(self):
        provider = self._config_provider(
            {"id": "legacy", "custom_extra_body": {"reasoning_effort": "max"}}
        )
        final, captured = await self._captured_final(provider)
        self.assertEqual(final["output_config"], {"effort": "max"})
        self.assertNotIn("reasoning_effort", captured["extra_body"])
        self.assertNotIn("reasoning_effort", final)

        # 无效别名不透传：回退工作台档位
        provider = self._config_provider(
            {"id": "legacy-invalid", "custom_extra_body": {"reasoning_effort": "ultra"}}
        )
        final, captured = await self._captured_final(provider)
        self.assertEqual(final["output_config"], {"effort": "default"})
        self.assertNotIn("reasoning_effort", captured["extra_body"])

    async def test_explicit_output_config_wins_and_other_fields_pass_through(self):
        provider = self._config_provider(
            {
                "id": "explicit",
                "custom_extra_body": {
                    "output_config": {"effort": "xhigh"},
                    "reasoning_effort": "low",
                    "safety": "keep-me",
                },
            }
        )
        final, captured = await self._captured_final(provider)
        self.assertEqual(final["output_config"], {"effort": "xhigh"})
        self.assertEqual(captured["extra_body"].get("safety"), "keep-me")

        # 显式 output_config 缺 effort：按 旧别名 > 工作台 解析并合入，保留其余键
        provider = self._config_provider(
            {
                "id": "explicit-partial",
                "custom_extra_body": {
                    "output_config": {"keep": 1},
                    "reasoning_effort": "medium",
                },
            }
        )
        final, _ = await self._captured_final(provider)
        self.assertEqual(final["output_config"], {"keep": 1, "effort": "medium"})

    async def test_m3_default_adaptive_and_workbench_effort_ignored(self):
        provider = self._config_provider(
            {"id": "m3", "model": "MiniMax-M3", "custom_extra_body": {}}
        )
        self.effort_holder["value"] = "max"
        final, _ = await self._captured_final(provider)
        self.assertEqual(final["thinking"], {"type": "adaptive"})
        self.assertNotIn("output_config", final)

    async def test_m3_explicit_disabled_is_kept(self):
        provider = self._config_provider(
            {
                "id": "m3-off",
                "model": "MiniMax-M3",
                "custom_extra_body": {"thinking": {"type": "disabled"}},
            }
        )
        final, captured = await self._captured_final(provider)
        self.assertEqual(final["thinking"], {"type": "disabled"})
        self.assertNotIn("output_config", final)
        self.assertNotIn("thinking", captured["extra_body"])

    async def test_custom_model_extra_body_passes_through(self):
        """自定义模型不弹字段：三字段与其他字段原样到达最终合并参数（普通与流式）。"""
        provider = self._config_provider(
            {
                "id": "custom",
                "model": "MiniMax-M3-Lite",
                "custom_extra_body": {
                    "reasoning_effort": "high",
                    "output_config": {"effort": "low"},
                    "thinking": {"type": "disabled"},
                    "safety": "keep-me",
                },
            }
        )
        final, captured = await self._captured_final(provider)
        self.assertEqual(final["reasoning_effort"], "high")
        self.assertEqual(final["output_config"], {"effort": "low"})
        self.assertEqual(final["thinking"], {"type": "disabled"})
        self.assertEqual(final["safety"], "keep-me")
        self.assertEqual(captured["extra_body"]["safety"], "keep-me")

        final_stream, _ = await self._captured_final(provider, stream=True)
        self.assertEqual(final_stream["reasoning_effort"], "high")
        self.assertEqual(final_stream["output_config"], {"effort": "low"})
        self.assertEqual(final_stream["thinking"], {"type": "disabled"})
        self.assertEqual(final_stream["safety"], "keep-me")

    async def test_explicit_anth_thinking_config_keeps_native_behavior(self):
        provider = self._config_provider(
            {
                "id": "native-thinking",
                "anth_thinking_config": {"type": "adaptive", "effort": "high"},
            }
        )
        self.effort_holder["value"] = "xhigh"
        final, _ = await self._captured_final(provider)
        # AstrBot 原生行为保持：档位来自 thinking_config，工作台不参与
        self.assertEqual(final["thinking"], {"type": "adaptive"})
        self.assertEqual(final["output_config"], {"effort": "high"})

    async def test_generic_payload_fields_are_untouched(self):
        payloads = {
            "messages": [{"role": "user", "content": "hi"}],
            "model": "MiniMax-M3.1-Flash",
            "max_tokens": 1024,
            "metadata": {"user": "u1"},
            "temperature": 0.3,
        }
        tools = object()  # 工具集由 AstrBot 原生处理，仅验证透传
        await self.provider._query(payloads, tools)
        captured = self.provider.captured_queries[-1]
        self.assertIs(captured["tools"], tools)
        merged = {**captured["payloads"], **captured["extra_body"]}
        self.assertEqual(merged["max_tokens"], 1024)
        self.assertEqual(merged["metadata"], {"user": "u1"})
        self.assertEqual(merged["temperature"], 0.3)
        self.assertEqual(merged["output_config"], {"effort": "default"})

    async def test_concurrent_requests_keep_own_effort(self):
        import asyncio

        counter = {"n": 0}

        def alternating():
            counter["n"] += 1
            return "low" if counter["n"] % 2 else "high"

        self.llm_provider.set_token_source(
            lambda: "tok-1", effort_getter=alternating
        )
        peers = [
            self.llm_provider.MiniMaxCodeProvider(
                region="cn",
                token_getter=lambda: "tok-1",
                credentials_getter=lambda: ("tok-1", "login-1"),
            )
            for _ in range(2)
        ]
        finals = await asyncio.gather(*(self._captured_final(p) for p in peers))
        efforts = sorted(item[0]["output_config"]["effort"] for item in finals)
        self.assertEqual(efforts, ["high", "low"])


class AdapterRegistrationTests(unittest.TestCase):
    def test_register_idempotent_and_unregister(self):
        with provider_stubs() as (llm_provider, _):
            import astrbot.core.provider.register as reg

            self.assertTrue(llm_provider.register_provider_adapter_type())
            self.assertIn("minimax_code", reg.provider_cls_map)
            meta = reg.provider_cls_map["minimax_code"]
            self.assertIs(meta.cls_type, llm_provider.MiniMaxCodeProvider)
            self.assertEqual(meta.default_config_tmpl["type"], "minimax_code")
            # 幂等：重复注册不叠加
            self.assertTrue(llm_provider.register_provider_adapter_type())
            self.assertEqual(len(reg.provider_registry), 1)
            # 注销后类型与注册表清空
            llm_provider.unregister_provider_adapter_type()
            self.assertNotIn("minimax_code", reg.provider_cls_map)
            self.assertEqual(reg.provider_registry, [])

    def test_config_mode_construction(self):
        with provider_stubs() as (llm_provider, _):
            llm_provider.set_token_source(
                lambda: "cfg-tok",
                credentials_getter=lambda: ("cfg-tok", "cfg-login"),
                region_getter=lambda: "global",
            )
            provider = llm_provider.MiniMaxCodeProvider(
                {
                    "id": "my_mmcode",
                    "model": "MiniMax-M3.1-Flash",
                    "region": "cn",  # 旧配置残留字段：被忽略
                    "timeout": 60,
                }
            )
            self.assertEqual(provider.meta().id, "my_mmcode")
            request = httpx.Request(
                "POST",
                "https://agent.minimax.cn/mavis/api/v1/llm/v1/messages",
                json={"messages": []},
            )
            import asyncio

            asyncio.run(provider._rewrite_outgoing_headers(request))
            self.assertEqual(request.url.host, "agent.minimax.io")
            self.assertEqual(request.headers["Host"], "agent.minimax.io")
            self.assertEqual(request.url.path, "/mavis/api/v1/llm/v1/messages")
            self.assertEqual(provider.get_current_key(), "cfg-tok")
            self.assertEqual(
                provider.resolve_request_headers()["Authorization"], "Bearer cfg-tok"
            )
            llm_provider.clear_token_source()
            self.assertEqual(provider.get_current_key(), "")
            self.assertIsNone(llm_provider._module_region_getter)

    def test_template_has_no_region(self):
        with provider_stubs() as (llm_provider, _):
            import astrbot.core.provider.register as reg

            llm_provider.register_provider_adapter_type()
            tmpl = reg.provider_cls_map["minimax_code"].default_config_tmpl
            self.assertNotIn("region", tmpl)
            self.assertEqual(tmpl["id"], "mmx_minimax_code")
            self.assertEqual(tmpl["type"], "minimax_code")
            self.assertEqual(tmpl["provider_type"], "chat_completion")


class FakeAstrBotConfig(dict):
    """AstrBotConfig 桩：记录 save_config_async 调用次数。"""

    def __init__(self) -> None:
        super().__init__()
        self.saved = 0

    async def save_config_async(self, replace_config=None, *, indent: int = 2) -> bool:
        self.saved += 1
        return True


def make_fake_context(provider_insts=None, *, provider_module=None):
    """构造带配置与供应商管理器桩的插件上下文。

    传入 provider_module 时，load_provider/terminate_provider 模拟 AstrBot 真实行为：
    合并提供商源配置、构造当前模块实例并维护 insts 与 inst_map。
    """
    from unittest.mock import AsyncMock

    manager = SimpleNamespace(
        provider_insts=provider_insts if provider_insts is not None else [],
        inst_map={},
        provider_sources_config=[],
        load_provider=AsyncMock(),
        terminate_provider=AsyncMock(),
    )

    if provider_module is not None:

        async def load_provider(provider_config):
            merged = dict(provider_config)
            for source in manager.provider_sources_config:
                if isinstance(source, dict) and source.get("id") == merged.get(
                    "provider_source_id"
                ):
                    merged = {**source, **merged}
                    break
            merged["id"] = provider_config["id"]
            if not merged.get("enable", True):
                return
            inst = provider_module.MiniMaxCodeProvider(merged)
            manager.provider_insts.append(inst)
            manager.inst_map[merged["id"]] = inst

        async def terminate_provider(provider_id):
            inst = manager.inst_map.pop(provider_id, None)
            if inst is None:
                return
            if inst in manager.provider_insts:
                manager.provider_insts.remove(inst)
            terminate = getattr(inst, "terminate", None)
            if terminate is not None:
                await terminate()

        manager.load_provider = load_provider
        manager.terminate_provider = terminate_provider

    config = FakeAstrBotConfig()
    config["provider"] = []
    # 与真实 AstrBot 相同：管理器与配置持有同一个列表引用
    config["provider_sources"] = manager.provider_sources_config
    context = SimpleNamespace(
        add_llm_tools=lambda *a, **k: None,
        register_web_api=lambda *a, **k: None,
        provider_manager=manager,
        get_config=lambda: config,
    )
    return context, config, manager



class PluginRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_and_unregister_lifecycle(self):
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, config, manager = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    # 未登录时不注册
                    self.assertFalse(await plugin._register_llm_provider())
                    self.assertEqual(manager.provider_insts, [])

                    # 登录后注册进 insts 与 inst_map
                    from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

                    plugin._token_store.save(
                        OAuthToken(
                            access_token="a",
                            refresh_token="r",
                            expires_at=9e9,
                            region="cn",
                        )
                    )
                    self.assertTrue(await plugin._register_llm_provider())
                    self.assertEqual(len(manager.provider_insts), 1)
                    epoch = plugin._token_store.load().login_epoch
                    self.assertRegex(epoch, r"^[0-9a-f]{32}$")
                    self.assertEqual(plugin._provider_credentials(), ("a", epoch))
                    self.assertEqual(plugin._llm_provider._credentials(), ("a", epoch))
                    self.assertIs(
                        manager.inst_map["mmx_minimax_code"], plugin._llm_provider
                    )

                    # 幂等：重复注册不叠加
                    self.assertTrue(await plugin._register_llm_provider())
                    self.assertEqual(len(manager.provider_insts), 1)

                    # 注销（terminate 也会走同一路径）
                    await plugin._unregister_llm_provider()
                    self.assertEqual(manager.provider_insts, [])
                    self.assertNotIn("mmx_minimax_code", manager.inst_map)

    async def test_runtime_provider_switches_account_and_region(self):
        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context()
                    config["provider_sources"].append(
                        {
                            "id": "mmx_minimax_code",
                            "api_base": "http://foreign",
                            "type": "openai_chat_completion",
                        }
                    )
                    plugin = main.Main(context, {"api_key": ["k"]})
                    from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

                    plugin._token_store.save(
                        OAuthToken("a", "r", 9e9, region="cn", login_epoch="login-cn")
                    )
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)
                    first = plugin._llm_provider
                    first.terminate = AsyncMock()

                    plugin._token_store.save(
                        OAuthToken(
                            "a2", "r2", 9e9, region="cn", login_epoch="login-cn"
                        )
                    )
                    self.assertTrue(await plugin._register_llm_provider())
                    self.assertIs(plugin._llm_provider, first)

                    plugin._token_store.save(
                        OAuthToken(
                            "b", "r3", 9e9, region="global", login_epoch="login-global"
                        )
                    )
                    self.assertTrue(await plugin._register_llm_provider())
                    second = plugin._llm_provider
                    self.assertIsNot(second, first)
                    self.assertEqual(second._region, "global")
                    self.assertEqual(
                        second.base_url, "https://agent.minimax.io/mavis/api/v1/llm"
                    )
                    self.assertEqual(manager.provider_insts, [second])
                    self.assertIs(manager.inst_map["mmx_minimax_code"], second)
                    first.terminate.assert_awaited_once()

    async def test_initialize_migrates_legacy_login_epoch(self):
        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, _, _ = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

                    plugin._token_store.save(OAuthToken("a", "r", expires_at=9e9))
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)
                    token, epoch = plugin._provider_credentials()
                    self.assertEqual(token, "a")
                    self.assertRegex(epoch, r"^[0-9a-f]{32}$")
                    self.assertEqual(plugin._provider_credentials()[1], epoch)
                    self.assertEqual(plugin._token_store.load().login_epoch, epoch)

    async def test_auto_signin_claims_once_per_day(self):
        from unittest.mock import AsyncMock

        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, _, _ = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

                    plugin._token_store.save(
                        OAuthToken(
                            access_token="a",
                            refresh_token="r",
                            expires_at=9e9,
                            region="cn",
                        )
                    )
                    plugin._account.signin_claim = AsyncMock(
                        return_value={"day_no": 2, "points": 10}
                    )
                    await plugin._auto_signin_once()
                    await plugin._auto_signin_once()  # 同一天只调用一次
                    plugin._account.signin_claim.assert_awaited_once()

                    # 未登录时不签到
                    plugin._signin_claimed_day = ""
                    plugin._token_store.clear()
                    await plugin._auto_signin_once()
                    plugin._account.signin_claim.assert_awaited_once()


class ProviderSourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_write_and_idempotent(self):
        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, config, manager = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    # 冷启动：写入配置但不即时加载（交给 AstrBot 原生加载）
                    self.assertEqual(config.saved, 1)
                    manager.load_provider.assert_not_awaited()
                    source = config["provider_sources"][0]
                    self.assertEqual(source["id"], "mmx_minimax_code")
                    self.assertEqual(source["type"], "minimax_code")
                    self.assertIn("OAuth", source["key"][0])
                    self.assertNotEqual(source["api_base"], "")
                    self.assertEqual(
                        source["managed_by"], "astrbot_plugin_mmx_cli_tool"
                    )
                    entry_ids = [p["id"] for p in config["provider"]]
                    self.assertEqual(
                        entry_ids,
                        [
                            "mmx_minimax_code/MiniMax-M3.1-Flash",
                            "mmx_minimax_code/MiniMax-M3",
                        ],
                    )
                    for entry in config["provider"]:
                        self.assertEqual(
                            entry["provider_source_id"], "mmx_minimax_code"
                        )
                        self.assertEqual(entry["max_context_tokens"], 512000)
                    self.assertEqual(plugin._provider_source_warning, "")
                    self.assertEqual(plugin._provider_source_state, "managed")
                    self.assertNotIn("region", source)

                    # 幂等：重复执行不追加、不落盘
                    await plugin._ensure_provider_source()
                    self.assertEqual(config.saved, 1)
                    self.assertEqual(len(config["provider_sources"]), 1)
                    self.assertEqual(len(config["provider"]), 2)


    async def test_region_change_does_not_touch_sources_or_reload(self):
        """换区域重新登录后：不改源配置、不落盘、不重载已加载条目。"""
        from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, config, manager = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    # 模拟我们的条目已由原生加载（热环境）
                    for entry in config["provider"]:
                        inst = SimpleNamespace(provider_config=entry)
                        manager.inst_map[entry["id"]] = inst
                        manager.provider_insts.append(inst)

                    plugin._token_store.save(
                        OAuthToken(
                            access_token="a",
                            refresh_token="r",
                            expires_at=9e9,
                            region="global",
                        )
                    )
                    saved_before = config.saved
                    sources_before = [dict(x) for x in config["provider_sources"]]
                    await plugin._ensure_provider_source()
                    self.assertEqual(config.saved, saved_before)
                    self.assertEqual(config["provider_sources"], sources_before)
                    self.assertNotIn("region", config["provider_sources"][0])
                    manager.load_provider.assert_not_awaited()

    async def test_terminate_clears_module_token_source(self):
        """正常提供商源路径（无运行时实例）terminate 也必须清理模块来源。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, _, _ = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.assertIsNone(plugin._llm_provider)
                    llm_provider.set_token_source(lambda: "tok")
                    await plugin.terminate()
                    self.assertEqual(llm_provider._module_token_getter(), "")
                    self.assertIsNone(llm_provider._module_credentials_getter)

    async def test_terminate_cleanup_failure_warns_but_still_unregisters(self):
        """terminate 的令牌清理与适配器注销互不影响，失败有 warning 不静默。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, _, _ = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()

                    with patch.object(
                        llm_provider,
                        "clear_token_source",
                        side_effect=RuntimeError("boom-clear"),
                    ):
                        with self.assertLogs("mmx-test", level="DEBUG") as logs:
                            await plugin.terminate()
                    joined = "\n".join(logs.output)
                    self.assertIn("清理模块级令牌来源失败", joined)
                    self.assertIn("boom-clear", joined)
                    # 清理失败不影响适配器注销
                    self.assertIn("已注销 MiniMax Code 供应商适配器", joined)

    async def test_occupied_foreign_source_skips_with_warning(self):
        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, config, _ = make_fake_context()
                    foreign = {
                        "id": "mmx_minimax_code",
                        "api_base": "http://x",
                        "type": "openai_chat_completion",
                    }
                    config["provider_sources"].append(foreign)
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    self.assertIn("已被其他配置占用", plugin._provider_source_warning)
                    self.assertEqual(config.saved, 0)
                    self.assertEqual(config["provider"], [])
                    self.assertIs(config["provider_sources"][0], foreign)
                    self.assertEqual(plugin._provider_source_state, "conflict")

    async def test_config_read_failure_resets_state(self):
        with (
            provider_stubs(),
            tempfile.TemporaryDirectory() as tmp,
            plugin_modules(Path(tmp)) as (main, _),
        ):
            context, _, _ = make_fake_context()
            plugin = main.Main(context, {"api_key": ["k"]})
            plugin._provider_source_state = "managed"
            plugin._provider_source_warning = "old"

            def boom():
                raise RuntimeError("no config")

            context.get_config = boom
            await plugin._ensure_provider_source()
            self.assertEqual(plugin._provider_source_state, "")
            self.assertEqual(plugin._provider_source_warning, "")

    async def test_user_managed_same_type_source_is_left_alone(self):
        with (
            provider_stubs(),
            tempfile.TemporaryDirectory() as tmp,
            plugin_modules(Path(tmp)) as (main, _),
        ):
            context, config, manager = make_fake_context()
            mine = {
                "id": "mmx_minimax_code",
                "type": "minimax_code",
                "api_base": "http://x",
            }
            config["provider_sources"].append(mine)
            plugin = main.Main(context, {"api_key": ["k"]})
            await plugin.initialize()
            self.addAsyncCleanup(plugin.terminate)

            self.assertEqual(plugin._provider_source_warning, "")
            self.assertEqual(plugin._provider_source_state, "user_managed")
            self.assertEqual(config.saved, 0)
            self.assertEqual(config["provider"], [])
            self.assertIsNone(plugin._llm_provider)
            self.assertNotIn("mmx_minimax_code", manager.inst_map)
            self.assertEqual(
                mine,
                {
                    "id": "mmx_minimax_code",
                    "type": "minimax_code",
                    "api_base": "http://x",
                },
            )

    async def test_switch_off_leaves_config_untouched(self):
        from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

        with (
            provider_stubs() as (llm_provider, _),
            tempfile.TemporaryDirectory() as tmp,
            plugin_modules(Path(tmp)) as (main, _),
        ):
            import astrbot.core.provider.register as reg

            context, config, manager = make_fake_context()
            plugin = main.Main(
                context,
                {"api_key": ["k"], "auto_create_provider_source": False},
            )
            plugin._token_store.save(
                OAuthToken("a", "r", 9e9, region="global", login_epoch="e1")
            )
            await plugin.initialize()
            self.addAsyncCleanup(plugin.terminate)

            self.assertEqual(plugin._provider_source_state, "disabled")
            self.assertEqual(plugin._provider_source_warning, "")
            self.assertEqual(config["provider_sources"], [])
            self.assertEqual(config["provider"], [])
            self.assertEqual(config.saved, 0)
            self.assertEqual(manager.inst_map, {})
            self.assertIsNone(plugin._llm_provider)
            self.assertIn("minimax_code", reg.provider_cls_map)
            self.assertEqual(llm_provider._module_token_getter(), "a")
            self.assertEqual(llm_provider._module_region(), "global")

            # 登录后同步同样不触碰配置、不走回退
            with patch.object(
                plugin, "_ensure_provider_source", AsyncMock()
            ) as ensure:
                await plugin._sync_provider_source()
                ensure.assert_not_awaited()
            self.assertEqual(config.saved, 0)
            self.assertEqual(plugin._provider_source_state, "disabled")

    async def test_switch_off_keeps_existing_managed_source(self):
        with (
            provider_stubs(),
            tempfile.TemporaryDirectory() as tmp,
            plugin_modules(Path(tmp)) as (main, _),
        ):
            context, config, _ = make_fake_context()
            managed = {
                "id": "mmx_minimax_code",
                "type": "minimax_code",
                "managed_by": "astrbot_plugin_mmx_cli_tool",
                "region": "cn",
            }
            config["provider_sources"].append(managed)
            plugin = main.Main(
                context, {"api_key": ["k"], "auto_create_provider_source": False}
            )
            await plugin.initialize()
            self.addAsyncCleanup(plugin.terminate)

            self.assertEqual(config.saved, 0)
            self.assertEqual(config["provider"], [])
            self.assertEqual(managed["region"], "cn")
            self.assertEqual(len(config["provider_sources"]), 1)

    async def test_unregister_runtime_provider_keeps_token_source(self):
        from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

        with (
            provider_stubs() as (llm_provider, _),
            tempfile.TemporaryDirectory() as tmp,
            plugin_modules(Path(tmp)) as (main, _),
        ):
            context, _, _ = make_fake_context()
            plugin = main.Main(context, {"api_key": ["k"]})
            await plugin.initialize()
            self.addAsyncCleanup(plugin.terminate)
            plugin._token_store.save(
                OAuthToken("a", "r", 9e9, region="global", login_epoch="e1")
            )
            self.assertTrue(await plugin._register_llm_provider())
            await plugin._unregister_llm_provider()
            self.assertEqual(llm_provider._module_token_getter(), "a")
            self.assertEqual(llm_provider._module_region(), "global")


class LegacyModuleProviderStub:
    """上一轮插件模块的 Provider 残留替身：同名类型、不同类对象。"""

    def __init__(self, entry):
        self.provider_config = {**entry, "type": "minimax_code"}
        self.terminated = False

    async def terminate(self):
        self.terminated = True


class ProviderReloadRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """插件重载后 MiniMax Code 原生实例的终止与恢复。"""

    def _source(self, source_id="mmx_minimax_code", **extra):
        source = {"id": source_id, "type": "minimax_code", "api_base": "http://x"}
        source.update(extra)
        return source

    def _entry(self, model, source_id="mmx_minimax_code", **extra):
        entry = {
            "id": f"{source_id}/{model}",
            "enable": True,
            "provider_source_id": source_id,
            "model": model,
            "custom_extra_body": {},
        }
        entry.update(extra)
        return entry

    def _place_stale(self, manager, *entries):
        stale = [LegacyModuleProviderStub(entry) for entry in entries]
        for inst in stale:
            manager.provider_insts.append(inst)
            manager.inst_map[inst.provider_config["id"]] = inst
        return stale

    def _save_login(self, plugin, token="a"):
        from astrbot_plugin_mmx_cli_tool.mmx.oauth import OAuthToken

        plugin._token_store.save(
            OAuthToken(token, "r", 9e9, region="cn", login_epoch="login-1")
        )

    async def test_switch_off_replaces_stale_instances_for_custom_source(self):
        """开关关闭 + 手动自定义源/模型：旧模块残留被终止并按当前模块恢复，配置不动。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    source = self._source("my_own_source")
                    entry_a = self._entry("MyModel-A", "my_own_source")
                    entry_b = self._entry("MyModel-B", "my_own_source")
                    config["provider_sources"].append(source)
                    config["provider"].extend([entry_a, entry_b])
                    stale = self._place_stale(manager, entry_a, entry_b)
                    setattr(manager, main._PROVIDER_HOT_FLAG, True)

                    plugin = main.Main(
                        context,
                        {"api_key": ["k"], "auto_create_provider_source": False},
                    )
                    self._save_login(plugin, "tok-SUPERSECRET")
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    self.assertEqual(plugin._provider_source_state, "disabled")
                    for inst in stale:
                        self.assertTrue(inst.terminated)
                    self.assertIsNot(manager.inst_map.get(entry_a["id"]), stale[0])
                    self.assertIsNot(manager.inst_map.get(entry_b["id"]), stale[1])
                    fresh_a = manager.inst_map[entry_a["id"]]
                    fresh_b = manager.inst_map[entry_b["id"]]
                    self.assertIsInstance(fresh_a, llm_provider.MiniMaxCodeProvider)
                    self.assertIsInstance(fresh_b, llm_provider.MiniMaxCodeProvider)
                    # 新实例读取新模块令牌来源（登录态令牌），不再是旧模块的空令牌
                    self.assertEqual(fresh_a.get_current_key(), "tok-SUPERSECRET")
                    self.assertEqual(fresh_b.get_current_key(), "tok-SUPERSECRET")
                    # 配置不变、不落盘
                    self.assertEqual(config.saved, 0)
                    self.assertEqual(config["provider_sources"], [source])
                    self.assertEqual(config["provider"], [entry_a, entry_b])

    async def test_reload_recovers_user_managed_source(self):
        """手动创建的同类型默认源：不改配置归属，但旧实例同样被恢复。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    source = self._source()  # 无 managed_by：用户自管
                    entries = [
                        self._entry("MiniMax-M3.1-Flash"),
                        self._entry("MiniMax-M3"),
                    ]
                    config["provider_sources"].append(source)
                    config["provider"].extend(entries)
                    stale = self._place_stale(manager, *entries)
                    setattr(manager, main._PROVIDER_HOT_FLAG, True)

                    plugin = main.Main(context, {"api_key": ["k"]})
                    self._save_login(plugin)
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    self.assertEqual(plugin._provider_source_state, "user_managed")
                    self.assertNotIn("managed_by", source)
                    self.assertEqual(config.saved, 0)
                    self.assertEqual(config["provider_sources"], [source])
                    self.assertTrue(all(inst.terminated for inst in stale))
                    for entry in entries:
                        self.assertIsInstance(
                            manager.inst_map[entry["id"]],
                            llm_provider.MiniMaxCodeProvider,
                        )

    async def test_cold_start_leaves_loading_to_astrbot(self):
        """冷启动：ProviderManager 尚未原生加载，插件不抢先加载，无双实例。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    config["provider_sources"].append(self._source())
                    config["provider"].extend(
                        [self._entry("MiniMax-M3.1-Flash"), self._entry("MiniMax-M3")]
                    )
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    self.assertEqual(manager.provider_insts, [])
                    self.assertEqual(manager.inst_map, {})
                    self.assertFalse(getattr(manager, main._PROVIDER_HOT_FLAG, False))

    async def test_hot_recovery_loads_missing_and_skips_healthy(self):
        """热环境缺失实例补加载；健康且属于当前模块的实例不被重复加载。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    source = self._source(managed_by="astrbot_plugin_mmx_cli_tool")
                    entry_a = self._entry("MiniMax-M3.1-Flash")
                    entry_b = self._entry("MiniMax-M3")
                    config["provider_sources"].append(source)
                    config["provider"].extend([entry_a, entry_b])
                    healthy = llm_provider.MiniMaxCodeProvider(dict(entry_a))
                    manager.provider_insts.append(healthy)
                    manager.inst_map[entry_a["id"]] = healthy
                    stale = self._place_stale(manager, entry_b)
                    setattr(manager, main._PROVIDER_HOT_FLAG, True)

                    plugin = main.Main(context, {"api_key": ["k"]})
                    self._save_login(plugin)
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    self.assertTrue(stale[0].terminated)
                    self.assertIs(manager.inst_map[entry_a["id"]], healthy)
                    self.assertIsInstance(
                        manager.inst_map[entry_b["id"]],
                        llm_provider.MiniMaxCodeProvider,
                    )
                    # 再次恢复（登录/区域同步路径）不产生新实例
                    insts_before = list(manager.provider_insts)
                    await plugin._recover_provider_instances()
                    self.assertEqual(manager.provider_insts, insts_before)

    async def test_missing_instances_reloaded_when_config_clean(self):
        """配置未变但实例缺失（禁用再启用等热重载）时，补加载且不重复落盘。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    config["provider_sources"].append(
                        self._source(managed_by="astrbot_plugin_mmx_cli_tool")
                    )
                    entries = [
                        self._entry("MiniMax-M3.1-Flash"),
                        self._entry("MiniMax-M3"),
                    ]
                    config["provider"].extend(entries)
                    setattr(manager, main._PROVIDER_HOT_FLAG, True)

                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)

                    self.assertEqual(config.saved, 0)
                    self.assertEqual(
                        list(manager.inst_map),
                        [entry["id"] for entry in entries],
                    )

    async def test_hot_reload_with_only_minimax_models(self):
        """只有 MiniMax 模型、没有其他供应商的热重载也能恢复。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    plugin = main.Main(context, {"api_key": ["k"]})
                    self._save_login(plugin)
                    await plugin.initialize()  # 冷启动：只写配置
                    for entry in list(config["provider"]):
                        await manager.load_provider(dict(entry))  # AstrBot 原生加载
                    self.assertEqual(len(manager.provider_insts), 2)

                    await plugin.terminate()  # 插件重载：清理全部并打标记
                    self.assertEqual(manager.provider_insts, [])
                    self.assertEqual(manager.inst_map, {})
                    self.assertTrue(getattr(manager, main._PROVIDER_HOT_FLAG, False))

                    plugin2 = main.Main(context, {"api_key": ["k"]})
                    await plugin2.initialize()
                    self.addAsyncCleanup(plugin2.terminate)
                    self.assertEqual(len(manager.provider_insts), 2)
                    for inst in manager.provider_insts:
                        self.assertIsInstance(inst, llm_provider.MiniMaxCodeProvider)

    async def test_terminate_clears_all_and_reload_respects_config_changes(self):
        """terminate 清理全部本类型实例；禁用、改型、已删除条目不被重新加载。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    config["provider_sources"].append(
                        self._source(managed_by="astrbot_plugin_mmx_cli_tool")
                    )
                    entry_on = self._entry("MiniMax-M3.1-Flash")
                    entry_off = self._entry("MiniMax-M3", enable=False)
                    entry_changed = self._entry("MiniMax-M3-Lite", "other_src")
                    config["provider"].extend([entry_on, entry_off, entry_changed])
                    config["provider_sources"].append(
                        {"id": "other_src", "type": "openai_chat_completion"}
                    )
                    # 改型前遗留的旧实例与已删除条目的残留
                    legacy_changed = LegacyModuleProviderStub(entry_changed)
                    legacy_gone = LegacyModuleProviderStub(
                        self._entry("Gone", "mmx_minimax_code")
                    )
                    for inst in (legacy_changed, legacy_gone):
                        manager.provider_insts.append(inst)
                        manager.inst_map[inst.provider_config["id"]] = inst
                    foreign = SimpleNamespace(
                        provider_config={"id": "other/m", "type": "openai_chat_completion"}
                    )
                    manager.provider_insts.append(foreign)
                    manager.inst_map["other/m"] = foreign

                    plugin = main.Main(context, {"api_key": ["k"]})
                    self._save_login(plugin)
                    await plugin.initialize()  # 旧模块残留构成热环境：初始化即恢复原生条目
                    self.assertTrue(await plugin._register_llm_provider())

                    await plugin.terminate()
                    # 全部本类型实例清理：旧模块残留 + 运行时直注册；其他类型不动
                    self.assertEqual(
                        [inst for inst in manager.provider_insts if inst is not foreign],
                        [],
                    )
                    self.assertTrue(legacy_changed.terminated)
                    self.assertTrue(legacy_gone.terminated)
                    self.assertTrue(getattr(manager, main._PROVIDER_HOT_FLAG, False))

                    plugin2 = main.Main(context, {"api_key": ["k"]})
                    await plugin2.initialize()
                    self.addAsyncCleanup(plugin2.terminate)
                    # 只恢复启用且仍为 minimax_code 的条目
                    self.assertEqual(
                        [
                            pid
                            for pid, inst in manager.inst_map.items()
                            if inst is not foreign
                        ],
                        [entry_on["id"]],
                    )

    async def test_reload_logs_key_events_without_tokens(self):
        """关键事件有日志（注销/终止/恢复/失败原因/冷启动跳过），且不含令牌。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    cold_context, _, cold_manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    cold_plugin = main.Main(cold_context, {"api_key": ["k"]})
                    with self.assertLogs("mmx-test", level="DEBUG") as cold_logs:
                        await cold_plugin.initialize()
                    self.addAsyncCleanup(cold_plugin.terminate)
                    self.assertTrue(
                        any("冷启动" in line for line in cold_logs.output)
                    )

                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    config["provider_sources"].append(
                        self._source(managed_by="astrbot_plugin_mmx_cli_tool")
                    )
                    config["provider"].extend(
                        [
                            self._entry("MiniMax-M3.1-Flash"),
                            self._entry("MiniMax-M3"),
                            self._entry("MiniMax-M3-Lite"),
                        ]
                    )
                    plugin = main.Main(context, {"api_key": ["k"]})
                    self._save_login(plugin, "tok-SUPERSECRET")
                    with self.assertLogs("mmx-test", level="DEBUG"):
                        await plugin.initialize()  # 冷启动不加载

                    original_load = manager.load_provider

                    async def failing_load(provider_config):
                        pid = provider_config["id"]
                        if pid.endswith("MiniMax-M3"):
                            raise RuntimeError("boom")
                        if pid.endswith("MiniMax-M3-Lite"):
                            return  # 模拟 AstrBot 自身吞掉异常
                        await original_load(provider_config)

                    manager.load_provider = failing_load
                    await manager.load_provider(dict(config["provider"][0]))
                    with self.assertLogs("mmx-test", level="DEBUG") as logs:
                        await plugin.terminate()
                        plugin2 = main.Main(context, {"api_key": ["k"]})
                        await plugin2.initialize()
                    self.addAsyncCleanup(plugin2.terminate)

                    joined = "\n".join(logs.output)
                    self.assertIn("已注销 MiniMax Code 供应商适配器", joined)
                    self.assertIn("已终止 1 个旧 MiniMax Code 供应商实例", joined)
                    # 失败时不再误报"恢复完成"，计数按实际成功/待重试拆分
                    self.assertNotIn("恢复完成", joined)
                    self.assertIn(
                        "MiniMax Code 实例恢复结果：成功终止 0 个旧实例，"
                        "成功加载 1 个模型，2 项待重试失败",
                        joined,
                    )
                    self.assertIn("下次同步或重载插件可重试", joined)
                    self.assertIn("mmx_minimax_code/MiniMax-M3", joined)
                    self.assertIn("boom", joined)
                    self.assertIn("mmx_minimax_code/MiniMax-M3-Lite", joined)
                    self.assertIn("未进入实例表", joined)
                    self.assertFalse(
                        any("tok-SUPERSECRET" in line for line in logs.output)
                    )

    async def test_failed_recovery_keeps_hot_flag_retryable(self):
        """仅 MiniMax 配置：首次加载失败热标记保留，再次恢复成功才清除，且不落盘。"""
        with provider_stubs() as (llm_provider, _):
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, config, manager = make_fake_context(
                        provider_module=llm_provider
                    )
                    config["provider_sources"].append(
                        self._source(managed_by="astrbot_plugin_mmx_cli_tool")
                    )
                    entry = self._entry("MiniMax-M3.1-Flash")
                    config["provider"].append(entry)
                    setattr(manager, main._PROVIDER_HOT_FLAG, True)
                    plugin = main.Main(context, {"api_key": ["k"]})
                    real_load = manager.load_provider

                    async def failing_load(provider_config):
                        raise RuntimeError("boom")

                    manager.load_provider = failing_load
                    await plugin._recover_provider_instances()
                    # 加载失败：热标记保留，下次同步或显式恢复可重试
                    self.assertTrue(getattr(manager, main._PROVIDER_HOT_FLAG, False))
                    self.assertEqual(manager.inst_map, {})

                    manager.load_provider = real_load
                    saved_before = config.saved
                    await plugin._recover_provider_instances()
                    self.assertIn(entry["id"], manager.inst_map)
                    self.assertFalse(getattr(manager, main._PROVIDER_HOT_FLAG, False))
                    self.assertEqual(config.saved, saved_before)

    async def test_sweep_counts_termination_failures(self):
        """部分旧实例终止失败：按实际成功/失败计数记录，失败实例保留待下轮清理。"""
        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                with plugin_modules(Path(tmp)) as (main, _):
                    context, _, manager = make_fake_context()
                    entry_ok = self._entry("MiniMax-Ok", "src")
                    entry_bad = self._entry("MiniMax-Bad", "src")
                    self._place_stale(manager, entry_ok, entry_bad)

                    async def terminate_provider(pid):
                        if pid == entry_bad["id"]:
                            raise RuntimeError("boom")
                        inst = manager.inst_map.pop(pid, None)
                        if inst in manager.provider_insts:
                            manager.provider_insts.remove(inst)

                    manager.terminate_provider = terminate_provider
                    plugin = main.Main(context, {"api_key": ["k"]})
                    with self.assertLogs("mmx-test", level="DEBUG") as logs:
                        await plugin._sweep_native_provider_instances()
                    joined = "\n".join(logs.output)
                    self.assertIn("已终止 1 个旧 MiniMax Code 供应商实例", joined)
                    self.assertIn("1 个终止失败", joined)
                    self.assertIn("boom", joined)
                    self.assertIn(entry_bad["id"], manager.inst_map)
                    self.assertTrue(getattr(manager, main._PROVIDER_HOT_FLAG, False))


if __name__ == "__main__":
    unittest.main()
