"""本插件运行日志与 HTTP 诊断采集（纯内存、实时可调、不改插件配置）。

职责：
1. 在 `astrbot.api.logger` 上挂载轻量 Handler，严格按源码目录（插件根目录）与前缀过滤，
   仅捕获本插件自身的日志；动态切换 DEBUG 档位时保护宿主既有 Handler 等级不受污染，
   并在关闭或切回时完整还原。
2. 接收本插件各 HTTP 客户端（MiniMaxClient / OAuthClient / MatrixMediaClient /
   MiniMaxCodeProvider）上报的请求/响应事件，按运行时开关做敏感信息脱敏、二进制跳过与
   文本截断。
"""

from __future__ import annotations

import json
import logging
import re
import time
import traceback
import uuid
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from astrbot.api import logger

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SELF_FILE = Path(__file__).resolve()

LEVEL_MAP: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_LEVEL_ALIAS: dict[str, str] = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARN": "WARNING",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
}

_SENSITIVE_HEADERS: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "cookie",
        "set-cookie",
        "x-signature",
        "yy",
        "x-auth-token",
        "x-token",
        "x-access-token",
    }
)

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "token",
        "secret",
        "client_secret",
        "password",
        "device_code",
        "code_verifier",
        "authorization",
        "signature",
        "x-signature",
        "yy",
        "inline_data",
        "image_base64",
        "audio_base64",
    }
)

_BINARY_CONTENT_PREFIXES: tuple[str, ...] = (
    "image/",
    "audio/",
    "video/",
    "application/octet-stream",
    "multipart/form-data",
)

_DATA_URI_RE = re.compile(r"(data:[^;,\s]+;base64,)[A-Za-z0-9+/=]{48,}")
_BEARER_RE = re.compile(r"^(Bearer\s+)(.+)$", re.IGNORECASE)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _clamp_int(value: Any, *, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _mask_secret(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    matched = _BEARER_RE.match(text)
    if matched:
        prefix, secret = matched.group(1), matched.group(2).strip()
        return f"{prefix}{_mask_token(secret)}"
    return _mask_token(text)


def _mask_token(secret: str) -> str:
    if len(secret) <= 10:
        return "[REDACTED]"
    return f"{secret[:4]}***{secret[-4:]}"


def sanitize_url(url: str) -> str:
    """对 URL 中的敏感查询参数脱敏，保留路径与常规参数便于排障。"""
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text[:512]
    if not parts.query:
        return text
    cleaned: list[tuple[str, str]] = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered in _SENSITIVE_KEYS or any(
            frag in lowered for frag in ("token", "secret", "key", "sig")
        ):
            cleaned.append((key, "[REDACTED]"))
        else:
            cleaned.append((key, val))
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(cleaned), "")
    )


def sanitize_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """返回脱敏后的 Header 字典。"""
    if not headers:
        return {}
    result: dict[str, str] = {}
    for key, val in headers.items():
        name = str(key)
        raw = str(val)
        if name.lower() in _SENSITIVE_HEADERS:
            result[name] = _mask_secret(raw)
        else:
            result[name] = raw[:400]
    return result


def _redact_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "…"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            lowered = key_str.lower()
            if lowered in _SENSITIVE_KEYS or any(
                frag in lowered for frag in ("secret", "password", "token")
            ):
                if isinstance(item, str) and item:
                    sanitized[key_str] = _mask_token(item)
                else:
                    sanitized[key_str] = "[REDACTED]"
            else:
                sanitized[key_str] = _redact_json_value(item, depth=depth + 1)
        return sanitized
    if isinstance(value, list):
        items = [_redact_json_value(item, depth=depth + 1) for item in value[:40]]
        if len(value) > 40:
            items.append(f"… (+{len(value) - 40} items)")
        return items
    if isinstance(value, str):
        cleaned = _DATA_URI_RE.sub(r"\1[BASE64_OMITTED]", value)
        if len(cleaned) > 600:
            return f"{cleaned[:600]}… ({len(cleaned)} chars)"
        return cleaned
    return value


def sanitize_body(
    payload: Any, *, content_type: str = "", max_chars: int = 1500
) -> str:
    """对请求/响应体做二进制跳过、敏感字段脱敏和长度截断。"""
    if payload is None:
        return ""
    mime = str(content_type or "").split(";")[0].strip().lower()
    if any(mime.startswith(prefix) for prefix in _BINARY_CONTENT_PREFIXES):
        size = len(payload) if isinstance(payload, (bytes, bytearray, str)) else 0
        return f"[binary {mime or 'stream'} {size} B]"

    if isinstance(payload, (bytes, bytearray)):
        if len(payload) == 0:
            return ""
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            return f"[binary {mime or 'bytes'} {len(payload)} B]"
        payload = text

    if isinstance(payload, (dict, list)):
        redacted = _redact_json_value(payload)
        rendered = json.dumps(redacted, ensure_ascii=False, indent=2)
    elif isinstance(payload, str):
        stripped = payload.strip()
        if not stripped:
            return ""
        if stripped.startswith(("{", "[")):
            try:
                parsed = json.loads(stripped)
                rendered = json.dumps(
                    _redact_json_value(parsed), ensure_ascii=False, indent=2
                )
            except ValueError:
                rendered = _DATA_URI_RE.sub(r"\1[BASE64_OMITTED]", stripped)
        else:
            rendered = _DATA_URI_RE.sub(r"\1[BASE64_OMITTED]", stripped)
    else:
        rendered = str(payload)

    if len(rendered) > max_chars:
        return f"{rendered[:max_chars]}\n… [已截断，总长 {len(rendered)} 字符]"
    return rendered


@dataclass
class RuntimeLogSettings:
    """运行时日志与 HTTP 抓包开关（仅驻留内存，不修改插件配置文件）。"""

    plugin_enabled: bool = True
    plugin_level: str = "INFO"
    http_enabled: bool = True
    http_include_headers: bool = False
    http_include_body: bool = False
    http_body_max_chars: int = 1500
    http_only_errors: bool = False
    max_entries: int = 500

    def to_dict(self) -> dict[str, Any]:
        return {
            "plugin_enabled": self.plugin_enabled,
            "plugin_level": self.plugin_level,
            "http_enabled": self.http_enabled,
            "http_include_headers": self.http_include_headers,
            "http_include_body": self.http_include_body,
            "http_body_max_chars": self.http_body_max_chars,
            "http_only_errors": self.http_only_errors,
            "max_entries": self.max_entries,
        }

    def apply_patch(self, patch: Mapping[str, Any]) -> None:
        if "plugin_enabled" in patch:
            self.plugin_enabled = bool(patch["plugin_enabled"])
        if "plugin_level" in patch:
            raw = str(patch["plugin_level"] or "").strip().upper()
            if raw in _LEVEL_ALIAS:
                self.plugin_level = _LEVEL_ALIAS[raw]
        if "http_enabled" in patch:
            self.http_enabled = bool(patch["http_enabled"])
        if "http_include_headers" in patch:
            self.http_include_headers = bool(patch["http_include_headers"])
        if "http_include_body" in patch:
            self.http_include_body = bool(patch["http_include_body"])
        if "http_body_max_chars" in patch:
            self.http_body_max_chars = _clamp_int(
                patch["http_body_max_chars"], default=1500, low=200, high=8000
            )
        if "http_only_errors" in patch:
            self.http_only_errors = bool(patch["http_only_errors"])
        if "max_entries" in patch:
            self.max_entries = _clamp_int(
                patch["max_entries"], default=500, low=50, high=2000
            )


def _normalize_record_level(levelno: int) -> str:
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING:
        return "warn"
    if levelno >= logging.INFO:
        return "info"
    return "debug"


class PluginRecordCapture(logging.Handler):
    """挂载到 `astrbot.api.logger` 的插件作用域捕获器。"""

    def __init__(
        self,
        *,
        plugin_root: Path,
        on_record: Callable[[logging.LogRecord], None],
    ) -> None:
        super().__init__(level=logging.DEBUG)
        self._root = plugin_root.resolve()
        self._on_record = on_record

    def _belongs_to_plugin(self, record: logging.LogRecord) -> bool:
        pathname = getattr(record, "pathname", "") or ""
        if pathname:
            try:
                resolved = Path(pathname).resolve()
                if resolved == _SELF_FILE:
                    return False
                if resolved == self._root or self._root in resolved.parents:
                    return True
            except OSError:
                pass
        message = str(getattr(record, "msg", "") or "")
        return message.startswith(("[mmx]", "[mmx-web]"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if not self._belongs_to_plugin(record):
                return
            self._on_record(record)
        except Exception:  # noqa: BLE001 - 日志捕获本身不得干扰业务路径
            return


class RuntimeLogHub:
    """统一管理本插件日志捕获、HTTP 诊断日志与内存环形缓冲。"""

    def __init__(
        self,
        *,
        plugin_root: Path = PLUGIN_ROOT,
        on_entry: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._root = plugin_root.resolve()
        self._settings = RuntimeLogSettings()
        self._entries: deque[dict[str, Any]] = deque(maxlen=self._settings.max_entries)
        self._on_entry = on_entry
        self._handler: PluginRecordCapture | None = None
        self._attached = False
        self._saved_level: int | None = None
        self._saved_effective: int | None = None
        self._saved_handler_levels: dict[logging.Handler, int] = {}

    @property
    def settings(self) -> RuntimeLogSettings:
        return self._settings

    def attach(self) -> None:
        """将插件日志捕获器挂载到 `astrbot.api.logger`。"""
        if self._attached:
            return
        add_handler = getattr(logger, "addHandler", None)
        if not callable(add_handler):
            return
        self._handler = PluginRecordCapture(
            plugin_root=self._root, on_record=self._handle_plugin_record
        )
        add_handler(self._handler)
        self._attached = True
        self._sync_level_boost()

    def detach(self) -> None:
        """卸载捕获器并恢复宿主 logger 原始等级。"""
        self._restore_level_boost()
        if self._attached and self._handler is not None:
            remove_handler = getattr(logger, "removeHandler", None)
            if callable(remove_handler):
                try:
                    remove_handler(self._handler)
                except Exception:  # noqa: BLE001
                    pass
        self._handler = None
        self._attached = False

    def _sync_level_boost(self) -> None:
        """在不改变宿主既有输出的前提下，按需放开捕获等级（如运行时切到 DEBUG）。"""
        if not self._attached:
            return
        target = LEVEL_MAP.get(self._settings.plugin_level, logging.INFO)
        if not self._settings.plugin_enabled:
            self._restore_level_boost()
            return
        if self._saved_effective is not None and target >= self._saved_effective:
            self._restore_level_boost()
            return
        get_effective = getattr(logger, "getEffectiveLevel", None)
        set_level = getattr(logger, "setLevel", None)
        if not callable(get_effective) or not callable(set_level):
            return
        current_effective = int(get_effective())
        if self._saved_level is None:
            if current_effective <= target:
                return
            self._saved_level = int(getattr(logger, "level", logging.NOTSET))
            self._saved_effective = current_effective
            handlers = list(getattr(logger, "handlers", []) or [])
            for item in handlers:
                if item is self._handler:
                    continue
                item_level = int(getattr(item, "level", logging.NOTSET))
                if item_level < current_effective:
                    self._saved_handler_levels[item] = item_level
                    item.setLevel(current_effective)
        set_level(target)

    def _restore_level_boost(self) -> None:
        if self._saved_level is None:
            return
        set_level = getattr(logger, "setLevel", None)
        if callable(set_level):
            try:
                set_level(self._saved_level)
            except Exception:  # noqa: BLE001
                pass
        for item, original in list(self._saved_handler_levels.items()):
            try:
                item.setLevel(original)
            except Exception:  # noqa: BLE001
                pass
        self._saved_handler_levels.clear()
        self._saved_level = None
        self._saved_effective = None

    def update_settings(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        """实时更新运行时采集参数（不写配置文件）。"""
        old_max = self._settings.max_entries
        self._settings.apply_patch(patch)
        if self._settings.max_entries != old_max:
            self._entries = deque(self._entries, maxlen=self._settings.max_entries)
        self._sync_level_boost()
        return self._settings.to_dict()

    def _push(self, entry: dict[str, Any]) -> dict[str, Any]:
        self._entries.append(entry)
        if self._on_entry is not None:
            try:
                self._on_entry(entry)
            except Exception:  # noqa: BLE001
                pass
        return entry

    def _handle_plugin_record(self, record: logging.LogRecord) -> None:
        if not self._settings.plugin_enabled:
            return
        threshold = LEVEL_MAP.get(self._settings.plugin_level, logging.INFO)
        if record.levelno < threshold:
            return
        message = record.getMessage()
        detail = ""
        if record.exc_info:
            detail = "".join(traceback.format_exception(*record.exc_info)).strip()
            if len(detail) > 3000:
                detail = detail[:3000] + "\n… [异常栈已截断]"
        module_ref = self._relative_location(record)
        source = self._classify_source(record, message)
        self._push(
            {
                "id": f"plg_{uuid.uuid4().hex[:12]}",
                "ts_ms": int(record.created * 1000) if record.created else _now_ms(),
                "category": "plugin",
                "source": source,
                "level": _normalize_record_level(record.levelno),
                "command": message[:240],
                "detail": detail if detail else (message if len(message) > 240 else ""),
                "module": module_ref,
            }
        )

    def _relative_location(self, record: logging.LogRecord) -> str:
        pathname = getattr(record, "pathname", "") or ""
        lineno = getattr(record, "lineno", 0) or 0
        if not pathname:
            return ""
        try:
            rel = Path(pathname).resolve().relative_to(self._root).as_posix()
        except ValueError:
            rel = Path(pathname).name
        return f"{rel}:{lineno}" if lineno else rel

    def _classify_source(self, record: logging.LogRecord, message: str) -> str:
        if message.startswith("[mmx-web]"):
            return "web_api"
        pathname = str(getattr(record, "pathname", "") or "").replace("\\", "/")
        if "/tools/" in pathname:
            return "tools"
        if "/mmx/apis/" in pathname:
            return "apis"
        if "llm_provider" in pathname:
            return "llm"
        if "oauth" in pathname:
            return "oauth"
        if "web_api" in pathname:
            return "web_api"
        return "plugin"

    def record_http(
        self,
        *,
        client: str,
        method: str,
        url: str,
        status_code: int | None = None,
        latency_ms: int | None = None,
        ok: bool | None = None,
        stream: bool = False,
        model: str = "",
        request_headers: Mapping[str, Any] | None = None,
        response_headers: Mapping[str, Any] | None = None,
        request_body: Any = None,
        request_content_type: str = "",
        response_body: Any = None,
        response_content_type: str = "",
        error: str = "",
    ) -> dict[str, Any] | None:
        """记录一条本插件自身的 HTTP 请求/响应日志。"""
        cfg = self._settings
        if not cfg.http_enabled:
            return None
        is_ok = (
            ok
            if ok is not None
            else (not error and status_code is not None and 200 <= status_code < 400)
        )
        if cfg.http_only_errors and is_ok:
            return None

        safe_url = sanitize_url(url)
        parts = urlsplit(safe_url)
        host = parts.netloc
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        method_upper = str(method or "GET").upper()

        if error or (status_code is not None and status_code >= 500):
            level = "error"
        elif status_code is not None and status_code >= 400:
            level = "warn"
        else:
            level = "ok"

        http_meta: dict[str, Any] = {
            "client": client,
            "method": method_upper,
            "url": safe_url,
            "host": host,
            "path": path or "/",
            "status_code": status_code,
            "ok": is_ok,
            "stream": bool(stream),
        }
        if model:
            http_meta["model"] = model
        if error:
            http_meta["error"] = str(error)[:800]
        if cfg.http_include_headers:
            if request_headers:
                http_meta["request_headers"] = sanitize_headers(request_headers)
            if response_headers:
                http_meta["response_headers"] = sanitize_headers(response_headers)
        if cfg.http_include_body:
            req_preview = sanitize_body(
                request_body,
                content_type=request_content_type,
                max_chars=cfg.http_body_max_chars,
            )
            if req_preview:
                http_meta["request_body"] = req_preview
            if stream:
                http_meta["response_body"] = "[SSE / streaming response]"
            else:
                res_preview = sanitize_body(
                    response_body,
                    content_type=response_content_type,
                    max_chars=cfg.http_body_max_chars,
                )
                if res_preview:
                    http_meta["response_body"] = res_preview

        summary_parts = [f"HTTP {status_code}" if status_code is not None else "HTTP ERR"]
        if stream:
            summary_parts.append("stream")
        if model:
            summary_parts.append(f"model={model}")
        if error:
            summary_parts.append(str(error)[:300])

        entry: dict[str, Any] = {
            "id": f"http_{uuid.uuid4().hex[:12]}",
            "ts_ms": _now_ms(),
            "category": "http",
            "source": client or "http",
            "level": level,
            "command": f"{method_upper} {path or safe_url}",
            "detail": " · ".join(summary_parts),
            "http": http_meta,
        }
        if latency_ms is not None:
            entry["latency_ms"] = int(max(0, latency_ms))
        return self._push(entry)

    def record_event(self, entry: dict[str, Any]) -> None:
        """同步一份工作台事件到统一缓冲，便于初始拉取历史。"""
        enriched = dict(entry)
        enriched.setdefault("category", "event")
        self._entries.append(enriched)

    def snapshot(
        self,
        *,
        category: str = "all",
        level: str = "all",
        limit: int = 300,
    ) -> dict[str, Any]:
        """导出当前缓冲区条目、统计与运行时采集配置。"""
        items = list(self._entries)
        counts = {"all": len(items), "plugin": 0, "http": 0, "event": 0, "errors": 0}
        for item in items:
            cat = str(item.get("category") or "event")
            if cat in counts:
                counts[cat] += 1
            if item.get("level") in ("error", "warn"):
                counts["errors"] += 1

        filtered = items
        if category and category != "all":
            filtered = [item for item in filtered if item.get("category") == category]
        if level and level != "all":
            if level == "info":
                filtered = [
                    item for item in filtered if item.get("level") in ("info", "ok")
                ]
            else:
                filtered = [item for item in filtered if item.get("level") == level]

        capped = max(1, min(2000, limit))
        # 最新条目在前，与前端流式展示一致
        ordered = list(reversed(filtered[-capped:]))
        return {
            "entries": ordered,
            "config": self._settings.to_dict(),
            "stats": counts,
        }

    def clear(self, *, category: str = "all") -> int:
        """清空全部或指定分类的内存日志条目。"""
        if not category or category == "all":
            count = len(self._entries)
            self._entries.clear()
            return count
        before = len(self._entries)
        kept = [item for item in self._entries if item.get("category") != category]
        self._entries = deque(kept, maxlen=self._settings.max_entries)
        return before - len(self._entries)
