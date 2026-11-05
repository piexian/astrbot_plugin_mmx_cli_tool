"""MiniMax Code LLM Provider — 把 OAuth 账号接入 AstrBot 供应商体系。
复刻 MiniMax Code 的 Claude Messages 转发协议，按 AstrBot 对话稳定转写
Mavis 会话，并在最终出站边界按白名单重建请求头。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any
from urllib.parse import urlsplit

from astrbot.core.provider.entities import ProviderMeta, ProviderType
from astrbot.core.provider.sources.anthropic_source import ProviderAnthropic

from .endpoints import MATRIX_BASES
from .errors import ErrorCategory, MiniMaxError


logger = logging.getLogger(__name__)
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


#: 官方工作台推理档位：MiniMax-M3.1-Flash（forced_on）按 output_config.effort 发送
_EFFORT_LEVELS = ("default", "low", "medium", "high", "xhigh", "max")
#: 模型请求形态：M3.1-Flash 按档位；MiniMax-M3（switchable）只有 adaptive/disabled 开关
_EFFORT_MODEL = "minimax-m3.1-flash"
_SWITCH_MODEL = "minimax-m3"
LLM_PATH_PREFIX = "/mavis/api/v1/llm"
_TRANSPORT_HEADER_NAMES = frozenset(
    {"accept-encoding", "connection", "content-length", "host"}
)
_REQUEST_LOGIN_EPOCH: ContextVar[str] = ContextVar(
    "mmx_request_login_epoch", default=""
)
_REQUEST_SESSION_KEY: ContextVar[str] = ContextVar(
    "mmx_request_session_key", default=""
)


def stable_session_id(login_epoch: str, conversation_id: str) -> str:
    """把登录命名空间内的会话键映射为官方 mvs_ 会话 ID。"""
    seed = f"astrbot-mmx\0{login_epoch}\0{conversation_id}"
    b = bytearray(hashlib.sha256(seed.encode("utf-8")).digest()[:16])
    b[6] = (b[6] & 0x0F) | 0x40
    b[8] = (b[8] & 0x3F) | 0x80
    return "mvs_" + b.hex()


def _new_session_key() -> str:
    return f"request:{secrets.token_hex(16)}"


def _session_key_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    session_key = (
        kwargs.get("conversation_id") or kwargs.get("umo") or kwargs.get("session_id")
    )
    # ProviderAnthropic 的第二个位置参数是 session_id。
    if not session_key and len(args) > 1:
        session_key = args[1]
    return str(session_key or _new_session_key())


def _timezone_offset_seconds() -> int:
    return int(time.localtime().tm_gmtoff)


#: 插件登录态的模块级晚绑定源（配置模式实例经此取 OAuth 令牌）
def _empty_token_getter() -> str:
    return ""


def _default_effort() -> str:
    return "default"


def _module_token() -> str:
    return _module_token_getter()


def _module_credentials() -> tuple[str, str]:
    if _module_credentials_getter is not None:
        return _module_credentials_getter()
    return _module_token(), ""


def _module_region() -> str:
    if _module_region_getter is not None:
        return _module_region_getter()
    return ""


def _module_effort() -> str:
    return _module_effort_getter()


async def _module_refresh() -> None:
    if _module_token_refresher is not None:
        await _module_token_refresher()


_module_token_getter: Callable[[], str] = _empty_token_getter
_module_token_refresher: Callable[[], Awaitable[None]] | None = None
_module_models_fetcher: Callable[[], Awaitable[list[str]]] | None = None
_module_credentials_getter: Callable[[], tuple[str, str]] | None = None
_module_region_getter: Callable[[], str] | None = None
_module_effort_getter: Callable[[], str] = _default_effort

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
    region_getter: Callable[[], str] | None = None,
    effort_getter: Callable[[], str] | None = None,
) -> None:
    """绑定配置模式实例的令牌、刷新、模型、登录会话、区域与工作台档位来源。"""
    global _module_token_getter, _module_token_refresher, _module_models_fetcher
    global _module_credentials_getter, _module_region_getter, _module_effort_getter
    _module_token_getter = getter
    _module_token_refresher = refresher
    _module_models_fetcher = models_fetcher
    _module_credentials_getter = credentials_getter
    _module_region_getter = region_getter
    _module_effort_getter = effort_getter or _default_effort


def clear_token_source() -> None:
    global _module_token_getter, _module_token_refresher
    global _module_models_fetcher, _models_cache, _models_cache_at, _models_cache_region
    global _module_credentials_getter, _module_region_getter, _module_effort_getter
    _module_token_getter = _empty_token_getter
    _module_token_refresher = None
    _module_models_fetcher = None
    _module_credentials_getter = None
    _module_region_getter = None
    _module_effort_getter = _default_effort
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
        region_getter: Callable[[], str] | None = None,
        effort_getter: Callable[[], str] | None = None,
    ) -> None:
        """支持插件直连与 AstrBot 配置模式构造。

        region 仅作兜底区域（默认 cn）；实际请求区域由 region_getter 跟随登录态。
        """
        provider_config = dict(provider_config or {})
        region = region if region in MATRIX_BASES else "cn"
        origin = MATRIX_BASES.get(region, MATRIX_BASES["cn"]).rstrip("/")
        provider_config.setdefault("id", PROVIDER_ID)
        provider_config["type"] = PROVIDER_TYPE
        provider_config.setdefault("enable", True)
        provider_config["api_base"] = f"{origin}{LLM_PATH_PREFIX}"
        provider_config.setdefault("model", DEFAULT_MODELS[0])
        provider_config.setdefault("timeout", 120.0)
        # custom_extra_body 归一化仅限两个已适配模型：reasoning_effort 旧别名与
        # output_config/thinking 由请求准备统一落位（合并语义确定）；自定义模型
        # 不弹任何字段，显式配置原样透传到最终请求
        self._legacy_effort = ""
        self._explicit_output_config: dict[str, Any] | None = None
        self._explicit_thinking: dict[str, Any] | None = None
        extra_body = provider_config.get("custom_extra_body")
        extra_body = dict(extra_body) if isinstance(extra_body, dict) else {}
        model_name = str(provider_config.get("model", "")).strip().lower()
        if model_name in (_EFFORT_MODEL, _SWITCH_MODEL):
            legacy_effort = str(
                extra_body.pop("reasoning_effort", "") or ""
            ).strip().lower()
            if legacy_effort:
                # 非法旧别名告警并固定 default：不透传，也不向工作台档位滑落
                self._legacy_effort = self._validated_effort(
                    legacy_effort, provider_config.get("id"), model_name
                )
            output_config = extra_body.pop("output_config", None)
            if isinstance(output_config, dict):
                self._explicit_output_config = dict(output_config)
                if "effort" in self._explicit_output_config:
                    # 非法显式档位告警并固定 default，其余键原样保留
                    self._explicit_output_config["effort"] = self._validated_effort(
                        self._explicit_output_config["effort"],
                        provider_config.get("id"),
                        model_name,
                    )
            thinking = extra_body.pop("thinking", None)
            if isinstance(thinking, dict):
                self._explicit_thinking = dict(thinking)
        provider_config["custom_extra_body"] = extra_body
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
        self._region_getter = region_getter or _module_region
        self._auth_refresher = auth_refresher
        self._effort_getter = effort_getter or _module_effort
        # 与 AstrBot 基类一致（桩环境下基类不赋值）；显式配置时请求准备不改写
        self.thinking_config = provider_config.get("anth_thinking_config", {})

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

    def _current_region(self) -> str:
        """当前请求区域：优先跟随登录态，未登录或非法时用兜底区域。"""
        try:
            region = str(self._region_getter() or "")
        except Exception:  # noqa: BLE001 - 取区域失败回退兜底区域
            region = ""
        return region if region in MATRIX_BASES else self._region

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
        region = self._current_region()
        now = time.monotonic()
        if (
            _models_cache
            and _models_cache_region == region
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
            fetched = await fetch_available_models(region)
        if fetched:
            _models_cache = list(dict.fromkeys(fetched))
            _models_cache_at = now
            _models_cache_region = region
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
        session_key: str = "",
        credentials: tuple[str, str] | None = None,
    ) -> dict[str, str]:
        token, login_epoch = credentials or self._credentials()
        session_key = (
            session_key.strip() or _REQUEST_SESSION_KEY.get() or _new_session_key()
        )
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
        session_key = _REQUEST_SESSION_KEY.get() or _session_key_from_call((), kwargs)
        return self._dynamic_headers(session_key)

    async def _rewrite_outgoing_headers(self, request: Any) -> None:
        """在实际发送前提取会话并重建应用层请求头。"""
        token, login_epoch = self._credentials()
        region = self._current_region()
        bound_epoch = _REQUEST_LOGIN_EPOCH.get()
        if bound_epoch and bound_epoch != login_epoch:
            raise MiniMaxError(ErrorCategory.AUTH, "MiniMax 账号已切换，请重试")
        # 区域跟随登录态：按当前区域改写目标主机，保持路径与查询不变
        target = urlsplit(MATRIX_BASES[region])
        host = target.hostname or ""
        if host:
            request.url = request.url.copy_with(
                scheme=target.scheme or "https", host=host, port=target.port
            )
            request.headers["Host"] = target.netloc
        session_key = _REQUEST_SESSION_KEY.get() or request.extensions.get(
            "mmx_session_key", ""
        )
        if not session_key:
            session_key = request.headers.get("x-astrbot-conversation-id", "").strip()
        if not session_key:
            session_key = _new_session_key()
        request.extensions["mmx_session_key"] = session_key
        headers = self._dynamic_headers(
            session_key, credentials=(token, login_epoch)
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
        session_token = _REQUEST_SESSION_KEY.set(_session_key_from_call(args, kwargs))
        try:
            try:
                return await super().text_chat(*args, **kwargs)
            except Exception as exc:
                if not _is_auth_error(exc) or not await self._retry_on_auth_error():
                    raise
                return await super().text_chat(*args, **kwargs)
        finally:
            _REQUEST_SESSION_KEY.reset(session_token)
            _REQUEST_LOGIN_EPOCH.reset(epoch_token)

    async def text_chat_stream(self, *args: Any, **kwargs: Any):
        epoch_token = _REQUEST_LOGIN_EPOCH.set(self._credentials()[1])
        session_token = _REQUEST_SESSION_KEY.set(_session_key_from_call(args, kwargs))
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
            _REQUEST_SESSION_KEY.reset(session_token)
            _REQUEST_LOGIN_EPOCH.reset(epoch_token)

    # ---- 请求参数（M3.1 effort 档位 / M3 thinking 开关） ----

    @staticmethod
    def _validated_effort(raw: Any, provider_id: Any, model: Any) -> str:
        """显式档位校验：合法返回归一化小写；非法告警并回退推荐档位 default。"""
        effort = str(raw or "").strip().lower()
        if effort in _EFFORT_LEVELS:
            return effort
        logger.warning(
            f"[mmx] Provider {provider_id} 模型 {model} 的推理档位类型无效"
            f"（{type(raw).__name__}），已回退 default"
        )
        return "default"

    def _resolve_effort(self) -> str:
        """M3.1 最终档位：显式 output_config > reasoning_effort 旧别名 > 工作台 > default。

        显式档位构造时已校验：非法值告警并固定 default，不向低优先级滑落。
        """
        if self._explicit_output_config is not None and (
            "effort" in self._explicit_output_config
        ):
            return self._explicit_output_config["effort"]
        if self._legacy_effort:
            return self._legacy_effort
        effort = str(self._effort_getter() or "").strip().lower()
        return effort if effort in _EFFORT_LEVELS else "default"

    def _patch_model_payloads(self, payloads: dict) -> None:
        """按模型落最终 thinking/output_config；普通与流式共用，不碰其余字段。"""
        model = str(self.get_model() or "").strip().lower()
        if model not in (_EFFORT_MODEL, _SWITCH_MODEL):
            return
        if model == _EFFORT_MODEL:
            if self.thinking_config:
                # 显式 anth_thinking_config：先恢复非冲突显式字段再交基类，
                # native thinking/effort 由基类应用并覆盖显式档位
                if self._explicit_output_config is not None:
                    payloads["output_config"] = dict(self._explicit_output_config)
                elif self._legacy_effort:
                    payloads["output_config"] = {"effort": self._legacy_effort}
                return
            payloads.pop("thinking", None)
            if self._explicit_thinking is not None:
                payloads["thinking"] = dict(self._explicit_thinking)
            output_config = (
                dict(self._explicit_output_config)
                if self._explicit_output_config is not None
                else {}
            )
            output_config["effort"] = self._resolve_effort()
            payloads["output_config"] = output_config
            return
        # M3：二值开关，不吃工作台档位，也不发送档位字段
        payloads.pop("output_config", None)
        if self.thinking_config:
            # 显式 anth_thinking_config：native thinking 交基类应用；
            # 显式 thinking/output_config 对 M3 均为冲突字段，不恢复
            return
        if self._explicit_thinking is not None:
            payloads["thinking"] = dict(self._explicit_thinking)
        elif not isinstance(payloads.get("thinking"), dict):
            payloads["thinking"] = {"type": "adaptive"}

    async def _query(self, payloads: dict, tools: Any, **kwargs: Any):
        self._patch_model_payloads(payloads)
        return await super()._query(payloads, tools, **kwargs)

    async def _query_stream(self, payloads: dict, tools: Any, **kwargs: Any):
        self._patch_model_payloads(payloads)
        async for chunk in super()._query_stream(payloads, tools, **kwargs):
            yield chunk


def _is_auth_error(exc: BaseException) -> bool:
    """识别 anthropic SDK 的 401 AuthenticationError（含其子类）。"""
    try:
        from anthropic import AuthenticationError
    except ImportError:  # pragma: no cover - AstrBot 运行时必装 anthropic
        return False
    return isinstance(exc, AuthenticationError)
