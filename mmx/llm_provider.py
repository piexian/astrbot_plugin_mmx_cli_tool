"""MiniMax Code LLM Provider — 把 OAuth 账号接入 AstrBot 供应商体系。
复刻 MiniMax Code 的 Claude Messages 转发协议，按 AstrBot 对话稳定转写
Mavis 会话，并在最终出站边界按白名单重建请求头。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from astrbot.core.provider.entities import ProviderMeta, ProviderType
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic

from .endpoints import MATRIX_BASES
from .errors import ErrorCategory, MiniMaxError

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
_AUXILIARY_SESSION = "auxiliary"
_TRANSPORT_HEADER_NAMES = frozenset(
    {"accept-encoding", "connection", "content-length", "host"}
)
_REQUEST_LOGIN_EPOCH: ContextVar[str] = ContextVar(
    "mmx_request_login_epoch", default=""
)
_REQUEST_CONVERSATION_ID: ContextVar[str] = ContextVar(
    "mmx_request_conversation_id", default=""
)


def stable_session_id(login_epoch: str, conversation_id: str) -> str:
    """把登录命名空间内的 AstrBot 对话映射为官方 mvs_ 会话 ID。"""
    seed = f"astrbot-mmx\0{login_epoch}\0{conversation_id}"
    b = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return "mvs_" + b.hex()


def _timezone_offset_seconds() -> int:
    return int(time.localtime().tm_gmtoff)


#: 插件登录态的模块级晚绑定源（配置模式实例经此取 OAuth 令牌）
def _empty_token_getter() -> str:
    return ""


def _module_token() -> str:
    return _module_token_getter()


def _module_credentials() -> tuple[str, str]:
    if _module_credentials_getter is not None:
        return _module_credentials_getter()
    return _module_token(), ""


async def _module_refresh() -> None:
    if _module_token_refresher is not None:
        await _module_token_refresher()


_module_token_getter: Callable[[], str] = _empty_token_getter
_module_token_refresher: Callable[[], Awaitable[None]] | None = None
_module_models_fetcher: Callable[[], Awaitable[list[str]]] | None = None
_module_credentials_getter: Callable[[], tuple[str, str]] | None = None

#: 云端模型列表的模块级缓存（WebUI 每次点击都会新建临时实例，缓存必须模块级）
_MODELS_CACHE_TTL = 300.0
_models_cache: list[str] = []
_models_cache_at: float = 0.0
_models_cache_region: str = ""


def set_token_source(
    getter: Callable[[], str],
    refresher: Callable[[], Awaitable[None]] | None = None,
    models_fetcher: Callable[[], Awaitable[list[str]]] | None = None,
    credentials_getter: Callable[[], tuple[str, str]] | None = None,
) -> None:
    """绑定配置模式实例的令牌、刷新、模型与登录会话来源。"""
    global _module_token_getter, _module_token_refresher, _module_models_fetcher
    global _module_credentials_getter
    _module_token_getter = getter
    _module_token_refresher = refresher
    _module_models_fetcher = models_fetcher
    _module_credentials_getter = credentials_getter


def clear_token_source() -> None:
    global _module_token_getter, _module_token_refresher
    global _module_models_fetcher, _models_cache, _models_cache_at, _models_cache_region
    global _module_credentials_getter
    _module_token_getter = _empty_token_getter
    _module_token_refresher = None
    _module_models_fetcher = None
    _module_credentials_getter = None
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
    """以 OAuth 登录态调用 MiniMax Code，并按对话与登录隔离 Mavis 会话。"""

    def __init__(
        self,
        provider_config: dict[str, Any] | None = None,
        provider_settings: dict[str, Any] | None = None,
        *,
        use_api_key: bool = False,
        region: str | None = None,
        token_getter: Callable[[], str] | None = None,
        auth_refresher: Callable[[], Awaitable[None]] | None = None,
        credentials_getter: Callable[[], tuple[str, str]] | None = None,
    ) -> None:
        """支持插件直连与 AstrBot 配置模式构造。"""
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
            if credentials_getter is None:
                credentials_getter = _module_credentials
        if auth_refresher is None:
            auth_refresher = _module_refresh
        self._token_getter = token_getter
        self._credentials_getter = credentials_getter
        self._fallback_login_epoch = secrets.token_hex(16)
        self._region = region
        self._auth_refresher = auth_refresher

        from anthropic import AsyncAnthropic

        http_client = self._create_http_client(provider_config)
        if http_client is None:
            from anthropic import _base_client as anthropic_base_client

            httpx_module = getattr(anthropic_base_client, "httpx", None) or getattr(
                anthropic_base_client, "httpx2"
            )
            http_client = httpx_module.AsyncClient(timeout=self.timeout)
        http_client.event_hooks.setdefault("request", []).append(
            self._rewrite_outgoing_headers
        )
        self.client = AsyncAnthropic(
            auth_token=self._token_getter() or "unset",
            timeout=self.timeout,
            base_url=self.base_url,
            default_headers={},
            http_client=http_client,
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

    def _credentials(self) -> tuple[str, str]:
        if self._credentials_getter is not None:
            token, login_epoch = self._credentials_getter()
        else:
            token, login_epoch = self._token_getter(), ""
        return str(token or "").strip(), (
            str(login_epoch or "").strip() or self._fallback_login_epoch
        )

    def _dynamic_headers(
        self,
        conversation_id: str = "",
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, str]:
        token, login_epoch = credentials or self._credentials()
        session_key = conversation_id.strip() or _AUXILIARY_SESSION
        headers = {
            "User-Agent": "MiniMaxAgent",
            "X-Mavis-Session-Id": stable_session_id(login_epoch, session_key),
            "X-Mavis-Agent-Id": _AGENT_ID,
            "X-Mavis-Timezone-Offset": str(_timezone_offset_seconds()),
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def resolve_request_headers(self, **kwargs: Any) -> dict[str, str]:
        conversation_id = str(
            kwargs.get("conversation_id")
            or kwargs.get("umo")
            or kwargs.get("session_id")
            or ""
        )
        return self._dynamic_headers(conversation_id)

    async def _rewrite_outgoing_headers(self, request: Any) -> None:
        """在实际发送前提取会话并重建应用层请求头。"""
        token, login_epoch = self._credentials()
        bound_epoch = _REQUEST_LOGIN_EPOCH.get()
        if bound_epoch and bound_epoch != login_epoch:
            raise MiniMaxError(ErrorCategory.AUTH, "MiniMax 账号已切换，请重试")
        conversation_id = _REQUEST_CONVERSATION_ID.get()
        if not conversation_id:
            conversation_id = request.headers.get("x-astrbot-conversation-id", "").strip()
        headers = self._dynamic_headers(
            conversation_id, credentials=(token, login_epoch)
        )
        try:
            payload = json.loads(request.content)
        except (TypeError, ValueError):
            payload = None
        if isinstance(payload, dict) and payload.get("stream") is True:
            headers["Accept"] = "text/event-stream"
        for name in list(request.headers.keys()):
            if name.casefold() not in _TRANSPORT_HEADER_NAMES:
                del request.headers[name]
        for name, value in headers.items():
            request.headers[name] = value

    async def _retry_on_auth_error(self) -> bool:
        """仅在同一次登录内刷新并重试一次。"""
        if self._auth_refresher is None:
            return False
        expected_epoch = _REQUEST_LOGIN_EPOCH.get()
        if expected_epoch and self._credentials()[1] != expected_epoch:
            return False
        try:
            await self._auth_refresher()
        except Exception:  # noqa: BLE001 - 刷新失败按不可重试处理
            return False
        token, login_epoch = self._credentials()
        return bool(token) and (
            not expected_epoch or login_epoch == expected_epoch
        )

    async def text_chat(self, *args: Any, **kwargs: Any):
        epoch_token = _REQUEST_LOGIN_EPOCH.set(self._credentials()[1])
        conversation_token = _REQUEST_CONVERSATION_ID.set(
            str(
                kwargs.get("conversation_id")
                or kwargs.get("umo")
                or kwargs.get("session_id")
                or ""
            )
        )
        try:
            try:
                return await super().text_chat(*args, **kwargs)
            except Exception as exc:
                if not _is_auth_error(exc) or not await self._retry_on_auth_error():
                    raise
                return await super().text_chat(*args, **kwargs)
        finally:
            _REQUEST_CONVERSATION_ID.reset(conversation_token)
            _REQUEST_LOGIN_EPOCH.reset(epoch_token)

    async def text_chat_stream(self, *args: Any, **kwargs: Any):
        epoch_token = _REQUEST_LOGIN_EPOCH.set(self._credentials()[1])
        conversation_token = _REQUEST_CONVERSATION_ID.set(
            str(
                kwargs.get("conversation_id")
                or kwargs.get("umo")
                or kwargs.get("session_id")
                or ""
            )
        )
        try:
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
        finally:
            _REQUEST_CONVERSATION_ID.reset(conversation_token)
            _REQUEST_LOGIN_EPOCH.reset(epoch_token)


def _is_auth_error(exc: BaseException) -> bool:
    """识别 anthropic SDK 的 401 AuthenticationError（含其子类）。"""
    try:
        from anthropic import AuthenticationError
    except ImportError:  # pragma: no cover - AstrBot 运行时必装 anthropic
        return False
    return isinstance(exc, AuthenticationError)
