"""MiniMax 多模态工具 — AstrBot 插件入口。

提供 LLM 工具和指令，覆盖 MiniMax 图片/视频生成、语音合成与转写、联网搜索、视觉理解和额度查询。

"""

from __future__ import annotations

import asyncio
import html
import json
import re
import time as _time
import uuid
from pathlib import Path

from astrbot.api import AstrBotConfig, logger, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.core.message import components as Comp
from astrbot.core.message.components import Record, Video
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from astrbot.api.util import SessionController, session_waiter
from .mmx import MiniMaxClient
from .mmx.plugin_service import MiniMaxService, ServiceError
from .mmx.apis.image import ImageAPI
from .mmx.apis.video import VideoAPI
from .mmx.apis.search import SearchAPI
from .mmx.apis.vision import VisionAPI
from .mmx.apis.quota import QuotaAPI
from .mmx.apis.speech import SpeechAPI
from .mmx.apis.transcription import SpeechToTextAPI, TranscriptionOptions
from .mmx.transcription import TranscriptionService
from .tools.transcription_tools import transcribe_event
from .mmx.files import FileAPI
from .mmx.matrix import MatrixAPI
from .mmx.account import MiniMaxCodeAccount
from .mmx.media_transfer import MatrixMediaClient, MediaTransferError
from .mmx.oauth import OAuthClient, OAuthError, TokenStore
from .mmx.web_api import EFFORT_LEVELS, MCodeWebAPI
from .mmx.vision_input import extract_image_input, extract_image_inputs
from .mmx.direct_command_args import (
    DirectCommandError,
    parse_file_command,
    parse_image_command,
    parse_image_search_command,
    parse_music_command,
    parse_speech_command,
    parse_transcription_command,
    parse_video_command,
)
from .mmx.errors import ErrorCategory, MiniMaxError, friendly_message
from .mmx.keypool import KeyPool
from .mmx.quota_usage import (
    format_quota_reset_time,
    format_quota_usage,
    is_video_quota_model,
    normalize_quota_models,
    quota_window_label,
)
from .mmx.utils import (
    get_shared_temp_dir,
    resolve_existing_data_path,
    resolve_image,
    resolve_subject_reference,
)
from .tools import (
    GenerateImageTool,
    GenerateVideoTool,
    QueryVideoTaskTool,
    DownloadVideoTool,
    WebSearchTool,
    DescribeImageTool,
    CheckQuotaTool,
    SpeechSynthesizeTool,
    SpeechTranscribeTool,
    ListVoicesTool,
    UploadFileTool,
    ListFilesTool,
    DeleteFileTool,
    SearchImagesTool,
    ImageReverseSearchTool,
    GenerateMusicTool,
    GenerateSoundEffectTool,
)
from .tools.image_reverse_tools import format_reverse_results
from .tools.image_search_tools import search_and_download


#: 刷新令牌被服务端明确拒绝的错误码（仅这些才清除本地登录态）
_REFRESH_FATAL_CODES = {
    "invalid_grant",
    "invalid_token",
    "access_denied",
    "unauthorized_client",
}


def _clean_display_text(value: object, *, limit: int | None = None) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit is not None and len(text) > limit:
        return text[:limit].rstrip()
    return text


def _extract_vision_text(result: dict) -> str:
    for key in ("content", "description", "text"):
        text = _clean_display_text(result.get(key))
        if text:
            return text

    choices = result.get("choices", [])
    if choices:
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        text = _clean_display_text(message.get("content"))
        if text:
            return text

    data = result.get("data")
    if isinstance(data, dict):
        for key in ("content", "description", "text"):
            text = _clean_display_text(data.get(key))
            if text:
                return text

    return _clean_display_text(result, limit=2000)


def _file_size_text(value: object) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "未知大小"
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f} MB"
    if size >= 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size} B"


def _file_payload(result: dict) -> dict:
    item = result.get("file")
    return item if isinstance(item, dict) else result


def _format_quota_window(label: str, window: dict, *, is_video: bool = False) -> str:
    text = f"{label}: {format_quota_usage(window, is_video=is_video)}"
    if window.get("unlimited") or window.get("unavailable"):
        return text
    reset = format_quota_reset_time(window.get("remains_time"))
    if reset:
        text += "（即将重置）" if reset == "即将" else f"（{reset}后重置）"
    return text


def _format_account_balance(result: dict) -> list[str]:
    """格式化账户余额（sk-api- Key 的 query_balance 响应，对齐 mmx-cli 展示）。"""
    fields = [
        ("可用余额", "available_amount"),
        ("现金", "cash_balance"),
        ("代金券", "voucher_balance"),
        ("信用额度", "credit_balance"),
        ("欠费", "owed_amount"),
    ]
    lines = ["账户余额:"]
    for label, key in fields:
        value = result.get(key)
        lines.append(f"  {label}: {value if value is not None else '未知'}")
    if result.get("balance_alert_switch") is not None:
        alert = "开" if result.get("balance_alert_switch") else "关"
        lines.append(f"  余额提醒: {alert}")
    return lines


def _normalize_quota_command_args(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""

    parts = text.split(maxsplit=2)
    first = parts[0].lstrip("/").lower()
    if first == "mmx":
        if len(parts) >= 2 and parts[1].lower() == "quota":
            return parts[2].strip() if len(parts) > 2 else ""
        rest = text.split(maxsplit=1)
        return rest[1].strip() if len(rest) > 1 else ""
    if first == "quota":
        rest = text.split(maxsplit=1)
        return rest[1].strip() if len(rest) > 1 else ""
    return text


@filter.command_group("mmx")
def mmx_group() -> None:
    """MiniMax 多模态工具指令组"""


class Main(star.Star):
    """MiniMax multi-modal plugin — image, video, speech, search, vision, quota."""

    def __init__(self, context: star.Context, config: AstrBotConfig) -> None:
        super().__init__(context)

        # 读取配置
        self._config = config
        raw_keys = config.get("api_key", [])
        keys: list[str] = (
            [str(k).strip() for k in raw_keys if str(k).strip()]
            if isinstance(raw_keys, list)
            else []
        )
        region = str(config.get("region", "cn"))
        base_url_override = str(config.get("base_url", "")).strip() or None
        timeout = float(config.get("timeout", 300))
        effort = str(config.get("thinking_effort", "default")).strip().lower()
        self._thinking_effort = effort if effort in EFFORT_LEVELS else "default"
        self._video_poll_interval = int(config.get("video_poll_interval", 5))
        self._video_timeout = int(config.get("video_timeout", 600))
        self._default_image_model = str(config.get("default_image_model", "")).strip()
        self._default_video_model = str(config.get("default_video_model", "")).strip()
        self._default_video_sef_model = str(
            config.get("default_video_sef_model", "")
        ).strip()
        self._default_video_subject_model = str(
            config.get("default_video_subject_model", "")
        ).strip()
        self._default_speech_model = str(config.get("default_speech_model", "")).strip()
        self._default_speech_voice = str(config.get("default_speech_voice", "")).strip()

        # 插件数据目录
        _plugin_name = getattr(self, "name", None) or "astrbot_plugin_mmx_cli_tool"
        self._plugin_data_dir = (
            Path(get_astrbot_data_path()) / "plugin_data" / _plugin_name
        )
        self._plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # 缓存目录：生成的媒体文件只作中转，统一放 AstrBot 临时目录，无需自行清理
        self._cache_dir = Path(get_shared_temp_dir())
        # 是否允许 LLM 工具/直接指令读取 AstrBot 临时目录（可在配置中收紧）
        self._allow_temp_dir_reads = bool(config.get("allow_astrbot_temp_dir", True))
        self._extra_dirs = [str(self._cache_dir)] if self._allow_temp_dir_reads else []

        # 创建客户端 — 支持单 Key 和多 Key 池两种模式
        if not keys:
            logger.warning("[mmx] api_key 未配置，插件将无法调用 API")
            self._key_pool = None
            client_kwargs: dict = {
                "api_key": "",
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }
        elif len(keys) > 1:
            self._key_pool = KeyPool(keys, region)
            logger.info(f"[mmx] 多 Key 模式已启用，共 {len(keys)} 个 Key")
            client_kwargs = {
                "key_getter": self._key_pool.get_key,
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }
        else:
            self._key_pool = None
            client_kwargs = {
                "api_key": keys[0],
                "base_url": base_url_override,
                "region": region,
                "timeout": timeout,
            }

        self._client = MiniMaxClient(**client_kwargs)

        # OAuth 账号登录与 Matrix MCP 底座（搜图/以图搜图/音乐音效）
        self._region = region
        self._static_keys = keys
        self._oauth = OAuthClient()
        self._token_store = TokenStore(self._plugin_data_dir / "auth.json")
        self._matrix = MatrixAPI(
            self._client, self._matrix_credentials, self._refresh_matrix_token
        )
        self._matrix_media = MatrixMediaClient(self._matrix)
        self._login_sessions: set[str] = set()
        self._refresh_lock = asyncio.Lock()

        # MiniMax Code 账户（签到/积分/余量）与 LLM Provider
        self._account = MiniMaxCodeAccount(
            self._client, self._matrix_credentials, self._refresh_matrix_token
        )
        self._llm_provider = None
        self._auto_signin_task: asyncio.Task | None = None
        self._auto_signin_enabled = bool(config.get("auto_signin", False))
        self._signin_claimed_day = ""
        self._provider_source_warning = ""
        # 以图搜图工具实例（LLM 与直接指令共用）
        self._reverse_image_tool = ImageReverseSearchTool(
            self._matrix,
            self._matrix_media,
            str(self._plugin_data_dir),
            extra_allowed_dirs=self._extra_dirs,
        )

        self._image = ImageAPI(self._client)
        self._video = VideoAPI(self._client)
        self._search = SearchAPI(self._client)
        self._vision = VisionAPI(self._client)
        self._quota = QuotaAPI(self._client)
        self._speech = SpeechAPI(self._client)
        self._files = FileAPI(self._client)
        self._transcription = TranscriptionService(
            SpeechToTextAPI(self._client),
            str(self._plugin_data_dir),
            str(self._cache_dir),
            extra_allowed_dirs=self._extra_dirs,
            default_model=str(
                config.get("default_transcription_model", "asr-1.0")
            ).strip(),
        )

        # 插件数据目录路径（字符串）
        _data_dir = str(self._plugin_data_dir)
        _cache_dir = str(self._cache_dir)
        # AstrBot 临时目录：聊天图片下载后存放于此，允许 LLM 工具读取
        _extra_dirs = self._extra_dirs

        self._service = MiniMaxService(self, configured=bool(keys))
        self._terminate_task: asyncio.Task | None = None
        self._web_api: MCodeWebAPI | None = None

        # 注册 LLM 工具
        self._tools = [
            GenerateImageTool(
                self._image,
                self._default_image_model,
                _data_dir,
                cache_dir=_cache_dir,
                extra_allowed_dirs=_extra_dirs,
            ),
            GenerateVideoTool(
                self._video,
                self._video_poll_interval,
                self._video_timeout,
                self._default_video_model,
                self._default_video_sef_model,
                self._default_video_subject_model,
                _data_dir,
                cache_dir=_cache_dir,
                extra_allowed_dirs=_extra_dirs,
            ),
            QueryVideoTaskTool(self._video, self._default_video_model),
            DownloadVideoTool(self._video, _data_dir, cache_dir=_cache_dir),
            WebSearchTool(self._search),
            DescribeImageTool(self._vision, _data_dir, extra_allowed_dirs=_extra_dirs),
            CheckQuotaTool(self._quota, keys),
            SpeechSynthesizeTool(
                self._speech,
                _data_dir,
                self._default_speech_model,
                default_voice=self._default_speech_voice,
                cache_dir=_cache_dir,
            ),
            ListVoicesTool(self._speech),
            SpeechTranscribeTool(self._transcription),
            UploadFileTool(self._files, _data_dir, extra_allowed_dirs=_extra_dirs),
            ListFilesTool(self._files),
            DeleteFileTool(self._files),
            SearchImagesTool(self._matrix, self._matrix_media, _cache_dir),
            self._reverse_image_tool,
            GenerateMusicTool(self._matrix, self._matrix_media, _cache_dir),
            GenerateSoundEffectTool(self._matrix, self._matrix_media, _cache_dir),
        ]
        context.add_llm_tools(*self._tools)

    def get_service(self, api_version: int = 1) -> MiniMaxService:
        if type(api_version) is not int or api_version != 1:
            raise ServiceError("unsupported_version")
        return self._service

    async def initialize(self) -> None:
        self._service._initialize()
        self._web_api = MCodeWebAPI(self)
        self._web_api.register()
        try:
            from .mmx import llm_provider as _llm_module

            if _llm_module.register_provider_adapter_type():
                logger.info(
                    "[mmx] 供应商类型 minimax_code 已注册进 AstrBot 适配器表："
                    "WebUI「模型供应商 → 添加供应商」的类型下拉可选 MiniMax Code"
                )
            else:
                logger.warning(
                    "[mmx] MiniMax Code 供应商类型注册未生效（当前 AstrBot 版本可能不支持）"
                )
        except Exception as e:
            logger.warning(f"[mmx] MiniMax Code 供应商类型注册失败: {e}")
        try:
            self._ensure_token_login_epoch()
            from .mmx.llm_provider import set_token_source

            set_token_source(
                self._provider_token,
                self._provider_refresh,
                credentials_getter=self._provider_credentials,
            )
        except Exception as e:
            logger.warning(f"[mmx] LLM 运行时来源绑定失败: {e}")
        await self._ensure_provider_source()
        if self._provider_source_warning:
            # 提供商源 ID 被外部配置占用：退回运行时直注册，保证会话下拉可用
            self._register_llm_provider()
        if self._auto_signin_enabled:
            self._auto_signin_task = asyncio.create_task(self._auto_signin_loop())

    async def terminate(self) -> None:
        if self._auto_signin_task is not None:
            self._auto_signin_task.cancel()
            self._auto_signin_task = None
        self._unregister_llm_provider()  # 仅回退路径的运行时实例需要注销
        try:
            from .mmx import llm_provider as _llm_module

            # 无论是否走过运行时直注册，都清理模块级令牌/模型来源绑定
            _llm_module.clear_token_source()
            _llm_module.unregister_provider_adapter_type()
        except Exception:
            pass
        if self._web_api is not None:
            await self._web_api.close()
        self._service._begin_close()
        if self._terminate_task is None:

            async def cleanup():
                try:
                    await self._service._drain()
                finally:
                    try:
                        try:
                            await self._oauth.close()
                            await self._matrix_media.close()
                        finally:
                            await self._client.close()
                    finally:
                        self._service._finish_close()

            self._terminate_task = asyncio.create_task(cleanup())
        await asyncio.shield(self._terminate_task)

    async def _matrix_credentials(self) -> tuple[str, str]:
        """Matrix 凭据 (region, token)：优先 OAuth Token（临期自动刷新），回退静态 API Key。

        仅在刷新令牌被服务端明确拒绝时清除登录态；网络等临时故障保留 Token。
        """
        token = self._token_store.load()
        if token and token.is_expired():
            async with self._refresh_lock:
                token = self._token_store.load()  # 双检：可能已被并发请求刷新
                if token and token.is_expired():
                    try:
                        refreshed = await self._oauth.refresh(
                            token.refresh_token,
                            token.region,
                            login_epoch=token.login_epoch,
                        )
                    except OAuthError as e:
                        if e.code in _REFRESH_FATAL_CODES:
                            logger.warning(
                                f"[mmx] 登录态已被服务端拒绝，清除本地 Token: {e}"
                            )
                            self._token_store.clear()
                            token = None
                        else:
                            logger.warning(
                                f"[mmx] OAuth Token 刷新失败（临时故障）: {e}"
                            )
                    else:
                        self._token_store.save(refreshed)
                        return refreshed.region, refreshed.access_token
        if token and not token.is_expired():
            return token.region, token.access_token
        if self._static_keys:
            return self._region, self._static_keys[0]
        return self._region, ""

    async def _refresh_matrix_token(self) -> tuple[str, str]:
        """401/403 后强制刷新 OAuth Token（加锁，防止轮换时并发覆盖）。仅 OAuth 模式有效。"""
        async with self._refresh_lock:
            token = self._token_store.load()
            if not token:
                return self._region, ""
            try:
                refreshed = await self._oauth.refresh(
                    token.refresh_token, token.region, login_epoch=token.login_epoch
                )
            except OAuthError as e:
                if e.code in _REFRESH_FATAL_CODES:
                    self._token_store.clear()
                logger.warning(f"[mmx] 强制刷新 Token 失败: {e}")
                return self._region, ""
            self._token_store.save(refreshed)
            return refreshed.region, refreshed.access_token

    def _ensure_token_login_epoch(self) -> None:
        """为旧登录态持久化一次登录 epoch。"""
        token = self._token_store.load()
        if token and token.ensure_login_epoch():
            self._token_store.save(token)

    def _provider_credentials(self) -> tuple[str, str]:
        """从同一份登录态读取 access token 与登录 epoch。"""
        token = self._token_store.load()
        if not token:
            return "", ""
        return token.access_token, token.login_epoch

    def _provider_token(self) -> str:
        """LLM Provider 当前访问令牌（每次请求读取，令牌轮换即时生效）。"""
        token = self._token_store.load()
        return token.access_token if token else ""

    async def _provider_refresh(self) -> None:
        """Provider 401 时的强制刷新钩子；失败抛 MiniMaxError 由调用方中止重试。"""
        _, token = await self._refresh_matrix_token()
        if not token:
            raise MiniMaxError(ErrorCategory.AUTH, "登录凭据已失效，请重新 /mmx login")

    def _register_llm_provider(self) -> bool:
        """OAuth 登录态存在时注册 MiniMax Code LLM 供应商（幂等，可重复调用）。"""
        if self._llm_provider is not None:
            return True
        token = self._token_store.load()
        if not token:
            return False
        try:
            if token.ensure_login_epoch():
                self._token_store.save(token)
            from .mmx.llm_provider import (
                PROVIDER_ID,
                MiniMaxCodeProvider,
                set_token_source,
            )

            set_token_source(
                self._provider_token,
                self._provider_refresh,
                credentials_getter=self._provider_credentials,
            )
        except Exception as e:
            logger.warning(
                f"[mmx] AstrBot 供应商 API 不可用，跳过 LLM Provider 注册: {e}"
            )
            return False
        provider = MiniMaxCodeProvider(
            region=token.region,
            token_getter=self._provider_token,
            auth_refresher=self._provider_refresh,
            credentials_getter=self._provider_credentials,
        )
        self._llm_provider = provider
        try:
            manager = self.context.provider_manager
            # 卸载旧实例（插件重载场景），避免同 id 重复注册
            manager.provider_insts[:] = [
                p
                for p in manager.provider_insts
                if getattr(p, "provider_config", {}).get("id") != PROVIDER_ID
            ]
            manager.provider_insts.append(provider)
            manager.inst_map[PROVIDER_ID] = provider
        except Exception as e:
            logger.warning(f"[mmx] LLM Provider 注册失败: {e}")
            self._llm_provider = None
            return False
        logger.info(
            f"[mmx] MiniMax Code LLM Provider 已注册（{PROVIDER_ID}），"
            "请在 WebUI 会话/Agent 设置中选择该供应商"
        )
        return True

    def _unregister_llm_provider(self) -> None:
        if self._llm_provider is None:
            return
        try:
            from .mmx.llm_provider import PROVIDER_ID, clear_token_source

            manager = self.context.provider_manager
            manager.provider_insts[:] = [
                p for p in manager.provider_insts if p is not self._llm_provider
            ]
            manager.inst_map.pop(PROVIDER_ID, None)
            clear_token_source()
        except Exception:
            pass

        self._llm_provider = None

    async def _ensure_provider_source(self) -> None:
        """把 mmx_minimax_code 提供商源与默认模型条目写入 AstrBot 配置。

        源 ID 被外部配置占用时不改写并记录告警（工作台弹提示）；
        模型条目只补缺，用户自定义或修改过的条目保持原样。
        """
        try:
            from .mmx.llm_provider import (
                API_BASE_PLACEHOLDER,
                API_KEY_NOTICE,
                DEFAULT_CONTEXT_TOKENS,
                DEFAULT_MODELS,
                MANAGED_BY,
                PROVIDER_SOURCE_ID,
                PROVIDER_TYPE,
            )
        except ImportError:
            # AstrBot 供应商 API 不可用的环境（离线测试桩）直接跳过
            return

        self._provider_source_warning = ""
        try:
            config = self.context.get_config()
            manager = self.context.provider_manager
        except Exception as e:
            logger.warning(f"[mmx] 读取 AstrBot 供应商配置失败，跳过提供商源注册: {e}")
            return

        sources = config.setdefault("provider_sources", [])
        if manager.provider_sources_config is not sources:
            manager.provider_sources_config = sources

        token = self._token_store.load()
        region = token.region if token else "cn"
        existing = next(
            (
                s
                for s in sources
                if isinstance(s, dict) and s.get("id") == PROVIDER_SOURCE_ID
            ),
            None,
        )
        if existing is not None and existing.get("managed_by") != MANAGED_BY:
            self._provider_source_warning = (
                f"提供商源 ID {PROVIDER_SOURCE_ID} 已被其他配置占用，"
                "插件未写入 MiniMax Code 源；请处理重名后重载插件。"
            )
            logger.warning(f"[mmx] {self._provider_source_warning}")
            return

        dirty = False
        region_changed = False
        if existing is None:
            sources.append(
                {
                    "id": PROVIDER_SOURCE_ID,
                    "type": PROVIDER_TYPE,
                    "provider_type": "chat_completion",
                    "provider": "minimax",
                    "enable": True,
                    "key": [API_KEY_NOTICE],
                    "api_base": API_BASE_PLACEHOLDER,
                    "timeout": 120,
                    "proxy": "",
                    "custom_headers": {},
                    "region": region,
                    "managed_by": MANAGED_BY,
                }
            )
            dirty = True
            logger.info(
                "[mmx] 已写入提供商源 mmx_minimax_code（模型供应商页可见，可添加自定义模型）"
            )
        elif existing.get("region") != region:
            existing["region"] = region
            dirty = True
            region_changed = True

        providers = config.setdefault("provider", [])
        for model in DEFAULT_MODELS:
            entry_id = f"{PROVIDER_SOURCE_ID}/{model}"
            if any(isinstance(p, dict) and p.get("id") == entry_id for p in providers):
                continue
            providers.append(
                {
                    "id": entry_id,
                    "enable": True,
                    "provider_source_id": PROVIDER_SOURCE_ID,
                    "model": model,
                    "modalities": ["text", "tool_use"],
                    "custom_extra_body": {},
                    "max_context_tokens": DEFAULT_CONTEXT_TOKENS,
                }
            )
            dirty = True

        if dirty:
            try:
                await config.save_config_async()
            except Exception as e:
                logger.warning(f"[mmx] 提供商源配置落盘失败（重启后仍会生效）: {e}")

        # 仅当 AstrBot 已完成原生供应商初始化（配置中已有条目加载进 inst_map）
        # 才即时补齐/重载；冷启动交给原生 initialize，避免同一模型被加载两次。
        native_loaded = any(
            isinstance(p, dict) and p.get("id") in manager.inst_map for p in providers
        )
        if not native_loaded:
            return
        for model in DEFAULT_MODELS:
            entry_id = f"{PROVIDER_SOURCE_ID}/{model}"
            entry = next(
                (
                    p
                    for p in providers
                    if isinstance(p, dict) and p.get("id") == entry_id
                ),
                None,
            )
            if entry is None or not entry.get("enable", True):
                continue
            if entry_id in manager.inst_map and not region_changed:
                # 已加载且区域未变：无需重复实例化
                continue
            try:
                manager.provider_insts[:] = [
                    p
                    for p in manager.provider_insts
                    if getattr(p, "provider_config", {}).get("id") != entry_id
                ]
                manager.inst_map.pop(entry_id, None)
                await manager.load_provider(dict(entry))
            except Exception as e:
                logger.warning(f"[mmx] 模型 {model} 即时加载失败（重启后生效）: {e}")

    async def _auto_signin_loop(self) -> None:
        """自动签到：启动即巡检一次，此后每 30 分钟一次；上游按天幂等，本地仅省调用。"""
        logger.info("[mmx] 自动签到已开启（每 30 分钟巡检）")
        while True:
            try:
                await self._auto_signin_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[mmx] 自动签到任务异常: {e}")
            await asyncio.sleep(30 * 60)

    async def _auto_signin_once(self) -> None:
        today = _time.strftime("%Y-%m-%d")
        if self._signin_claimed_day == today:
            return
        if not self._token_store.load():
            return
        try:
            result = await self._account.signin_claim()
        except MiniMaxError as e:
            logger.warning(f"[mmx] 自动签到失败: {e}")
            return
        self._signin_claimed_day = today
        logger.info(
            f"[mmx] 自动签到完成：第 {result.get('day_no')} 天，积分 {result.get('points')}"
        )

    @staticmethod
    def _matrix_error_text(exc: MiniMaxError) -> str:
        """AUTH 错误保留 /mmx login 指引，其余走友好文案。"""
        if exc.category is ErrorCategory.AUTH:
            return str(exc)
        return friendly_message(exc)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mmx_group.command("login")
    async def mmx_login(self, event: AstrMessageEvent):
        """登录 MiniMax 账号（OAuth 设备授权），解锁搜图/以图搜图/音乐音效。"""
        session_key = getattr(event, "unified_msg_origin", "") or event.message_str
        if session_key in self._login_sessions:
            yield event.plain_result(
                "当前会话已有进行中的登录流程，请先完成或等待超时。"
            )
            return
        self._login_sessions.add(session_key)
        try:
            async for result in self._run_login_flow(event):
                yield result
        finally:
            self._login_sessions.discard(session_key)

    async def _run_login_flow(self, event: AstrMessageEvent):
        """两阶段登录：1) 会话内选择区域；2) 设备授权 + 自动轮询绑定。"""
        yield event.plain_result(
            "请选择 MiniMax 账号服务区域：\n"
            "1. 国内站 (cn) - https://account.minimax.cn\n"
            "2. 国际站 (global) - https://account.minimax.io\n"
            "输入 1 或 2 继续，输入 c 取消。（60 秒内有效）"
        )
        choice: dict[str, str] = {}
        initiator_id = event.get_sender_id()

        @session_waiter(timeout=60)
        async def region_waiter(controller: SessionController, ev: AstrMessageEvent):
            if ev.get_sender_id() != initiator_id:
                controller.keep(60, reset_timeout=False)  # 群聊中忽略非发起人的回复
                return
            text = ev.message_str.strip().lower()
            if text in {"1", "cn", "国内", "国内站"}:
                choice["region"] = "cn"
            elif text in {"2", "global", "en", "国际", "国际站", "海外"}:
                choice["region"] = "global"
            elif text in {"c", "cancel", "取消"}:
                choice["region"] = ""
            else:
                await ev.send(ev.plain_result("输入无效，请输入 1、2 或 c。"))
                controller.keep(60, reset_timeout=True)
                return
            controller.stop()

        try:
            await region_waiter(event)
        except TimeoutError:
            yield event.plain_result("登录已超时取消。")
            return
        region = choice.get("region")
        if region is None:
            yield event.plain_result("登录流程已结束。")
            return
        if not region:
            yield event.plain_result("已取消登录。")
            return

        try:
            auth_info = await self._oauth.start_device_authorization(region)
        except OAuthError as e:
            yield event.plain_result(f"发起设备授权失败: {e}")
            return
        yield event.plain_result(
            "🔗 请打开链接完成 MiniMax 账号授权：\n"
            f"{auth_info.verification_uri_complete}\n"
            f"🔑 验证码：{auth_info.user_code}\n"
            f"⏳ 有效期：{max(1, auth_info.expires_in // 60)} 分钟\n"
            "授权完成后，Bot 将自动完成登录绑定。"
        )
        try:
            token = await self._oauth.poll_device_token(auth_info, region, max_wait=600)
        except OAuthError as e:
            if e.code == "access_denied":
                yield event.plain_result("❌ 授权已被拒绝。")
            elif e.code == "expired_token":
                yield event.plain_result("⏰ 授权已超时，请重新发起 /mmx login。")
            else:
                yield event.plain_result(f"登录失败: {e}")
            return
        self._token_store.save(token)
        from .mmx.llm_provider import DEFAULT_MODELS

        await self._ensure_provider_source()
        if self._provider_source_warning:
            self._register_llm_provider()
        extra = (
            "\n已在「模型供应商」页注册提供商源 mmx_minimax_code"
            f"（默认模型 {' / '.join(DEFAULT_MODELS)}，可继续添加自定义模型），"
            "会话或 Agent 设置选择后对话走 MiniMax Code 官方通道（可用签到积分）。"
            if not self._provider_source_warning
            else "\n提供商源 ID mmx_minimax_code 已被其他配置占用，已回退为运行时注册，"
            "会话/Agent 设置中仍可直接选择 mmx_minimax_code。"
        )
        yield event.plain_result(
            f"✅ MiniMax 账号登录成功（{'国内站' if region == 'cn' else '国际站'}）！\n"
            "已解锁搜图、以图搜图、AI 音乐与音效生成能力。" + extra
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mmx_group.command("logout")
    async def mmx_logout(self, event: AstrMessageEvent):
        """退出 MiniMax 账号登录并清除本地 Token。"""
        token = self._token_store.load()
        if not token:
            yield event.plain_result("当前未登录 MiniMax 账号。")
            return
        await self._oauth.revoke(token.refresh_token, token.region)
        self._token_store.clear()
        self._unregister_llm_provider()
        yield event.plain_result(
            "已退出 MiniMax 账号登录。\n"
            "若默认聊天模型为 mmx_minimax_code，请在 WebUI 切换其他供应商，"
            "否则对话会因未登录而失败。"
        )

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mmx_group.command("signin")
    async def mmx_signin(self, event: AstrMessageEvent):
        """领取今日 MiniMax Code 签到积分（按天幂等，重复领取不叠加）。"""
        if not self._token_store.load():
            yield event.plain_result(
                "签到需要已登录的 MiniMax 账号，请管理员先发送 /mmx login 完成绑定。"
            )
            return
        try:
            result = await self._account.signin_claim()
        except MiniMaxError as e:
            yield event.plain_result(f"签到失败：{self._matrix_error_text(e)}")
            return
        lines = [
            f"✅ 签到成功（第 {result.get('day_no')} 天），积分 +{result.get('points')}。"
        ]
        try:
            status = await self._account.signin_status()
            days = status.get("days")
            if isinstance(days, list) and days:
                lines.append(
                    f"签到周期共 {len(days)} 天，可用 /mmx usage 查看积分详情。"
                )
        except MiniMaxError:
            pass
        yield event.plain_result("\n".join(lines))

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mmx_group.command("usage")
    async def mmx_usage(self, event: AstrMessageEvent):
        """查询 MiniMax Code 账号积分与套餐余量（需已登录）。"""
        if not self._token_store.load():
            yield event.plain_result(
                "用量查询需要已登录的 MiniMax 账号，请管理员先发送 /mmx login 完成绑定。"
            )
            return
        lines: list[str] = []
        identity = None
        try:
            identity = await self._account.identity()
            lines.append(
                f"账号：{identity.user_name or identity.real_user_id}"
                f"（{identity.email or '邮箱未公开'}）"
            )
        except MiniMaxError as e:
            lines.append(f"账号信息获取失败：{self._matrix_error_text(e)}")

        fallback_text = "未知"
        try:
            pool = await self._account.credit_pool()
            fallback = pool.get("token_plan_credit_fallback_enabled")
            if fallback is not None:
                fallback_text = "已开启" if fallback else "未开启"
        except MiniMaxError as e:
            fallback_text = f"查询失败（{self._matrix_error_text(e)}）"
        balance = identity.credit_balance if identity else ""
        lines.append(f"积分余额：{balance or '未知'}（积分兜底：{fallback_text}）")

        try:
            details = await self._account.credit_details()
            items = details.get("details")
            if isinstance(items, list) and items:
                remain_total = 0.0
                for item in items[:3]:
                    if not isinstance(item, dict):
                        continue
                    expire = item.get("expire_at_ms")
                    expire_text = (
                        _time.strftime("%Y-%m-%d", _time.localtime(expire / 1000))
                        if isinstance(expire, (int, float))
                        else "未标注"
                    )
                    lines.append(
                        f"- 积分条目：剩余 {item.get('remaining_amount', '?')}"
                        f" / {item.get('granted_amount', '?')}，到期 {expire_text}"
                    )
                for item in items:
                    if isinstance(item, dict):
                        try:
                            remain_total += float(item.get("remaining_amount") or 0)
                        except (TypeError, ValueError):
                            pass
                lines.append(f"积分明细：共 {len(items)} 条，剩余合计 {remain_total:g}")
            else:
                lines.append("积分明细：暂无条目")
        except MiniMaxError as e:
            lines.append(f"积分明细查询失败：{self._matrix_error_text(e)}")

        try:
            group_id = identity.group_id if identity else ""
            remains = await self._account.remains(group_id)
            models = remains.get("model_remains")
            if isinstance(models, list) and models:
                for model in models[:5]:
                    if isinstance(model, dict):
                        lines.append(
                            f"- 套餐 {model.get('model_name', '?')}："
                            f"剩余 {model.get('usage_count', '?')} 次"
                        )
            else:
                lines.append("套餐余量：暂无数据")
        except MiniMaxError as e:
            lines.append(f"套餐余量查询失败：{self._matrix_error_text(e)}")

        try:
            status = await self._account.signin_status()
            days = status.get("days")
            if isinstance(days, list):
                today = next(
                    (d for d in days if isinstance(d, dict) and d.get("is_today")), None
                )
                tip = (
                    f"今日第 {today.get('day_no')} 天（{today.get('points')} 积分）"
                    if today
                    else "今日暂无记录"
                )
                lines.append(f"签到：周期 {len(days)} 天，{tip}")
        except MiniMaxError:
            pass
        yield event.plain_result("\n".join(lines))

    @mmx_group.command("search-image")
    async def mmx_search_image(self, event: AstrMessageEvent, *, query: str = ""):
        """搜图并下载。用法: /mmx search-image <关键词> [--n 数量]"""
        # AstrBot 按空格切分参数，query 只有第一个词；优先取消息原文尾部
        parts = event.message_str.strip().split(maxsplit=2)
        raw_args = (parts[2] if len(parts) > 2 else "").strip() or (query or "").strip()
        if not raw_args:
            yield event.plain_result("用法: /mmx search-image <关键词> [--n 数量]")
            return
        try:
            args = parse_image_search_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        try:
            resp = await search_and_download(
                self._matrix,
                self._matrix_media,
                str(self._cache_dir),
                args.query,
                args.n,
            )
        except MiniMaxError as e:
            yield event.plain_result(self._matrix_error_text(e))
            return
        except Exception as e:
            logger.error(f"[mmx] 搜图失败: {e}")
            yield event.plain_result(f"搜图失败: {e}")
            return
        if not resp.get("ok"):
            yield event.plain_result(resp.get("error") or "未找到相关图片。")
            return
        if resp.get("file_paths"):
            for path in resp["file_paths"]:
                yield event.image_result(path)
            return
        yield event.plain_result(
            "图片下载失败，可直接访问：\n" + "\n".join(resp.get("image_urls", []))
        )

    @mmx_group.command("reverse-image")
    async def mmx_reverse_image(self, event: AstrMessageEvent, *, image: str = ""):
        """以图搜图。用法: /mmx reverse-image [图片URL]（可附带或引用图片）"""
        # AstrBot 按空格切分参数，image 只有第一个词；优先取消息原文尾部
        parts = event.message_str.strip().split(maxsplit=2)
        image_input = (parts[2] if len(parts) > 2 else "").strip() or (
            image or ""
        ).strip()
        trusted_local = False
        if not image_input:
            extracted, saw_image = await extract_image_input(
                event.get_messages(),
                image_type=Comp.Image,
                reply_type=Comp.Reply,
                event=event,
            )
            if not extracted:
                if saw_image:
                    yield event.plain_result(
                        "检测到了图片，但当前无法在本地解析。请直接发送图片或提供图片 URL 后重试。"
                    )
                else:
                    yield event.plain_result(
                        "请附带/引用一张图片，或提供图片 URL：/mmx reverse-image <图片URL>"
                    )
                return
            image_input = extracted
            trusted_local = True  # 聊天图片已落盘 AstrBot 临时目录，属可信来源
        try:
            image_url = await self._reverse_image_tool.resolve_image_url(
                image_input, trusted_local=trusted_local
            )
            items = await self._matrix.image_reverse_search(image_url)
        except MediaTransferError as e:
            yield event.plain_result(str(e))
            return
        except MiniMaxError as e:
            yield event.plain_result(self._matrix_error_text(e))
            return
        except Exception as e:
            logger.error(f"[mmx] 以图搜图失败: {e}")
            yield event.plain_result(f"以图搜图失败: {e}")
            return
        if not items:
            yield event.plain_result("未找到相似图片或出处。")
            return
        yield event.plain_result(format_reverse_results(items))

    @mmx_group.command("music")
    async def mmx_music(self, event: AstrMessageEvent, *, prompt: str = ""):
        """AI 音乐生成。用法: /mmx music <提示词> [--lyrics 歌词] [--format mp3]"""
        # AstrBot 按空格切分参数，prompt 只有第一个词；优先取消息原文尾部
        parts = event.message_str.strip().split(maxsplit=2)
        raw_args = (parts[2] if len(parts) > 2 else "").strip() or (
            prompt or ""
        ).strip()
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx music <提示词> [--lyrics 歌词] [--format mp3]"
            )
            return
        try:
            args = parse_music_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        yield event.plain_result("🎵 音乐生成中（可能需要几分钟）...")
        try:
            item = await self._matrix.batch_text_to_music(
                args.prompt, args.lyrics, args.audio_format
            )
        except MiniMaxError as e:
            yield event.plain_result(self._matrix_error_text(e))
            return
        except Exception as e:
            logger.error(f"[mmx] 音乐生成失败: {e}")
            yield event.plain_result(f"音乐生成失败: {e}")
            return
        async for result in self._deliver_audio(
            event, item, f"mmx_music_{int(_time.time() * 1000)}.{args.audio_format}"
        ):
            yield result

    @mmx_group.command("sfx")
    async def mmx_sfx(self, event: AstrMessageEvent, *, prompt: str = ""):
        """AI 音效生成。用法: /mmx sfx <音效描述>"""
        # AstrBot 按空格切分参数，prompt 只有第一个词；优先取消息原文尾部
        parts = event.message_str.strip().split(maxsplit=2)
        text = (parts[2] if len(parts) > 2 else "").strip() or (prompt or "").strip()
        if not text.strip():
            yield event.plain_result("用法: /mmx sfx <音效描述>")
            return
        yield event.plain_result("🔊 音效生成中（可能需要几分钟）...")
        try:
            item = await self._matrix.batch_text_to_audio(text.strip())
        except MiniMaxError as e:
            yield event.plain_result(self._matrix_error_text(e))
            return
        except Exception as e:
            logger.error(f"[mmx] 音效生成失败: {e}")
            yield event.plain_result(f"音效生成失败: {e}")
            return
        async for result in self._deliver_audio(
            event, item, f"mmx_sfx_{int(_time.time() * 1000)}.mp3"
        ):
            yield result

    async def _deliver_audio(self, event: AstrMessageEvent, item: dict, filename: str):
        """下载生成音频到临时目录并以语音组件发送，失败回退链接。"""
        output_url = str(item.get("output_url") or "")
        if not output_url:
            yield event.plain_result("生成完成，但服务端未返回音频地址。")
            return
        dest = self._cache_dir / filename
        try:
            path = await self._matrix_media.download_to_file(
                output_url, dest, allowed_dir=str(self._cache_dir)
            )
        except Exception as e:
            logger.warning(f"[mmx] 音频下载失败: {e}")
            yield event.plain_result(f"音频下载失败，可直接访问：\n{output_url}")
            return
        yield event.chain_result([Record(file=str(path))])

    async def _handle_speech_transcribe(self, event: AstrMessageEvent, raw_args: str):
        try:
            args = parse_transcription_command(raw_args)
            options = TranscriptionOptions(
                model=args.model or self._transcription.default_model,
                response_format=args.response_format,
                language=args.language,
                timestamp_level=args.timestamp_level,
                stream=args.stream,
            )
            result = await transcribe_event(
                self._transcription, event, options, file=args.file, out=args.out
            )
        except MiniMaxError as exc:
            yield event.plain_result(friendly_message(exc))
            return
        except (ValueError, OSError) as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.warning(f"[mmx] 转写失败: {exc}")
            yield event.plain_result(
                "转写未完成或状态未确认，请勿自动重复提交计费请求。"
            )
            return
        if result.get("warning"):
            yield event.plain_result(result["warning"])
        if result.get("file_path"):
            path = result["file_path"]
            yield event.chain_result([Comp.File(name=Path(path).name, file=path)])
            return
        data = result["data"]
        if isinstance(data, dict):
            content = (
                json.dumps(data, ensure_ascii=False)
                if args.response_format == "verbose_json"
                else data["text"]
            )
        else:
            content = data
        content = content or "未识别到语音。"
        for offset in range(0, len(content), 3500):
            yield event.plain_result(content[offset : offset + 3500])

    @mmx_group.command("speech")
    async def mmx_speech(self, event: AstrMessageEvent, *, text: str = ""):
        """语音合成，或用 transcribe/recognize 子命令转写音频。"""
        raw_args = text
        if not raw_args:
            parts = event.message_str.strip().split(maxsplit=2)
            if (
                len(parts) >= 2
                and parts[0].lstrip("/") == "mmx"
                and parts[1] == "speech"
            ):
                raw_args = parts[2] if len(parts) > 2 else ""
        parts = raw_args.strip().split(maxsplit=1)
        action = parts[0] if parts else ""
        rest = parts[1] if len(parts) > 1 else ""
        if action in {"transcribe", "recognize"}:
            async for result in self._handle_speech_transcribe(event, rest):
                yield result
            return
        try:
            args = parse_speech_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx speech <文本> [--voice <音色>] [--format mp3]\n"
                "例如: /mmx speech 你好世界 --speed 1.1 --subtitles"
            )
            return
        try:
            result = await self._speech.synthesize(
                text=args.text,
                model=args.model or self._default_speech_model,
                voice=(
                    args.voice
                    or self._default_speech_voice
                    or "English_expressive_narrator"
                ),
                speed=args.speed,
                volume=args.volume,
                pitch=args.pitch,
                emotion=args.emotion,
                text_normalization=args.text_normalization,
                latex_read=args.latex_read,
                audio_format=args.audio_format,
                sample_rate=args.sample_rate,
                bitrate=args.bitrate,
                channels=args.channels,
                language=args.language,
                subtitles=args.subtitles,
                pronunciation=args.pronunciation,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Speech error: {e}")
            yield event.plain_result(f"语音合成失败: {e}")
            return

        out_path = (
            self._cache_dir
            / f"mmx_speech_{int(_time.time() * 1000)}.{args.audio_format}"
        )
        try:
            saved_path = await self._speech.save(result, str(out_path))
        except Exception as e:
            logger.warning(f"[mmx] 语音保存失败: {e}")
            yield event.plain_result("语音合成完成，但保存音频失败。")
            return

        if saved_path:
            yield event.chain_result([Record(file=saved_path)])

    @filter.permission_type(filter.PermissionType.ADMIN)
    @mmx_group.command("file")
    async def mmx_file(self, event: AstrMessageEvent, *, args: str = ""):
        """文件管理。用法: /mmx file upload|list|delete"""
        raw_args = args or event.message_str
        try:
            parsed = parse_file_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return

        try:
            if parsed.action == "upload":
                safe_file = resolve_existing_data_path(
                    str(self._plugin_data_dir),
                    parsed.file or "",
                    self._extra_dirs,
                )
                if safe_file is None:
                    allowed = [f"- {self._plugin_data_dir}"]
                    allowed.extend(f"- {d}" for d in self._extra_dirs)
                    yield event.plain_result(
                        "file 必须位于以下目录内：\n" + "\n".join(allowed)
                    )
                    return
                if not safe_file.is_file():
                    yield event.plain_result(f"文件不存在: {parsed.file}")
                    return
                result = await self._files.upload(
                    str(safe_file),
                    purpose=parsed.purpose,
                )
                item = _file_payload(result)
                file_id = item.get("file_id") or item.get("id") or "未知"
                filename = item.get("filename") or item.get("name") or parsed.file
                lines = [
                    "文件上传完成。",
                    f"file_id: {file_id}",
                    f"filename: {filename}",
                    f"purpose: {item.get('purpose') or parsed.purpose}",
                ]
                if "bytes" in item:
                    lines.append(f"size: {_file_size_text(item.get('bytes'))}")
                yield event.plain_result("\n".join(lines))
                return

            if parsed.action == "list":
                result = await self._files.list()
                files = result.get("files", [])
                if not files:
                    yield event.plain_result("暂无已上传文件。")
                    return
                lines = ["MiniMax 文件列表:"]
                for item in files[:20]:
                    file_id = item.get("file_id") or item.get("id") or "未知"
                    filename = item.get("filename") or item.get("name") or "未知文件"
                    purpose = item.get("purpose") or "-"
                    size = _file_size_text(item.get("bytes"))
                    lines.append(f"- {file_id} | {filename} | {purpose} | {size}")
                if len(files) > 20:
                    lines.append(f"... 还有 {len(files) - 20} 个文件未显示")
                yield event.plain_result("\n".join(lines))
                return

            result = await self._files.delete(parsed.file_id or "")
            file_id = result.get("file_id") or parsed.file_id
            yield event.plain_result(f"文件已删除: {file_id}")
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
        except Exception as e:
            logger.error(f"[mmx] File command error: {e}")
            yield event.plain_result(f"文件操作失败: {e}")

    @mmx_group.command("image")
    async def mmx_image(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成图片。用法: /mmx image <描述> [--aspect-ratio 16:9] [--seed 42]"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = prompt or (parts[1] if len(parts) > 1 else "")
        try:
            args = parse_image_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx image <描述> [--aspect-ratio 16:9] [--seed 42]\n"
                "例如: /mmx image a cute cat --aigc-watermark"
            )
            return
        try:
            messages = event.get_messages()
            subject_ref_value = args.subject_ref
            subject_ref_trusted = False
            if subject_ref_value == "":
                subject_refs, _ = await extract_image_inputs(
                    messages,
                    image_type=Comp.Image,
                    reply_type=Comp.Reply,
                    event=event,
                    limit=1,
                )
                subject_ref_value = subject_refs[0] if subject_refs else None
                subject_ref_trusted = bool(subject_ref_value)
                if not subject_ref_value:
                    yield event.plain_result(
                        "请为 --subject-ref 提供图片 URL 或插件数据目录内路径，或附带/引用一张图片后重试。"
                    )
                    return
            subject_reference = (
                await resolve_subject_reference(
                    subject_ref_value,
                    data_dir=None
                    if subject_ref_trusted
                    else str(self._plugin_data_dir),
                    allow_trusted_local_path=subject_ref_trusted,
                    extra_allowed_dirs=self._extra_dirs,
                )
                if subject_ref_value
                else None
            )
            result = await self._image.generate(
                prompt=args.prompt,
                model=args.model or self._default_image_model or None,
                n=args.n,
                aspect_ratio=args.aspect_ratio,
                width=args.width,
                height=args.height,
                response_format=args.response_format,
                prompt_optimizer=args.prompt_optimizer,
                aigc_watermark=args.aigc_watermark,
                subject_reference=subject_reference,
                seed=args.seed,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Image error: {e}")
            yield event.plain_result(f"图片生成失败: {e}")
            return

        data = result.get("data", {})
        urls = data.get("image_urls", [])
        base64_images = data.get("image_base64", [])
        if not urls and not base64_images:
            yield event.plain_result("图片生成失败：未返回链接。")
            return

        saved = []
        try:
            saved = await self._image.save(result, out_dir=str(self._cache_dir))
        except Exception as e:
            logger.warning(f"[mmx] 图片下载失败，将尝试直接发送远程图片: {e}")

        if saved:
            yield event.image_result(saved[0])
            return

        if urls:
            yield event.image_result(urls[0])

    @mmx_group.command("video")
    async def mmx_video(self, event: AstrMessageEvent, *, prompt: str = ""):
        """生成视频。用法: /mmx video <描述> [--first-frame <图片>] [--no-wait]"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        raw_args = prompt or (parts[1] if len(parts) > 1 else "")
        try:
            args = parse_video_command(raw_args)
        except DirectCommandError as e:
            yield event.plain_result(str(e))
            return
        if not raw_args:
            yield event.plain_result(
                "用法: /mmx video <描述> [--first-frame <图片>] [--no-wait]"
            )
            return
        try:
            first_frame_ref = args.first_frame
            last_frame_ref = args.last_frame
            subject_image_ref = args.subject_image
            first_frame_trusted = False
            last_frame_trusted = False
            subject_image_trusted = False
            frame_flags_present = (
                args.first_frame is not None or args.last_frame is not None
            )
            image_refs: list[str] = []
            if any(
                value == ""
                for value in (args.first_frame, args.last_frame, args.subject_image)
            ):
                image_refs, _ = await extract_image_inputs(
                    event.get_messages(),
                    image_type=Comp.Image,
                    reply_type=Comp.Reply,
                    event=event,
                    limit=2,
                )
            if args.subject_image == "":
                if frame_flags_present:
                    yield event.plain_result(
                        "--subject-image 不能与 --first-frame/--last-frame 同时使用"
                    )
                    return
                if image_refs:
                    subject_image_ref = image_refs.pop(0)
                    subject_image_trusted = True
                else:
                    yield event.plain_result(
                        "请为 --subject-image 提供图片 URL 或插件数据目录内路径，或附带/引用图片后重试。"
                    )
                    return
            if args.first_frame == "":
                if image_refs:
                    first_frame_ref = image_refs.pop(0)
                    first_frame_trusted = True
                if not first_frame_ref:
                    yield event.plain_result(
                        "请为 --first-frame 提供图片 URL 或插件数据目录内路径，或附带/引用图片后重试。"
                    )
                    return
            if args.last_frame == "":
                if image_refs:
                    last_frame_ref = image_refs.pop(0)
                    last_frame_trusted = True
                if not last_frame_ref:
                    yield event.plain_result(
                        "请为 --last-frame 提供图片 URL 或插件数据目录内路径，或附带/引用图片后重试。"
                    )
                    return
            if args.subject_image is not None and frame_flags_present:
                yield event.plain_result(
                    "--subject-image 不能与 --first-frame/--last-frame 同时使用"
                )
                return
            if last_frame_ref and not first_frame_ref:
                yield event.plain_result("--last-frame 需要同时提供 --first-frame")
                return
            first_frame = (
                await resolve_image(
                    first_frame_ref,
                    data_dir=None
                    if first_frame_trusted
                    else str(self._plugin_data_dir),
                    allow_trusted_local_path=first_frame_trusted,
                    extra_allowed_dirs=self._extra_dirs,
                )
                if first_frame_ref
                else None
            )
            last_frame = (
                await resolve_image(
                    last_frame_ref,
                    data_dir=None if last_frame_trusted else str(self._plugin_data_dir),
                    allow_trusted_local_path=last_frame_trusted,
                    extra_allowed_dirs=self._extra_dirs,
                )
                if last_frame_ref
                else None
            )
            subject_reference = (
                [
                    {
                        "type": "character",
                        "image": [
                            await resolve_image(
                                subject_image_ref,
                                data_dir=None
                                if subject_image_trusted
                                else str(self._plugin_data_dir),
                                allow_trusted_local_path=subject_image_trusted,
                                extra_allowed_dirs=self._extra_dirs,
                            )
                        ],
                    }
                ]
                if subject_image_ref
                else None
            )
            selected_model = (
                args.model
                or (
                    self._default_video_sef_model
                    if first_frame and last_frame
                    else self._default_video_subject_model
                    if subject_reference
                    else self._default_video_model
                )
                or None
            )
            is_v2_cmd = selected_model == "MiniMax-H3"
            if is_v2_cmd and subject_reference:
                yield event.plain_result(
                    "MiniMax-H3 不支持角色一致性（S2V）。请改用参考图片，或移除 --subject-image。"
                )
                return
            if is_v2_cmd:
                result = await self._video.generate(
                    prompt=args.prompt,
                    model=selected_model,
                    first_frame_image=first_frame,
                    last_frame_image=last_frame,
                    duration=args.duration,
                    ratio=args.ratio,
                    callback_url=args.callback_url,
                )
            else:
                result = await self._video.generate(
                    prompt=args.prompt,
                    model=selected_model,
                    first_frame_image=first_frame,
                    last_frame_image=last_frame,
                    subject_reference=subject_reference,
                    callback_url=args.callback_url,
                )
            task_id = result.get("task_id", "")
            if not task_id:
                yield event.plain_result("视频任务提交失败：未返回任务 ID。")
                return
            if args.no_wait:
                yield event.plain_result(
                    f"视频任务已提交。task_id={task_id}，可稍后查询生成结果。"
                )
                return

            yield event.plain_result(f"⏳ 视频生成中（最长 {self._video_timeout}s）...")
            try:
                final = await self._video.wait_for_completion(
                    task_id,
                    poll_interval=args.poll_interval or self._video_poll_interval,
                    timeout=self._video_timeout,
                    model=selected_model,
                )
                fid = final.get("file_id", "")
                v2_url = final.get("video_url", "")
                if not fid and not v2_url:
                    yield event.plain_result("视频生成完成，但未返回可下载文件。")
                    return

                try:
                    safe_task_id = (
                        task_id
                        if re.fullmatch(r"[A-Za-z0-9_-]+", str(task_id))
                        else uuid.uuid4().hex
                    )
                    video_path = str(self._cache_dir / f"mmx_video_{safe_task_id}.mp4")
                    saved = await self._video.download(
                        fid, video_path, video_url=v2_url or None
                    )
                    yield event.chain_result([Video(file=saved)])
                except Exception as e:
                    logger.warning(f"[mmx] 视频下载失败: {e}")
                    if v2_url:
                        yield event.chain_result([Video(file=v2_url)])
                    else:
                        yield event.plain_result(
                            "视频生成完成，但下载失败，无法直接发送视频。"
                        )
            except TimeoutError:
                yield event.plain_result(
                    f"⏰ 视频生成超时。task_id={task_id}，请前往 MiniMax 控制台查看。"
                )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
        except Exception as e:
            logger.error(f"[mmx] Video error: {e}")
            yield event.plain_result(f"视频生成失败: {e}")

    @mmx_group.command("search")
    async def mmx_search(self, event: AstrMessageEvent, *, query: str = ""):
        """联网搜索。用法: /mmx search <查询>"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        query = query or (parts[1] if len(parts) > 1 else "")
        if not query:
            yield event.plain_result(
                "用法: /mmx search <查询>。例如: /mmx search 今天天气"
            )
            return
        try:
            result = await self._search.query(query)
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Search error: {e}")
            yield event.plain_result(f"搜索失败: {e}")
            return

        items = result.get("organic", result.get("data", result.get("results", [])))
        if not items:
            yield event.plain_result("🔍 未找到相关结果。")
            return

        lines = ["🔍 搜索结果:"]
        for i, item in enumerate(items[:5], 1):
            if isinstance(item, dict):
                title = _clean_display_text(item.get("title"), limit=80) or f"结果 {i}"
                url = _clean_display_text(item.get("url") or item.get("link"))
                snippet = _clean_display_text(
                    item.get("snippet")
                    or item.get("summary")
                    or item.get("content")
                    or item.get("description"),
                    limit=180,
                )
                lines.append(f"\n{i}. **{title}**")
                if url:
                    lines.append(f"   {url}")
                if snippet:
                    lines.append(f"   {snippet[:200]}")
        yield event.plain_result("\n".join(lines))

    @mmx_group.command("vision")
    async def mmx_vision(self, event: AstrMessageEvent, *, prompt: str = ""):
        """图片理解。用法: /mmx vision（支持当前消息带图或引用图片）"""
        msg = event.message_str.strip()
        parts = msg.split(maxsplit=1)
        prompt = prompt or (parts[1] if len(parts) > 1 else "Describe the image.")

        image_input, saw_image = await extract_image_input(
            event.get_messages(),
            image_type=Comp.Image,
            reply_type=Comp.Reply,
            event=event,
        )
        if not image_input:
            if saw_image:
                yield event.plain_result(
                    "检测到了图片，但当前无法在本地解析该图片。请改为直接发送图片，或引用一张仍可访问的图片后重试。"
                )
                return
            yield event.plain_result(
                "请附带一张图片，或引用一张图片后再发送 /mmx vision 指令。"
            )
            return

        try:
            result = await self._vision.describe(
                image=image_input,
                prompt=prompt,
                allow_trusted_local_path=True,
            )
        except MiniMaxError as e:
            yield event.plain_result(friendly_message(e))
            return
        except Exception as e:
            logger.error(f"[mmx] Vision error: {e}")
            yield event.plain_result(f"图片理解失败: {e}")
            return

        desc = _extract_vision_text(result)
        yield event.plain_result(f"🖼️ 图片分析:\n\n{desc}")

    @mmx_group.command("quota")
    async def mmx_quota(self, event: AstrMessageEvent, *, index: str = ""):
        """查询 MiniMax API 额度。用法: /mmx quota [序号] 或 /mmx quota page <页码>
        不带参数分页显示 Key 详情。带序号（如 /mmx quota 1）显示指定 Key 详情。
        """
        # 收集所有 Key
        all_keys: list[tuple[int, str]] = []  # (index, key)
        if self._key_pool is not None:
            for s in self._key_pool._states:
                all_keys.append((s.index, s.key))
        else:
            all_keys.append((0, self._client.api_key or ""))

        page_size = 3
        raw_args = _normalize_quota_command_args(index or event.message_str)
        page = 1
        selected_index: int | None = None

        if raw_args:
            parts = raw_args.split()
            command = parts[0].lower()
            if command in {"page", "p"}:
                if len(parts) > 2:
                    yield event.plain_result("用法: /mmx quota page <页码>")
                    return
                if len(parts) == 2:
                    try:
                        page = int(parts[1])
                    except ValueError:
                        yield event.plain_result(
                            "页码无效，请输入数字。如 /mmx quota page 2"
                        )
                        return
                if page < 1:
                    yield event.plain_result("页码必须大于 0。")
                    return
            else:
                try:
                    selected_index = int(raw_args) - 1
                except ValueError:
                    yield event.plain_result(
                        "序号无效，请输入数字。如 /mmx quota 1；翻页用 /mmx quota page 2"
                    )
                    return

        # 指定序号则只查该 Key
        if selected_index is not None:
            filtered = [(i, k) for i, k in all_keys if i == selected_index]
            if not filtered:
                yield event.plain_result(
                    f"Key 序号 {selected_index + 1} 不存在，共 {len(all_keys)} 个 Key。"
                )
                return
            keys_to_check = filtered
        else:
            total_pages = max(1, (len(all_keys) + page_size - 1) // page_size)
            if page > total_pages:
                yield event.plain_result(
                    f"页码 {page} 不存在，共 {total_pages} 页，{len(all_keys)} 个 Key。"
                )
                return
            start = (page - 1) * page_size
            keys_to_check = all_keys[start : start + page_size]

        import asyncio

        async def _fetch(api_key: str):
            try:
                result = await self._quota.info(api_key)
                return result, normalize_quota_models(result.get("model_remains", []))
            except Exception as e:
                logger.warning(f"[mmx] 额度查询失败: {e}")
            return None, []

        tasks = [_fetch(k) for _, k in keys_to_check]
        results = await asyncio.gather(*tasks)

        lines: list[str] = []
        paged_multi_key = selected_index is None and len(all_keys) > 1
        if paged_multi_key:
            total_pages = max(1, (len(all_keys) + page_size - 1) // page_size)
            lines.append(
                f"💰 MiniMax Key 额度（第 {page}/{total_pages} 页，"
                f"每页最多 {page_size} 个，共 {len(all_keys)} 个 Key）:"
            )

        for (ki, key), (raw_result, model_remains) in zip(keys_to_check, results):
            masked = key[:4] + "..." + key[-4:] if len(key) > 8 else "***"
            if paged_multi_key:
                lines.append("")
                lines.append(f"Key [{ki + 1}] {masked}:")
            else:
                lines.append(f"💰 Key [{ki + 1}] {masked} 额度:")
            if (
                isinstance(raw_result, dict)
                and raw_result.get("kind") == "account_balance"
            ):
                lines.extend(_format_account_balance(raw_result))
            elif not model_remains:
                lines.append("查询失败或无额度信息。")
            else:
                for m in model_remains:
                    is_video = is_video_quota_model(m["model"])
                    lines.append(f"- {m['model']}")
                    lines.append(
                        f"  {_format_quota_window(quota_window_label(m['current']), m['current'], is_video=is_video)}"
                    )
                    lines.append(
                        f"  {_format_quota_window('周额度', m['weekly'], is_video=is_video)}"
                    )
        if paged_multi_key:
            total_pages = max(1, (len(all_keys) + page_size - 1) // page_size)
            if page < total_pages:
                lines.append(f"\n下一页: /mmx quota page {page + 1}")
            if page > 1:
                lines.append(f"上一页: /mmx quota page {page - 1}")
        yield event.plain_result("\n".join(lines))
