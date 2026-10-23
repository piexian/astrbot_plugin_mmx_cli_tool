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
            self.set_model(provider_config.get("model", "unknown"))

        def set_model(self, model_name):
            self.model_name = model_name

        def get_model(self):
            return self.model_name

        def resolve_request_headers(self, **kwargs):
            return {}

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
            token = "token-abc"
            raw = bytearray(
                hashlib.sha256(("mavis-session|" + token).encode()).digest()[:16]
            )
            raw[6] = (raw[6] & 0x0F) | 0x40
            raw[8] = (raw[8] & 0x3F) | 0x80
            expected = str(raw.hex())
            expected = "-".join(
                (
                    expected[0:8],
                    expected[8:12],
                    expected[12:16],
                    expected[16:20],
                    expected[20:32],
                )
            )
            self.assertEqual(llm_provider.stable_session_id(token), expected)


class ProviderBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        stubs = provider_stubs()
        self.llm_provider, self.base_cls = self.enterContext(stubs)
        self.provider = self.llm_provider.MiniMaxCodeProvider(
            region="cn", token_getter=lambda: "tok-1"
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
        self.assertEqual(self.provider.get_model(), "MiniMax-M3.1-Flash")
        global_provider = self.llm_provider.MiniMaxCodeProvider(
            region="global", token_getter=lambda: "t"
        )
        self.assertEqual(
            global_provider.base_url, "https://agent.minimax.io/mavis/api/v1/llm"
        )

        # 每请求注入 Mavis 头与当前 Bearer
        headers = self.provider.resolve_request_headers(
            conversation_id=None,
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
            headers["X-Mavis-Session-Id"], self.llm_provider.stable_session_id("tok-1")
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
            llm_provider.set_token_source(lambda: "cfg-tok")
            provider = llm_provider.MiniMaxCodeProvider(
                {
                    "id": "my_mmcode",
                    "model": "MiniMax-M3.1-Flash",
                    "region": "global",
                    "timeout": 60,
                }
            )
            self.assertEqual(provider.meta().id, "my_mmcode")
            self.assertEqual(
                provider.base_url, "https://agent.minimax.io/mavis/api/v1/llm"
            )
            self.assertEqual(provider.get_current_key(), "cfg-tok")
            self.assertEqual(
                provider.resolve_request_headers()["Authorization"], "Bearer cfg-tok"
            )
            llm_provider.clear_token_source()
            self.assertEqual(provider.get_current_key(), "")


class FakeAstrBotConfig(dict):
    """AstrBotConfig 桩：记录 save_config_async 调用次数。"""

    def __init__(self) -> None:
        super().__init__()
        self.saved = 0

    async def save_config_async(self, replace_config=None, *, indent: int = 2) -> bool:
        self.saved += 1
        return True


def make_fake_context(provider_insts=None):
    """构造带配置与供应商管理器桩的插件上下文。"""
    from unittest.mock import AsyncMock

    manager = SimpleNamespace(
        provider_insts=provider_insts if provider_insts is not None else [],
        inst_map={},
        provider_sources_config=[],
        load_provider=AsyncMock(),
    )
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
                    self.assertFalse(plugin._register_llm_provider())
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
                    self.assertTrue(plugin._register_llm_provider())
                    self.assertEqual(len(manager.provider_insts), 1)
                    self.assertIs(
                        manager.inst_map["mmx_minimax_code"], plugin._llm_provider
                    )

                    # 幂等：重复注册不叠加
                    self.assertTrue(plugin._register_llm_provider())
                    self.assertEqual(len(manager.provider_insts), 1)

                    # 注销（terminate 也会走同一路径）
                    plugin._unregister_llm_provider()
                    self.assertEqual(manager.provider_insts, [])
                    self.assertNotIn("mmx_minimax_code", manager.inst_map)

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

                    # 幂等：重复执行不追加、不落盘
                    await plugin._ensure_provider_source()
                    self.assertEqual(config.saved, 1)
                    self.assertEqual(len(config["provider_sources"]), 1)
                    self.assertEqual(len(config["provider"]), 2)

    async def test_missing_instances_reloaded_when_config_clean(self):
        """配置未变但实例缺失（禁用再启用等）时，热环境必须补加载。"""
        with provider_stubs():
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with plugin_modules(root) as (main, tools):
                    context, config, manager = make_fake_context()
                    plugin = main.Main(context, {"api_key": ["k"]})
                    await plugin.initialize()
                    self.addAsyncCleanup(plugin.terminate)
                    manager.load_provider.assert_not_awaited()  # 冷启动未加载

                    # 模拟 AstrBot 已完成原生初始化，但我们的实例缺失
                    other = SimpleNamespace(provider_config={"id": "other/model"})
                    config["provider"].append(
                        {
                            "id": "other/model",
                            "enable": True,
                            "provider_source_id": "other",
                            "model": "m",
                        }
                    )
                    manager.inst_map["other/model"] = other
                    manager.provider_insts.append(other)

                    await plugin._ensure_provider_source()
                    self.assertEqual(config.saved, 1)  # 配置未变，未重复落盘
                    self.assertEqual(manager.load_provider.await_count, 2)
                    loaded_ids = [
                        call.args[0]["id"]
                        for call in manager.load_provider.await_args_list
                    ]
                    self.assertEqual(
                        loaded_ids,
                        [
                            "mmx_minimax_code/MiniMax-M3.1-Flash",
                            "mmx_minimax_code/MiniMax-M3",
                        ],
                    )

    async def test_region_change_reloads_loaded_entries(self):
        """换区域重新登录后，已加载的默认模型必须按新区域重载。"""
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
                    # 记录每次加载前旧实例是否已被移除（load_provider 为 mock，不会回填实例）
                    seen: list[tuple[str, bool]] = []

                    async def fake_load(entry):
                        entry_id = entry["id"]
                        present = any(
                            getattr(p, "provider_config", {}).get("id") == entry_id
                            for p in manager.provider_insts
                        )
                        seen.append((entry_id, present))

                    manager.load_provider = AsyncMock(side_effect=fake_load)
                    await plugin._ensure_provider_source()
                    self.assertEqual(config["provider_sources"][0]["region"], "global")
                    self.assertEqual(manager.load_provider.await_count, 2)
                    self.assertEqual(len(seen), 2)
                    self.assertTrue(all(not present for _, present in seen))

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


if __name__ == "__main__":
    unittest.main()
