"""MiniMax Code LLM Provider — 把 OAuth 账号接入 AstrBot 供应商体系。

复刻 new-api minimaxcode 适配的转发协议：Claude Messages 协议直连
{agent.minimax.cn|io}/mavis/api/v1/llm/v1/messages，请求头与官方客户端一致
（Authorization Bearer + User-Agent MiniMaxAgent + X-Mavis-* 三件套）。
会话 ID 从 access_token 稳定派生（对齐 new-api stableUUID，v4 风格位）。
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from typing import Any

from astrbot.core.provider.entities import ProviderMeta, ProviderType
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic

from .endpoints import MATRIX_BASES

#: 供应商实例 ID（WebUI 会话/Agent 设置中按此选择）
PROVIDER_ID = "mmx_minimax_code"
PROVIDER_TYPE = "minimax_code"
PROVIDER_SOURCE_ID = "mmx_minimax_code"
MANAGED_BY = "astrbot_plugin_mmx_cli_tool"
#: 默认注册的模型（用户可在 WebUI 源下继续添加自定义模型）
DEFAULT_MODELS = ["MiniMax-M3.1-Flash", "MiniMax-M3"]
#: 官方客户端 MiniMax-M3 上下文窗口（解包实测 1M）
#: 云端目录默认上下文窗口（512K；官方客户端提供 1M 可选档）
DEFAULT_CONTEXT_TOKENS = 512000
#: WebUI 提供商源表单展示的占位/提醒（实际请求由插件覆盖）
API_BASE_PLACEHOLDER = "由插件按登录区域自动注入（agent.minimax.cn / agent.minimax.io）"
API_KEY_NOTICE = "此处无需 API Key：令牌由插件 /mmx login 的 OAuth 登录态接管"

#: 官方客户端未配置 agent_id 时固定发送 main；上游模型为静态列表
_AGENT_ID = "main"
MODELS = ["MiniMax-M3.1-Flash", "MiniMax-M3"]

LLM_PATH_PREFIX = "/mavis/api/v1/llm"


def stable_session_id(access_token: str) -> str:
    """从 access_token 派生 8-4-4-4-12 稳定 UUID（v4 风格位，对齐 new-api）。"""
    b = bytearray(
        hashlib.sha256(("mavis-session|" + access_token).encode("utf-8")).digest()[:16]
    )
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    hex_str = b.hex()
    return "-".join(
        (hex_str[0:8], hex_str[8:12], hex_str[12:16], hex_str[16:20], hex_str[20:32])
    )


def _timezone_offset_seconds() -> int:
    return int(time.localtime().tm_gmtoff)


#: 插件登录态的模块级晚绑定源（配置模式实例经此取 OAuth 令牌）
def _empty_token_getter() -> str:
    return ""


def _module_token() -> str:
    """配置模式取令牌：每次调用读取当前模块级源，set/clear 即时生效。"""
    return _module_token_getter()


async def _module_refresh() -> None:
    if _module_token_refresher is not None:
        await _module_token_refresher()


_module_token_getter: Callable[[], str] = _empty_token_getter
_module_token_refresher: Callable[[], Awaitable[None]] | None = None
_module_models_fetcher: Callable[[], Awaitable[list[str]]] | None = None

#: 云端模型列表的模块级缓存（WebUI 每次点击都会新建临时实例，缓存必须模块级）
_MODELS_CACHE_TTL = 300.0
_models_cache: list[str] = []
_models_cache_at: float = 0.0
_models_cache_region: str = ""


def set_token_source(
    getter: Callable[[], str],
    refresher: Callable[[], Awaitable[None]] | None = None,
    models_fetcher: Callable[[], Awaitable[list[str]]] | None = None,
) -> None:
    """把插件当前的令牌读取/刷新/模型列表函数绑定为配置模式实例的默认来源。"""
    global _module_token_getter, _module_token_refresher, _module_models_fetcher
    _module_token_getter = getter
    _module_token_refresher = refresher
    _module_models_fetcher = models_fetcher


def clear_token_source() -> None:
    global _module_token_getter, _module_token_refresher
    global _module_models_fetcher, _models_cache, _models_cache_at, _models_cache_region
    _module_token_getter = _empty_token_getter
    _module_token_refresher = None
    _module_models_fetcher = None
    _models_cache = []
    _models_cache_at = 0.0
    _models_cache_region = ""


async def fetch_available_models(
    region: str = "cn", transport: Any | None = None
) -> list[str]:
    """拉取云端模型目录（公开接口，无需登录）；失败返回空列表。

    端点与解析对齐官方客户端 official-model-config-sync：
    providers[providerId=minimax].config.models 的键即模型 ID，
    model_order 提供官方排序（缺失时按字典序）。
    """
    import httpx

    origin = MATRIX_BASES.get(region, MATRIX_BASES["cn"]).rstrip("/")
    url = f"{origin}/mavis/api/v1/models"
    try:
        async with httpx.AsyncClient(timeout=10.0, transport=transport) as client:
            res = await client.get(
                url,
                params={"region": region, "buildEnv": "prod"},
                headers={"Accept": "application/json"},
            )
        res.raise_for_status()
        payload = res.json()
    except Exception:  # noqa: BLE001 - 目录接口失败时由调用方回退静态列表
        return []
    if not isinstance(payload, dict):
        return []
    providers = payload.get("providers")
    if not isinstance(providers, list):
        return []
    config = next(
        (
            p.get("config")
            for p in providers
            if isinstance(p, dict) and p.get("providerId") == "minimax"
        ),
        None,
    )
    if not isinstance(config, dict):
        return []
    models = config.get("models")
    if not isinstance(models, dict) or not models:
        return []
    ids = [str(key) for key in models if str(key).strip()]
    order = config.get("model_order")
    if (
        isinstance(order, list)
        and order
        and all(isinstance(item, str) for item in order)
    ):
        ranked = [item for item in order if item in ids]
        rest = [item for item in ids if item not in ranked]
        return ranked + rest
    return ids


def register_provider_adapter_type() -> bool:
    """把 minimax_code 类型注册进 AstrBot 适配器表（WebUI 供应商类型列表可识别）。

    插件重载导致模块重复导入时幂等；注册后配置了该类型的条目在下次启动可被
    AstrBot 原生加载（启动顺序：先加载插件、后实例化 Provider）。
    """
    try:
        from astrbot.core.provider.register import (
            provider_cls_map,
            register_provider_adapter,
        )
    except ImportError:
        return False
    if PROVIDER_TYPE in provider_cls_map:
        return True
    try:
        register_provider_adapter(
            PROVIDER_TYPE,
            "MiniMax Code 官方通道（OAuth 登录，消耗订阅额度与签到积分）",
            ProviderType.CHAT_COMPLETION,
            default_config_tmpl={
                "id": PROVIDER_SOURCE_ID,
                "type": PROVIDER_TYPE,
                "provider_type": "chat_completion",
                "provider": "minimax",
                "enable": True,
                "key": [API_KEY_NOTICE],
                "api_base": API_BASE_PLACEHOLDER,
                "region": "cn",
                "timeout": 120,
            },
            provider_display_name="MiniMax Code",
        )(MiniMaxCodeProvider)
    except Exception:
        return False
    return True


def unregister_provider_adapter_type() -> None:
    """插件卸载时移除类型注册，避免残留失效类引用。"""
    try:
        from astrbot.core.provider.register import provider_cls_map, provider_registry

        provider_cls_map.pop(PROVIDER_TYPE, None)
        provider_registry[:] = [
            m for m in provider_registry if getattr(m, "type", None) != PROVIDER_TYPE
        ]
    except ImportError:
        pass


class MiniMaxCodeProvider(ProviderAnthropic):
    """以 OAuth 访问令牌调用 MiniMax Code 的 Anthropic 协议供应商。

    token_getter 同步返回当前 access_token（每请求经 resolve_request_headers 注入，
    令牌轮换无需重建客户端）；auth_refresher 在 401 时异步刷新后重试一次。
    """

    def __init__(
        self,
        provider_config: dict[str, Any] | None = None,
        provider_settings: dict[str, Any] | None = None,
        *,
        use_api_key: bool = False,
        region: str | None = None,
        token_getter: Callable[[], str] | None = None,
        auth_refresher: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """双构造：插件直连模式传 region/token_getter；配置模式由 AstrBot 按
        provider_config 实例化（令牌经 set_token_source 晚绑定到插件登录态）。"""
        provider_config = dict(provider_config or {})
        region = region or str(provider_config.get("region") or "cn")
        origin = MATRIX_BASES.get(region, MATRIX_BASES["cn"]).rstrip("/")
        provider_config.setdefault("id", PROVIDER_ID)
        provider_config["type"] = PROVIDER_TYPE
        provider_config.setdefault("enable", True)
        provider_config["api_base"] = f"{origin}{LLM_PATH_PREFIX}"
        provider_config.setdefault("model", DEFAULT_MODELS[0])
        provider_config.setdefault("timeout", 120.0)
        super().__init__(
            provider_config, provider_settings or {}, use_api_key=use_api_key
        )
        if token_getter is None:
            token_getter = _module_token
        if auth_refresher is None:
            auth_refresher = _module_refresh
        self._token_getter = token_getter
        self._region = region
        self._auth_refresher = auth_refresher
        # 绕过 _init_api_key 的 x-api-key 方案：auth_token 走 Authorization Bearer，
        # 实际令牌由 resolve_request_headers 每请求覆盖
        from anthropic import AsyncAnthropic

        self.client = AsyncAnthropic(
            auth_token=self._token_getter() or "unset",
            timeout=self.timeout,
            base_url=self.base_url,
            default_headers=self.custom_headers,
        )

    # ---- 元数据与密钥 ----

    def meta(self) -> ProviderMeta:
        """直构 ProviderMeta，避免查 provider_cls_map（插件类型未注册进适配器表）。"""
        return ProviderMeta(
            id=str(self.provider_config.get("id", PROVIDER_ID)),
            model=self.get_model(),
            type=PROVIDER_TYPE,
            provider_type=ProviderType.CHAT_COMPLETION,
        )

    async def get_models(self) -> list[str]:
        """AstrBot WebUI「获取模型列表」入口：云端公开目录优先，失败回退静态。

        WebUI 每次点击都会新建临时实例，缓存必须放模块级；键入 region 防止
        双区域部署串缓存。
        """
        global _models_cache, _models_cache_at, _models_cache_region
        now = time.monotonic()
        if (
            _models_cache
            and _models_cache_region == self._region
            and now - _models_cache_at < _MODELS_CACHE_TTL
        ):
            return list(_models_cache)
        fetched: list[str] = []
        if _module_models_fetcher is not None:
            try:
                fetched = await _module_models_fetcher()
            except Exception:  # noqa: BLE001 - 取数失败回退公开目录
                fetched = []
        if not fetched:
            fetched = await fetch_available_models(self._region)
        if fetched:
            _models_cache = list(dict.fromkeys(fetched))
            _models_cache_at = now
            _models_cache_region = self._region
            return list(_models_cache)
        return list(DEFAULT_MODELS)

    def get_current_key(self) -> str:
        return self._token_getter()

    def get_keys(self) -> list[str]:
        return [self.get_current_key()]

    def set_key(self, key: str) -> None:
        # OAuth 令牌由登录态管理，不支持外部改写
        return None

    # ---- 请求头与 401 重试 ----

    def _dynamic_headers(self) -> dict[str, str]:
        token = self._token_getter()
        headers = {
            "User-Agent": "MiniMaxAgent",
            "X-Mavis-Agent-Id": _AGENT_ID,
            "X-Mavis-Timezone-Offset": str(_timezone_offset_seconds()),
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Mavis-Session-Id"] = stable_session_id(token)
        return headers

    def resolve_request_headers(self, **kwargs: Any) -> dict[str, str]:
        headers = super().resolve_request_headers(**kwargs)
        headers.update(self._dynamic_headers())
        return headers

    async def _retry_on_auth_error(self) -> bool:
        """401 时刷新登录态；无可用的 refresher 或刷新失败返回 False。"""
        if self._auth_refresher is None:
            return False
        try:
            await self._auth_refresher()
        except Exception:  # noqa: BLE001 - 刷新失败按不可重试处理
            return False
        return bool(self._token_getter())

    async def text_chat(self, *args: Any, **kwargs: Any):
        try:
            return await super().text_chat(*args, **kwargs)
        except Exception as exc:
            if not _is_auth_error(exc) or not await self._retry_on_auth_error():
                raise
            return await super().text_chat(*args, **kwargs)

    async def text_chat_stream(self, *args: Any, **kwargs: Any):
        # 首个事件前鉴权失败可安全重试（请求尚未对用户产出内容）
        for _attempt in range(2):
            agen = super().text_chat_stream(*args, **kwargs)
            try:
                first = await agen.__anext__()
            except StopAsyncIteration:
                return
            except Exception as exc:
                await agen.aclose()
                if _is_auth_error(exc) and await self._retry_on_auth_error():
                    continue
                raise
            yield first
            async for chunk in agen:
                yield chunk
            return


def _is_auth_error(exc: BaseException) -> bool:
    """识别 anthropic SDK 的 401 AuthenticationError（含其子类）。"""
    try:
        from anthropic import AuthenticationError
    except ImportError:  # pragma: no cover - AstrBot 运行时必装 anthropic
        return False
    return isinstance(exc, AuthenticationError)
