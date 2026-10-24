"""MCode 工作台 Web API — 把插件能力映射到 AstrBot 插件视图 bridge 路由。

路由前缀 `/astrbot_plugin_mmx_cli_tool/<endpoint>`，前端经
`window.AstrBotPluginView.apiGet/apiPost/upload/download/subscribeSSE` 调用。
"""

from __future__ import annotations

import asyncio
import json
import math
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from astrbot.api import logger
from astrbot.api.web import error_response, file_response, request, stream_response

from .canvas_store import CanvasStore, CanvasStoreError
from .endpoints import OAUTH_CLIENT_ID
from .errors import ErrorCategory, MiniMaxError, friendly_message
from .media_transfer import IMAGE_EXTENSIONS, MediaTransferError, guess_mime_type
from .oauth import DeviceAuthInfo, OAuthError
from .plugin_service import ServiceError
from .quota_usage import (
    is_video_quota_model,
    merge_quota_models,
    normalize_quota_models,
    quota_window_label,
)
from .utils import resolve_data_path

PLUGIN_NAME = "astrbot_plugin_mmx_cli_tool"
API_PREFIX = f"/{PLUGIN_NAME}"
VIEW_NAME = "mcode_dashboard"
EFFORT_LEVELS = ("default", "low", "medium", "high", "xhigh", "max")
REGIONS = ("cn", "global")
DEFAULT_SESSION_ID = "astrbot_active_session"

EVENT_HISTORY = 100
EVENT_QUEUE_SIZE = 200
SSE_KEEPALIVE_SECONDS = 20
MAX_SEARCH_ITEMS = 5
MAX_REVERSE_MATCHES = 10
MAX_ITERATE_NODES = 5
MAX_UPLOAD_IMAGE_BYTES = 20 * 1024 * 1024
LOGIN_SESSION_GRACE_SECONDS = 60.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _text(value: object, limit: int = 2000) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _int_arg(value: object, *, default: int, low: int, high: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _image_ext(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if 1 < len(suffix) <= 5 else ".jpg"


def _session_id(value: object) -> str:
    return _text(value, 64) or DEFAULT_SESSION_ID


def _vision_text(result: Any) -> str:
    """从 VLM 响应中提取可读文本（与指令侧展示保持一致）。"""
    if not isinstance(result, dict):
        return _text(result, 2000)
    for key in ("content", "description", "text"):
        text = _text(result.get(key), 4000)
        if text:
            return text
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            text = _text(message.get("content"), 4000)
            if text:
                return text
    data = result.get("data")
    if isinstance(data, dict):
        for key in ("content", "description", "text"):
            text = _text(data.get(key), 4000)
            if text:
                return text
    return _text(json.dumps(result, ensure_ascii=False), 2000)


class BridgeError(Exception):
    """可直接回给前端的业务错误，携带 HTTP 状态码。"""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def _error_status(exc: BaseException) -> tuple[int, str]:
    """把后端异常映射为 bridge 可识别的 HTTP 状态与文案。"""
    if isinstance(exc, BridgeError):
        return exc.status_code, str(exc)
    if isinstance(exc, ServiceError):
        return 503, "插件服务未就绪或正在关闭，请稍后重试"
    if isinstance(exc, MiniMaxError):
        status = {
            ErrorCategory.AUTH: 401,
            ErrorCategory.QUOTA: 402,
            ErrorCategory.TIMEOUT: 504,
            ErrorCategory.CONTENT_FILTER: 400,
            ErrorCategory.NETWORK: 502,
        }.get(exc.category, 502)
        message = (
            str(exc) if exc.category is ErrorCategory.AUTH else friendly_message(exc)
        )
        return status, message
    if isinstance(exc, OAuthError):
        return (
            401 if exc.code in {"access_denied", "expired_token"} else 502,
            str(exc),
        )
    if isinstance(exc, CanvasStoreError):
        return 400, str(exc)
    if isinstance(exc, MediaTransferError):
        status = 413 if exc.kind == "file_too_large" else 400
        return status, str(exc)
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return 400, str(exc) or "请求参数无效"
    logger.warning("[mmx-web] 未预期的工作台请求异常", exc_info=exc)
    return 500, "服务内部错误，请查看 AstrBot 日志"


@dataclass
class TerminalEvent:
    """SSE 推送的终端日志条目，字段与前端 TerminalLogEntry 一致。"""

    id: str
    ts_ms: int
    source: str
    level: str
    command: str
    detail: str = ""
    latency_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "ts_ms": self.ts_ms,
            "source": self.source,
            "level": self.level,
            "command": self.command,
            "detail": self.detail,
        }
        if self.latency_ms is not None:
            payload["latency_ms"] = self.latency_ms
        return payload


class EventHub:
    """终端事件的发布订阅中心（有界历史 + 每连接队列）。"""

    def __init__(self, history: int = EVENT_HISTORY) -> None:
        self._history: deque[TerminalEvent] = deque(maxlen=history)
        self._subscribers: set[asyncio.Queue[TerminalEvent]] = set()

    def subscribe(self) -> asyncio.Queue[TerminalEvent]:
        queue: asyncio.Queue[TerminalEvent] = asyncio.Queue(maxsize=EVENT_QUEUE_SIZE)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[TerminalEvent]) -> None:
        self._subscribers.discard(queue)

    def publish(
        self,
        *,
        source: str,
        level: str,
        command: str,
        detail: str = "",
        latency_ms: int | None = None,
    ) -> TerminalEvent:
        entry = TerminalEvent(
            id=f"evt_{uuid.uuid4().hex[:12]}",
            ts_ms=_now_ms(),
            source=source,
            level=level,
            command=command[:200],
            detail=_text(detail, 2000),
            latency_ms=latency_ms,
        )
        self._history.append(entry)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(entry)
            except asyncio.QueueFull:
                continue
        return entry

    def history(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self._history]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


@dataclass
class LoginSession:
    """工作台发起的一次设备授权会话。"""

    session_id: str
    region: str
    auth: DeviceAuthInfo
    expires_at: float
    status: str = "pending"
    message: str = ""
    token_expires_at: int | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class MCodeWebAPI:
    """注册并实现 MCode 工作台视图所需的全部 Web API 路由。"""

    ROUTES: tuple[tuple[str, str, list[str], str], ...] = (
        ("status", "_handle_status", ["GET"], "MCode 工作台：插件状态与能力"),
        ("quota", "_handle_quota", ["GET"], "MCode 工作台：Token Plan 额度"),
        (
            "account/usage",
            "_handle_account_usage",
            ["GET"],
            "MCode 工作台：账户积分与套餐余量",
        ),
        ("oauth/start", "_handle_oauth_start", ["POST"], "MCode 工作台：发起设备授权"),
        ("oauth/poll", "_handle_oauth_poll", ["GET"], "MCode 工作台：查询授权进度"),
        (
            "oauth/logout",
            "_handle_oauth_logout",
            ["POST"],
            "MCode 工作台：解绑 OAuth 凭据",
        ),
        (
            "config/effort",
            "_handle_config_effort",
            ["POST"],
            "MCode 工作台：设置推理强度档位",
        ),
        ("canvas/doc", "_handle_canvas_doc", ["GET"], "MCode 工作台：读取画布文档"),
        (
            "canvas/mutate",
            "_handle_canvas_mutate",
            ["POST"],
            "MCode 工作台：应用画布变更",
        ),
        (
            "canvas/iterate",
            "_handle_canvas_iterate",
            ["POST"],
            "MCode 工作台：提交画布批注",
        ),
        (
            "canvas/download",
            "_handle_canvas_download",
            ["GET"],
            "MCode 工作台：下载画布资产",
        ),
        (
            "tools/image-search",
            "_handle_image_search",
            ["POST"],
            "MCode 工作台：搜图并下载",
        ),
        (
            "tools/image-reverse",
            "_handle_image_reverse",
            ["POST"],
            "MCode 工作台：以图搜图",
        ),
        (
            "tools/image-reverse-upload",
            "_handle_image_reverse_upload",
            ["POST"],
            "MCode 工作台：上传图片以图搜图",
        ),
        ("tools/music", "_handle_music", ["POST"], "MCode 工作台：AI 音乐生成"),
        ("tools/sfx", "_handle_sfx", ["POST"], "MCode 工作台：AI 音效生成"),
        (
            "tools/download-temp",
            "_handle_download_temp",
            ["GET"],
            "MCode 工作台：下载临时媒体",
        ),
        ("events", "_handle_events", ["GET"], "MCode 工作台：终端事件流"),
    )

    def __init__(self, plugin: Any) -> None:
        self._plugin = plugin
        self._events = EventHub()
        self._canvas = CanvasStore(
            Path(plugin._plugin_data_dir) / "canvas",
            file_exists=lambda path: (
                self._locate_file(path, include_data=True) is not None
            ),
        )
        self._login_sessions: dict[str, LoginSession] = {}
        self._registered: list[tuple[str, tuple[str, ...]]] = []
        self._closed = False

    @property
    def events(self) -> EventHub:
        return self._events

    # ---------------------------------------------------------------- 生命周期

    def register(self) -> None:
        """批量注册路由；宿主不支持 register_web_api 时降级为仅聊天指令。"""
        context = self._plugin.context
        register = getattr(context, "register_web_api", None)
        if not callable(register):
            logger.warning(
                "[mmx-web] 当前 AstrBot 版本不支持 register_web_api，工作台不可用"
            )
            return
        for endpoint, handler_name, methods, desc in self.ROUTES:
            route = f"{API_PREFIX}/{endpoint}"
            register(route, getattr(self, handler_name), methods, desc)
            self._registered.append((route, tuple(methods)))
        logger.info(f"[mmx-web] 已注册 {len(self._registered)} 条工作台 Web API 路由")

    async def close(self) -> None:
        """注销本实例路由并停止后台轮询与 SSE 连接。"""
        self._closed = True
        context = self._plugin.context
        registered = getattr(context, "registered_web_apis", None)
        if isinstance(registered, list) and self._registered:
            owned = set(self._registered)
            registered[:] = [
                api for api in registered if (api[0], tuple(api[2])) not in owned
            ]
        self._registered.clear()
        self._cancel_login_sessions()

    # ---------------------------------------------------------------- 通用封装

    async def _guarded(
        self,
        source: str,
        command: str,
        action: Callable[[], Awaitable[Any]],
        *,
        summarize: Callable[[dict[str, Any]], str] | None = None,
    ) -> Any:
        """执行一次工作台动作，统一映射错误并写入终端事件。"""
        if self._closed:
            return error_response(
                "插件已卸载或正在关闭，请刷新页面重新加载工作台", status_code=503
            )
        started = time.perf_counter()
        try:
            payload = await action()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 统一转换为 bridge 错误响应
            latency = int((time.perf_counter() - started) * 1000)
            status, message = _error_status(exc)
            self._events.publish(
                source=source,
                level="error",
                command=command,
                detail=message,
                latency_ms=latency,
            )
            logger.warning(f"[mmx-web] {command} 失败: {message}")
            return error_response(message, status_code=status)
        latency = int((time.perf_counter() - started) * 1000)
        detail = ""
        if summarize and isinstance(payload, dict):
            detail = summarize(payload)
        self._events.publish(
            source=source,
            level="ok",
            command=command,
            detail=detail,
            latency_ms=latency,
        )
        return payload

    async def _body(self) -> dict[str, Any]:
        body = await request.json({})
        return body if isinstance(body, dict) else {}

    def _region(self, value: object) -> str:
        region = _text(value, 16).lower()
        return region if region in REGIONS else _text(self._plugin._region, 16) or "cn"

    def _allowed_dirs(self, *, include_data: bool = False) -> list[str]:
        """文件读取白名单根目录：AstrBot 共享临时目录（+ 插件数据目录）。"""
        dirs = [str(self._plugin._cache_dir)]
        if include_data:
            dirs.append(str(self._plugin._plugin_data_dir))
        return dirs

    def _locate_file(self, path: str, *, include_data: bool = False) -> Path | None:
        """在白名单根目录中定位真实存在的文件，找不到时返回 None。"""
        for root in self._allowed_dirs(include_data=include_data):
            candidate = resolve_data_path(root, path)
            if candidate is not None and candidate.is_file():
                return candidate
        return None

    def _serve_file(self, raw_path: object, *, include_data: bool = False) -> Any:
        path = _text(raw_path, 1024)
        if not path:
            raise BridgeError("缺少 path 参数", 400)
        candidate = self._locate_file(path, include_data=include_data)
        if candidate is None:
            raise BridgeError("文件不存在或不在允许的目录内", 404)
        return file_response(candidate, filename=candidate.name)

    # ---------------------------------------------------------------- 状态与额度

    async def _status_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        service_status = plugin._service.get_status()
        state = str(service_status.get("state") or "initializing")
        mapped = {
            "ready": "ready",
            "initializing": "initializing",
            "closing": "closed",
            "closed": "closed",
        }.get(state, "degraded")
        token = plugin._token_store.load()
        auth_mode = "oauth" if token else ("api_key" if plugin._static_keys else "none")
        region = token.region if token else _text(plugin._region, 16) or "cn"
        if token:
            account_label = f"{OAUTH_CLIENT_ID} ({region.upper()})"
        elif plugin._static_keys:
            account_label = f"静态 API Key × {len(plugin._static_keys)}"
        else:
            account_label = "未配置凭据"
        return {
            "api_version": 1,
            "instance_id": service_status.get("instance_id"),
            "state": mapped,
            "ready": mapped == "ready",
            "reason": service_status.get("reason"),
            "region": region,
            "auth_mode": auth_mode,
            "oauth_connected": bool(token),
            "oauth_expires_at": int(token.expires_at) if token else None,
            "account_label": account_label,
            "model": "coding-plan/vlm",
            "thinking_effort": plugin._thinking_effort,
            "provider_source_warning": str(
                getattr(plugin, "_provider_source_warning", "") or ""
            ),
            "capabilities": list(plugin._service.capabilities().get("features", [])),
            "views": [VIEW_NAME],
        }

    @staticmethod
    def _sum_int(windows: list[dict[str, Any]], key: str) -> int | None:
        values = [window.get(key) for window in windows]
        if not values or any(not isinstance(value, int) for value in values):
            return None
        return sum(values)

    @classmethod
    def _bucket(
        cls, windows: list[dict[str, Any]], *, fallback_label: str
    ) -> dict[str, Any]:
        """把多个模型的同类窗口合并成前端的一个额度环形。"""
        usable = [window for window in windows if not window.get("unavailable")]
        sources = usable or windows
        unlimited = bool(usable) and all(window.get("unlimited") for window in usable)
        totals = None if unlimited else cls._sum_int(usable, "total")
        remaining = None if unlimited else cls._sum_int(usable, "remaining")
        used = None if unlimited else cls._sum_int(usable, "used")

        percent: float | None = 100.0 if unlimited else None
        if percent is None and usable:
            weighted = [
                (window["remaining_percent"], window["total"])
                for window in usable
                if isinstance(window.get("remaining_percent"), (int, float))
                and isinstance(window.get("total"), int)
                and window["total"] > 0
            ]
            if weighted:
                denominator = sum(total for _, total in weighted)
                percent = round(
                    sum(value * total for value, total in weighted) / denominator, 1
                )
            else:
                plain = [
                    window["remaining_percent"]
                    for window in usable
                    if isinstance(window.get("remaining_percent"), (int, float))
                ]
                percent = round(sum(plain) / len(plain), 1) if plain else None
        if percent is None:
            percent = 0.0

        if unlimited:
            status = 3
        elif not sources or percent <= 0:
            status = 2
        else:
            status = 1

        resets = [
            window["remains_time"]
            for window in sources
            if isinstance(window.get("remains_time"), int)
            and window["remains_time"] > 0
        ]
        ends = [
            window["end_time"]
            for window in sources
            if isinstance(window.get("end_time"), int)
            and window["end_time"] > _now_ms()
        ]
        reset_at_ms = (
            _now_ms() + min(resets) if resets else (min(ends) if ends else _now_ms())
        )
        label = fallback_label
        for window in sources:
            text = quota_window_label(window)
            if text and text != "当前周期额度":
                label = text
                break
        return {
            "label": label,
            "total": totals if totals is not None else 0,
            "used": used if used is not None else 0,
            "remaining": remaining if remaining is not None else 0,
            "remaining_percent": percent,
            "status": status,
            "reset_at_ms": reset_at_ms,
        }

    async def _quota_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        keys = (
            [state.key for state in plugin._key_pool._states]
            if plugin._key_pool
            else [plugin._client.api_key]
        )
        keys = [key for key in keys if key]
        if not keys:
            raise BridgeError(
                "未配置 API Key，无法查询额度；请先在工作台完成 OAuth 登录或配置 api_key",
                503,
            )
        results = await asyncio.gather(
            *(plugin._quota.info(key) for key in keys), return_exceptions=True
        )
        models: list[dict[str, Any]] = []
        balance: dict[str, Any] | None = None
        failures = 0
        for result in results:
            if not isinstance(result, dict):
                failures += 1
                continue
            if result.get("kind") == "account_balance":
                balance = result
                continue
            models.extend(normalize_quota_models(result.get("model_remains") or []))
        if not models and balance is None:
            raise MiniMaxError(ErrorCategory.GENERAL, "额度查询失败，请稍后重试")

        merged = merge_quota_models(models) if models else {}
        interval_windows = [
            item["current"]
            for name, item in merged.items()
            if not is_video_quota_model(name)
        ]
        video_windows = [
            item["weekly"]
            for name, item in merged.items()
            if is_video_quota_model(name)
        ]
        region = _text(plugin._region, 16) or "cn"
        return {
            "plan_name": (
                f"Token Plan · {'国内站' if region == 'cn' else '国际站'}"
                if merged
                else "账户余额 · 按量计费"
            ),
            "region": region,
            "interval_5h": self._bucket(
                interval_windows, fallback_label="当前周期额度"
            ),
            "weekly_video": self._bucket(video_windows, fallback_label="周额度"),
            "credit_fallback": self._credit_fallback(balance),
            "updated_at_ms": _now_ms(),
            "keys": {"total": len(keys), "failed": failures},
        }

    @staticmethod
    def _amount(balance: dict[str, Any], key: str) -> tuple[float | None, str]:
        value = balance.get(key)
        try:
            number = float(str(value))
        except (TypeError, ValueError):
            return None, _text(value, 32) or "未知"
        return number, f"{number:,.2f}"

    @classmethod
    def _credit_fallback(cls, balance: dict[str, Any] | None) -> dict[str, Any]:
        if not balance:
            return {
                "enabled": False,
                "active_deducting": False,
                "balance": "—",
                "consumed_today": "—",
            }
        owed, _ = cls._amount(balance, "owed_amount")
        credit, _ = cls._amount(balance, "credit_balance")
        _, balance_text = cls._amount(balance, "available_amount")
        _, consumed_text = cls._amount(balance, "consumed_amount")
        return {
            "enabled": True,
            "active_deducting": bool((owed or 0) > 0 or (credit or 0) > 0),
            "balance": balance_text,
            "consumed_today": consumed_text,
        }

    async def _handle_status(self) -> Any:
        return await self._guarded(
            "plan",
            "Dashboard.status",
            self._status_payload,
            summarize=lambda payload: (
                f"状态 {payload['state']} · 凭据 {payload['auth_mode']} · 档位 {payload['thinking_effort']}"
            ),
        )

    async def _handle_quota(self) -> Any:
        return await self._guarded(
            "plan",
            "Quota.summary",
            self._quota_payload,
            summarize=lambda payload: (
                f"Token Plan 双周期刷新 · 周期剩余 {payload['interval_5h']['remaining_percent']}%"
                f" · 视频周额度 {payload['weekly_video']['remaining']}/{payload['weekly_video']['total']}"
            ),
        )

    async def _handle_account_usage(self) -> Any:
        """OAuth 账户的真实积分/套餐余量/签到数据；单项子查询失败仅缺对应块。"""
        plugin = self._plugin
        token = plugin._token_store.load()
        if not token:
            raise BridgeError("未登录 MiniMax 账号，无法查询积分与套餐余量", 503)
        account = plugin._account
        identity = await account.identity()
        details, remains, signin, pool = await asyncio.gather(
            account.credit_details(),
            account.remains(identity.group_id),
            account.signin_status(),
            account.credit_pool(),
            return_exceptions=True,
        )

        def _ok(result: object) -> dict[str, Any]:
            return result if isinstance(result, dict) else {}

        detail_items = [
            item for item in _ok(details).get("details") or [] if isinstance(item, dict)
        ]
        credit_base = 0.0
        credit_bonus = 0.0
        total_remaining = 0.0
        for item in detail_items:
            try:
                amount = float(item.get("remaining_amount") or 0)
            except (TypeError, ValueError):
                continue
            total_remaining += amount
            if item.get("credit_type") == 1:
                credit_base += amount
            else:
                credit_bonus += amount
        days = [d for d in _ok(signin).get("days") or [] if isinstance(d, dict)]
        today = next((d for d in days if d.get("is_today")), None)
        fallback = _ok(pool).get("token_plan_credit_fallback_enabled")
        models = [
            m for m in _ok(remains).get("model_remains") or [] if isinstance(m, dict)
        ]
        payload: dict[str, Any] = {
            "account": {
                "name": identity.user_name,
                "email": identity.email,
                "region": token.region,
            },
            "credit_balance": identity.credit_balance or "",
            "credit_split": (
                {"base": f"{credit_base:g}", "bonus": f"{credit_bonus:g}"}
                if detail_items
                else None
            ),
            "fallback_enabled": None if fallback is None else bool(fallback),
            "credit_details": [
                {
                    "granted_amount": _text(item.get("granted_amount"), 16),
                    "remaining_amount": _text(item.get("remaining_amount"), 16),
                    "expire_at_ms": item.get("expire_at_ms"),
                }
                for item in detail_items[:5]
            ],
            "credit_total_remaining": f"{total_remaining:g}",
            "model_remains": [
                {
                    "model_name": _text(m.get("model_name"), 48),
                    "usage_count": m.get("usage_count"),
                }
                for m in models[:6]
            ],
            "signin": {
                "days_total": len(days),
                "today_day_no": today.get("day_no") if today else None,
                "today_points": today.get("points") if today else None,
                # 实测签到状态：1=未领取（未来/待领取），3=已领取（含今天已签）
                "today_claimed": None if today is None else today.get("status") == 3,
            },
            "updated_at_ms": _now_ms(),
        }
        # Token Plan（OA）用量窗口：与 API Key 模式共用窗口解析
        remains_models = _ok(remains).get("model_remains")
        if remains_models:
            merged = merge_quota_models(normalize_quota_models(remains_models))
            if merged:
                payload["plan_name"] = "Coding Plan 用量"
                payload["interval_5h"] = self._bucket(
                    [
                        item["current"]
                        for name, item in merged.items()
                        if not is_video_quota_model(name)
                    ],
                    fallback_label="当前周期额度",
                )
                payload["weekly_video"] = self._bucket(
                    [
                        item["weekly"]
                        for name, item in merged.items()
                        if is_video_quota_model(name)
                    ],
                    fallback_label="周额度",
                )
        return payload

    # ---------------------------------------------------------------- OAuth

    def _cancel_login_sessions(self) -> None:
        for session in self._login_sessions.values():
            if session.task is not None and not session.task.done():
                session.task.cancel()
        self._login_sessions.clear()

    def _prune_login_sessions(self) -> None:
        deadline = time.time() - LOGIN_SESSION_GRACE_SECONDS
        expired = [
            key
            for key, session in self._login_sessions.items()
            if session.expires_at < deadline
        ]
        for key in expired:
            session = self._login_sessions.pop(key)
            if session.task is not None and not session.task.done():
                session.task.cancel()

    async def _poll_login(self, session: LoginSession) -> None:
        plugin = self._plugin
        command = f"OAuth.poll({session.region})"
        try:
            token = await plugin._oauth.poll_device_token(
                session.auth, session.region, max_wait=session.auth.expires_in
            )
        except OAuthError as exc:
            session.status = (
                "denied"
                if exc.code == "access_denied"
                else "expired"
                if exc.code == "expired_token"
                else "error"
            )
            session.message = str(exc)
            self._events.publish(
                source="oauth", level="warn", command=command, detail=session.message
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 后台轮询失败仅记录状态
            session.status = "error"
            session.message = f"授权轮询失败: {exc}"
            self._events.publish(
                source="oauth", level="error", command=command, detail=session.message
            )
            return
        plugin._token_store.save(token)
        # 与 /mmx login 命令路径一致：登录后同步提供商源 region/默认模型，
        # 避免 UI 登录国际站后 Provider 仍按旧区域请求。
        try:
            await plugin._ensure_provider_source()
            if plugin._provider_source_warning:
                await plugin._register_llm_provider()
        except Exception as exc:  # noqa: BLE001 - 登录已成功，源初始化失败仅记录
            logger.warning(f"[mmx-web] 登录后初始化 LLM 提供商源失败: {exc}")
        session.status = "authorized"
        session.token_expires_at = int(token.expires_at)
        session.message = "授权完成，已保存登录凭据"
        self._events.publish(
            source="oauth",
            level="ok",
            command=f"OAuth.bind({session.region})",
            detail=f"{OAUTH_CLIENT_ID} 设备授权完成，Matrix 工具已切换到 OAuth 凭据",
        )

    async def _oauth_start_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        region = self._region((await self._body()).get("region"))
        auth = await plugin._oauth.start_device_authorization(region)
        self._prune_login_sessions()
        session_id = uuid.uuid4().hex
        session = LoginSession(
            session_id=session_id,
            region=region,
            auth=auth,
            expires_at=time.time() + max(60.0, float(auth.expires_in)),
            message="等待用户在 MiniMax 账号页完成授权",
        )
        session.task = asyncio.create_task(self._poll_login(session))
        self._login_sessions[session_id] = session
        return {
            "session_id": session_id,
            "region": region,
            "user_code": auth.user_code,
            "verification_uri": auth.verification_uri,
            "verification_uri_complete": auth.verification_uri_complete,
            "expires_in": int(auth.expires_in),
            "interval": int(max(3, math.ceil(auth.interval))),
        }

    async def _oauth_poll_payload(self) -> dict[str, Any]:
        session_id = _text(request.query.get("session_id"), 128)
        self._prune_login_sessions()
        session = self._login_sessions.get(session_id)
        if session is None:
            raise BridgeError("授权会话不存在或已结束，请重新发起登录", 404)
        return {
            "session_id": session.session_id,
            "status": session.status,
            "region": session.region,
            "expires_at": session.token_expires_at or int(session.expires_at),
            "message": session.message,
        }

    async def _oauth_logout_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        token = plugin._token_store.load()
        if token:
            await plugin._oauth.revoke(token.refresh_token, token.region)
            plugin._token_store.clear()
        self._cancel_login_sessions()
        return {"ok": True}

    async def _handle_oauth_start(self) -> Any:
        return await self._guarded(
            "oauth",
            "OAuth.deviceAuthorization",
            self._oauth_start_payload,
            summarize=lambda payload: (
                f"发起设备授权 {payload['region'].upper()} · user_code={payload['user_code']}"
            ),
        )

    async def _handle_oauth_poll(self) -> Any:
        return await self._guarded("oauth", "OAuth.poll", self._oauth_poll_payload)

    async def _handle_oauth_logout(self) -> Any:
        return await self._guarded(
            "oauth",
            "OAuth.logout",
            self._oauth_logout_payload,
            summarize=lambda _: "已撤销 OAuth 凭据，Matrix 工具回退静态 API Key",
        )

    # ---------------------------------------------------------------- 配置

    async def _config_effort_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        effort = _text((await self._body()).get("thinking_effort"), 16).lower()
        if effort not in EFFORT_LEVELS:
            raise BridgeError(f"thinking_effort 必须是 {'/'.join(EFFORT_LEVELS)} 之一")
        plugin._thinking_effort = effort
        persisted = False
        config = getattr(plugin, "_config", None)
        if config is not None:
            config["thinking_effort"] = effort
            save_async = getattr(config, "save_config_async", None)
            save = getattr(config, "save_config", None)
            if callable(save_async):
                persisted = bool(await save_async())
            elif callable(save):
                save()
                persisted = True
        return {"ok": True, "thinking_effort": effort, "persisted": persisted}

    async def _handle_config_effort(self) -> Any:
        return await self._guarded(
            "plan",
            "Config.thinkingEffort",
            self._config_effort_payload,
            summarize=lambda payload: f"推理强度档位 -> {payload['thinking_effort']}",
        )

    # ---------------------------------------------------------------- 画布

    async def _canvas_doc_payload(self) -> dict[str, Any]:
        session_id = _session_id(request.query.get("session_id"))
        document = await self._canvas.load(session_id)
        self._events.publish(
            source="canvas",
            level="info",
            command=f"CanvasService.load({session_id})",
            detail=f"CanvasDocumentV1 changeSeq={document['changeSeq']} · {len(document['nodes'])} 个节点",
        )
        return document

    async def _canvas_mutate_payload(self) -> dict[str, Any]:
        body = await self._body()
        session_id = _session_id(body.get("sessionId") or body.get("session_id"))
        operation = body.get("operation")
        document = await self._canvas.apply(session_id, operation)
        mutations = operation.get("mutations") if isinstance(operation, dict) else []
        return {
            "operationId": operation.get("operationId")
            if isinstance(operation, dict)
            else None,
            "document": document,
            "applied": len(mutations) if isinstance(mutations, list) else 0,
        }

    @staticmethod
    def _annotation_lines(node: dict[str, Any], file_name: str) -> list[str]:
        lines: list[str] = []
        annotations = node.get("annotations")
        for annotation in annotations if isinstance(annotations, list) else []:
            if not isinstance(annotation, dict):
                continue
            rect = annotation.get("normalizedRect")
            rect = rect if isinstance(rect, dict) else {}
            comment = _text(annotation.get("comment"), 500)
            action = _text(annotation.get("action"), 16) or "comment"
            lines.append(
                f"[{file_name} #{annotation.get('number')} ({action}) "
                f"rect=({rect.get('x')},{rect.get('y')},{rect.get('width')},{rect.get('height')})]: {comment}"
            )
        return lines

    async def _canvas_iterate_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        body = await self._body()
        nodes = [item for item in (body.get("nodes") or []) if isinstance(item, dict)]
        if not nodes:
            raise BridgeError("没有可提交的画布节点批注")
        instruction = _text(body.get("instruction"), 2000)
        # 归一化坐标只对其所属图片有意义，因此每个节点单独构造提示词
        targets: list[tuple[dict[str, Any], str, str]] = []
        for node in nodes[:MAX_ITERATE_NODES]:
            file_name = (
                _text(node.get("file_name") or node.get("relative_path"), 128)
                or "asset"
            )
            lines = self._annotation_lines(node, file_name)
            if not lines:
                continue
            prompt = "以下是画布上的图片批注，请逐条分析被圈选区域的问题并给出可执行的修改建议：\n"
            prompt += "\n".join(lines)
            if instruction:
                prompt = f"{instruction}\n\n{prompt}"
            targets.append((node, file_name, prompt))
        if not targets:
            raise BridgeError("所选节点没有批注，请先在画布上圈注")
        if not plugin._static_keys:
            raise BridgeError(
                "画布批注分析需要开放平台 API Key，请在插件配置中填写 api_key", 503
            )
        results = await asyncio.gather(
            *(
                plugin._vision.describe(
                    image=_text(node.get("relative_path"), 512),
                    prompt=prompt,
                    data_dir=str(plugin._plugin_data_dir),
                    extra_allowed_dirs=plugin._extra_dirs,
                )
                for node, _, prompt in targets
            ),
            return_exceptions=True,
        )
        entries: list[dict[str, Any]] = []
        for (node, file_name, _), result in zip(targets, results):
            node_id = _text(node.get("node_id"), 64)
            if isinstance(result, BaseException):
                entries.append(
                    {
                        "node_id": node_id,
                        "file_name": file_name,
                        "ok": False,
                        "error": str(result),
                    }
                )
            else:
                entries.append(
                    {
                        "node_id": node_id,
                        "file_name": file_name,
                        "ok": True,
                        "text": _vision_text(result),
                    }
                )
        return {
            "ok": True,
            "submitted": sum(1 for entry in entries if entry["ok"]),
            "prompt_preview": _text(
                "\n\n".join(prompt for _, _, prompt in targets), 2000
            ),
            "results": entries,
            "skipped": len(nodes) - len(targets),
        }

    async def _handle_canvas_doc(self) -> Any:
        return await self._guarded(
            "canvas", "CanvasService.load", self._canvas_doc_payload
        )

    async def _handle_canvas_mutate(self) -> Any:
        return await self._guarded(
            "canvas",
            "CanvasService.apply",
            self._canvas_mutate_payload,
            summarize=lambda payload: (
                f"应用 {payload['applied']} 条变更 · changeSeq={payload['document']['changeSeq']}"
            ),
        )

    async def _handle_canvas_iterate(self) -> Any:
        return await self._guarded(
            "canvas",
            "Canvas.iterate",
            self._canvas_iterate_payload,
            summarize=lambda payload: (
                f"{payload['submitted']} 个节点完成 VLM 批注分析 · prompt {len(payload['prompt_preview'])} 字符"
            ),
        )

    async def _handle_canvas_download(self) -> Any:
        return await self._guarded(
            "canvas",
            "Canvas.download",
            lambda: _await_sync(
                self._serve_file, request.query.get("path"), include_data=True
            ),
        )

    # ---------------------------------------------------------------- 媒体工具

    async def _image_search_payload(self) -> dict[str, Any]:
        plugin = self._plugin
        body = await self._body()
        query = _text(body.get("query"), 300)
        if not query:
            raise BridgeError("缺少 query 关键词")
        count = _int_arg(body.get("count"), default=3, low=1, high=MAX_SEARCH_ITEMS)
        results = await plugin._matrix.images_search_and_download(query)
        entries: list[dict[str, Any]] = []
        if results and isinstance(results[0], dict):
            for item in results[0].get("images") or []:
                if isinstance(item, dict) and _text(item.get("image_url"), 2000):
                    entries.append(item)
                if len(entries) >= count:
                    break
        if not entries:
            raise MiniMaxError(
                ErrorCategory.GENERAL, f"未检索到与「{query}」相关的图片"
            )

        cache_dir = str(plugin._cache_dir)
        stamp = _now_ms()

        async def _download(index: int, url: str) -> str | None:
            dest = Path(cache_dir) / f"mmx_web_search_{stamp}_{index}{_image_ext(url)}"
            try:
                saved = await plugin._matrix_media.download_to_file(
                    url, dest, allowed_dir=cache_dir
                )
                return Path(saved).name
            except Exception as exc:  # noqa: BLE001 - 单张失败保留远程链接
                logger.warning(f"[mmx-web] 搜图下载失败 {url}: {exc}")
                return None

        saved = await asyncio.gather(
            *(
                _download(index, _text(item.get("image_url"), 2000))
                for index, item in enumerate(entries)
            )
        )
        items: list[dict[str, Any]] = []
        for index, (item, local_path) in enumerate(zip(entries, saved)):
            url = _text(item.get("image_url"), 2000)
            domain = _text(item.get("source") or item.get("site"), 128) or (
                urlparse(url).hostname or ""
            )
            items.append(
                {
                    "id": f"search_{stamp}_{index}",
                    "title": _text(item.get("title") or item.get("name"), 200)
                    or f"{query} · #{index + 1}",
                    "image_url": url,
                    "local_path": local_path,
                    "source_domain": domain,
                    "width": item.get("width")
                    if isinstance(item.get("width"), int)
                    else None,
                    "height": item.get("height")
                    if isinstance(item.get("height"), int)
                    else None,
                }
            )
        return {"items": items, "downloaded": sum(1 for path in saved if path)}

    @staticmethod
    def _match(item: Any) -> dict[str, Any]:
        raw = item if isinstance(item, dict) else {}
        return {
            "title": _text(raw.get("title"), 300),
            "link": _text(raw.get("link") or raw.get("url"), 1000),
            "source": _text(raw.get("source") or raw.get("domain"), 200),
            "image_url": _text(raw.get("image_url"), 2000),
            "snippet": _text(raw.get("snippet") or raw.get("description"), 600),
        }

    async def _reverse_matches(self, image_url: str) -> dict[str, Any]:
        items = await self._plugin._matrix.image_reverse_search(image_url)
        return {
            "matches": [
                self._match(item) for item in (items or [])[:MAX_REVERSE_MATCHES]
            ]
        }

    async def _image_reverse_payload(self) -> dict[str, Any]:
        image = _text((await self._body()).get("image"), 1024)
        if not image:
            raise BridgeError("缺少 image 参数")
        resolve = self._plugin._reverse_image_tool.resolve_image_url
        return await self._reverse_matches(await resolve(image))

    async def _image_reverse_upload_payload(self) -> dict[str, Any]:
        try:
            files = await request.files()
        except Exception as exc:  # noqa: BLE001 - 非 multipart 请求体
            raise BridgeError("上传请求必须是 multipart/form-data", 400) from exc
        upload = files.get("file") or files.get("image")
        if upload is None:
            raise BridgeError("未收到上传文件", 400)
        filename = _text(getattr(upload, "filename", ""), 200) or "upload.png"
        suffix = Path(filename).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS or not guess_mime_type(filename).startswith(
            "image/"
        ):
            raise BridgeError(
                f"仅支持图片文件（{'/'.join(sorted(IMAGE_EXTENSIONS))}）", 415
            )
        dest = (
            Path(self._plugin._cache_dir)
            / f"mmx_web_reverse_{uuid.uuid4().hex[:12]}{suffix}"
        )
        try:
            try:
                await upload.save(dest, max_bytes=MAX_UPLOAD_IMAGE_BYTES)
            except MediaTransferError as exc:
                if exc.kind != "file_too_large":
                    raise
                raise BridgeError(
                    f"图片超过 {MAX_UPLOAD_IMAGE_BYTES // 1024 // 1024} MiB 上限", 413
                ) from exc
            resolve = self._plugin._reverse_image_tool.resolve_image_url
            image_url = await resolve(str(dest), trusted_local=True)
            return await self._reverse_matches(image_url)
        finally:
            try:
                dest.unlink(missing_ok=True)
            except OSError:
                pass

    async def _audio_payload(self, *, kind: str) -> dict[str, Any]:
        plugin = self._plugin
        body = await self._body()
        prompt = _text(body.get("prompt"), 2000)
        if len(prompt) < 4:
            raise BridgeError("请输入更具体的音乐或音效描述词")
        audio_format = _text(body.get("format"), 8).lower()
        audio_format = audio_format if audio_format in ("mp3", "wav") else "mp3"
        lyrics = _text(body.get("lyrics"), 4000) if kind == "music" else ""
        if kind == "music":
            data = await plugin._matrix.batch_text_to_music(
                prompt, lyrics or None, audio_format
            )
        else:
            data = await plugin._matrix.batch_text_to_audio(prompt)
            audio_format = "mp3"
        output_url = _text(data.get("output_url"), 2000)
        if not output_url:
            raise MiniMaxError(ErrorCategory.GENERAL, "服务端未返回音频地址")
        cache_dir = str(plugin._cache_dir)
        track_id = f"track_{uuid.uuid4().hex[:12]}"
        dest = Path(cache_dir) / f"mmx_web_{kind}_{track_id}.{audio_format}"
        local_path: str | None = None
        try:
            saved = await plugin._matrix_media.download_to_file(
                output_url, dest, allowed_dir=cache_dir
            )
            local_path = Path(saved).name
        except Exception as exc:  # noqa: BLE001 - 下载失败仍回传远程地址
            logger.warning(f"[mmx-web] 音频下载失败 {output_url}: {exc}")
        return {
            "id": track_id,
            "kind": kind,
            "prompt": prompt,
            "lyrics": lyrics or None,
            "format": audio_format,
            "output_url": output_url,
            "local_path": local_path,
            "created_at_ms": _now_ms(),
            "duration_hint": _text(data.get("duration_hint"), 64)
            or ("AI 编曲合成" if kind == "music" else "AI 拟音音效"),
        }

    async def _handle_image_search(self) -> Any:
        return await self._guarded(
            "matrix_mcp",
            "Matrix.images_search_and_download",
            self._image_search_payload,
            summarize=lambda payload: (
                f"检索 {len(payload['items'])} 张，成功缓存 {payload['downloaded']} 张到 AstrBot 临时目录"
            ),
        )

    async def _handle_image_reverse(self) -> Any:
        return await self._guarded(
            "matrix_mcp",
            "Matrix.image_reverse_search",
            self._image_reverse_payload,
            summarize=lambda payload: f"以图搜图命中 {len(payload['matches'])} 条出处",
        )

    async def _handle_image_reverse_upload(self) -> Any:
        return await self._guarded(
            "matrix_mcp",
            "Matrix.image_reverse_search.upload",
            self._image_reverse_upload_payload,
            summarize=lambda payload: f"上传搜图命中 {len(payload['matches'])} 条出处",
        )

    async def _handle_music(self) -> Any:
        return await self._guarded(
            "matrix_mcp",
            "Matrix.batch_text_to_music",
            lambda: self._audio_payload(kind="music"),
            summarize=lambda payload: (
                f"音乐生成完成 {payload['format']} · "
                f"{'本地缓存' if payload['local_path'] else '仅远程链接'}"
            ),
        )

    async def _handle_sfx(self) -> Any:
        return await self._guarded(
            "matrix_mcp",
            "Matrix.batch_text_to_audio",
            lambda: self._audio_payload(kind="sfx"),
            summarize=lambda payload: (
                f"音效生成完成 mp3 · {'本地缓存' if payload['local_path'] else '仅远程链接'}"
            ),
        )

    async def _handle_download_temp(self) -> Any:
        return await self._guarded(
            "matrix_mcp",
            "Media.downloadTemp",
            lambda: _await_sync(self._serve_file, request.query.get("path")),
        )

    # ---------------------------------------------------------------- SSE

    async def _handle_events(self) -> Any:
        if self._closed:
            return error_response("插件已卸载或正在关闭", status_code=503)
        queue = self._events.subscribe()

        async def stream():
            try:
                yield ": mcode-events\n\n"
                while not self._closed:
                    try:
                        entry = await asyncio.wait_for(
                            queue.get(), timeout=SSE_KEEPALIVE_SECONDS
                        )
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield f"data: {json.dumps(entry.to_dict(), ensure_ascii=False)}\n\n"
            except asyncio.CancelledError:
                raise
            finally:
                self._events.unsubscribe(queue)

        return stream_response(
            stream(),
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


async def _await_sync(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """把同步动作适配成 _guarded 需要的协程。"""
    return func(*args, **kwargs)
