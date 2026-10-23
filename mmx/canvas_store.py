"""MCode 无限画布文档存储 — CanvasDocumentV1 落盘与 CanvasOperationV1 幂等应用。"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .media_transfer import IMAGE_EXTENSIONS

SCHEMA_VERSION = 1
MAX_NODES = 64
MAX_ANNOTATIONS = 32
MAX_MUTATIONS = 64
MAX_ID_LENGTH = 64
MAX_COMMENT_LENGTH = 4000
MAX_TEXT_LENGTH = 100_000
MAX_PREVIEW_URL_LENGTH = 65_536
MAX_APPLIED_OPERATIONS = 64

_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,%d}" % MAX_ID_LENGTH)
_ACTIONS = frozenset({"comment", "erase"})


def _now_ms() -> int:
    return int(time.time() * 1000)


class CanvasStoreError(ValueError):
    """画布读写或变更校验失败，code 由 Web API 映射为 HTTP 状态。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or f"画布操作被拒绝 ({code})")
        self.code = code


def _identifier(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise CanvasStoreError(
            "invalid_id", f"{label} 只允许字母、数字与 _ . : -（最长 {MAX_ID_LENGTH}）"
        )
    return text


def _number(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    number = float(value)
    return number if math.isfinite(number) else default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _layout(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        "x": round(_number(raw.get("x")), 2),
        "y": round(_number(raw.get("y")), 2),
        "width": round(max(1.0, _number(raw.get("width"), 320.0)), 2),
        "height": round(max(1.0, _number(raw.get("height"), 240.0)), 2),
        "zIndex": int(_number(raw.get("zIndex"))),
    }


def _rect(value: object) -> dict[str, float]:
    raw = value if isinstance(value, dict) else {}
    x = _clamp(_number(raw.get("x")), 0.0, 1.0)
    y = _clamp(_number(raw.get("y")), 0.0, 1.0)
    width = _clamp(_number(raw.get("width"), 0.1), 0.0, 1.0 - x)
    height = _clamp(_number(raw.get("height"), 0.1), 0.0, 1.0 - y)
    return {
        "x": round(x, 6),
        "y": round(y, 6),
        "width": round(width, 6),
        "height": round(height, 6),
    }


def _relative_path(value: object) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text or len(text) > 512:
        raise CanvasStoreError("invalid_path", "relativePath 不能为空且不超过 512 字符")
    parts = [part for part in text.split("/") if part]
    if (
        text.startswith("/")
        or ".." in parts
        or any(part.startswith("~") for part in parts)
    ):
        raise CanvasStoreError(
            "invalid_path", "relativePath 必须是相对路径且不允许 .. 穿越"
        )
    return text


def _text(value: object, limit: int) -> str:
    return str(value or "")[:limit]


def _annotation(value: object) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    action = str(raw.get("action") or "comment").strip().lower()
    created = raw.get("createdAtMs")
    return {
        "id": _identifier(raw.get("id") or uuid.uuid4().hex[:12], "annotation.id"),
        "number": int(max(1, _number(raw.get("number"), 1.0))),
        "normalizedRect": _rect(raw.get("normalizedRect")),
        "comment": _text(raw.get("comment"), MAX_COMMENT_LENGTH),
        "action": action if action in _ACTIONS else "comment",
        "createdAtMs": int(created)
        if isinstance(created, (int, float)) and not isinstance(created, bool)
        else _now_ms(),
    }


def _file(value: object, relative_path: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    item: dict[str, Any] = {"relativePath": relative_path}
    for key in ("assetId", "fileName", "mimeType", "sha256"):
        if raw.get(key) is not None:
            item[key] = _text(raw.get(key), 1024)
    if raw.get("previewUrl") is not None:
        item["previewUrl"] = _text(raw.get("previewUrl"), MAX_PREVIEW_URL_LENGTH)
    if raw.get("textContent") is not None:
        item["textContent"] = _text(raw.get("textContent"), MAX_TEXT_LENGTH)
    size = raw.get("sizeBytes")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size >= 0:
        item["sizeBytes"] = int(size)
    return item


def _fingerprint(mutations: list[object]) -> str:
    """operationId 回执指纹：同一 operationId 复用于不同变更时据此拒绝。"""
    payload = json.dumps(
        mutations, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _supports_annotation(node: dict[str, Any]) -> bool:
    """仅图片节点支持批注，按 fileName（缺省回退 relativePath）的扩展名判定。"""
    file = node["file"]
    name = file.get("fileName") or file["relativePath"]
    return Path(name).suffix.lower() in IMAGE_EXTENSIONS


def _node(value: object) -> dict[str, Any] | None:
    """规范化磁盘上的节点，结构不符时丢弃。"""
    if not isinstance(value, dict):
        return None
    try:
        node_id = _identifier(value.get("id"), "node.id")
        file = _file(
            value.get("file"),
            _relative_path((value.get("file") or {}).get("relativePath")),
        )
    except (CanvasStoreError, AttributeError):
        return None
    annotations = value.get("annotations")
    clean: list[dict[str, Any]] = []
    for item in annotations if isinstance(annotations, list) else []:
        try:
            clean.append(_annotation(item))
        except CanvasStoreError:
            continue
    return {
        "id": node_id,
        "type": "file_ref",
        "file": file,
        "layout": _layout(value.get("layout")),
        "annotations": clean[:MAX_ANNOTATIONS],
    }


def new_document(session_id: str) -> dict[str, Any]:
    """构造空画布文档。"""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "canvasId": f"canvas_{uuid.uuid4().hex[:12]}",
        "sessionId": session_id,
        "changeSeq": 0,
        "nodes": [],
        "updatedAtMs": _now_ms(),
        "appliedOperations": [],
    }


def _sanitize(raw: object, session_id: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return new_document(session_id)
    nodes = raw.get("nodes")
    doc = {
        "schemaVersion": SCHEMA_VERSION,
        "canvasId": _text(raw.get("canvasId"), 64) or f"canvas_{uuid.uuid4().hex[:12]}",
        "sessionId": session_id,
        "changeSeq": int(max(0, _number(raw.get("changeSeq")))),
        "nodes": [
            node
            for node in map(_node, nodes if isinstance(nodes, list) else [])
            if node
        ],
        "updatedAtMs": int(_number(raw.get("updatedAtMs"), _now_ms())),
        "appliedOperations": [
            {"operationId": item["operationId"], "fingerprint": item["fingerprint"]}
            for item in (raw.get("appliedOperations") or [])
            if isinstance(item, dict)
            and isinstance(item.get("operationId"), str)
            and isinstance(item.get("fingerprint"), str)
        ][-MAX_APPLIED_OPERATIONS:],
    }
    doc["nodes"] = doc["nodes"][:MAX_NODES]
    return doc


class CanvasStore:
    """按会话持久化 CanvasDocumentV1，写入原子化并保证操作幂等。

    ``file_exists`` 用于校验 add_file 的 relativePath 指向真实文件，避免持久化悬空引用；
    已落盘节点在读取时不再复查（临时文件可能被清理，节点仍应保留）。
    """

    def __init__(
        self, root: Path | str, *, file_exists: Callable[[str], bool] | None = None
    ) -> None:
        self._root = Path(root)
        self._file_exists = file_exists
        self._lock = asyncio.Lock()

    def _path(self, session_id: str) -> Path:
        if not _ID_RE.fullmatch(str(session_id or "")):
            raise CanvasStoreError(
                "invalid_session", "session_id 只允许字母、数字与 _ . : -"
            )
        return self._root / f"{session_id}.json"

    def _read(self, session_id: str) -> dict[str, Any]:
        path = self._path(session_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return new_document(session_id)
        return _sanitize(raw, session_id)

    def _write(self, session_id: str, doc: dict[str, Any]) -> None:
        path = self._path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex[:8]}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fp:
            fp.write(json.dumps(doc, ensure_ascii=False))
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    async def load(self, session_id: str) -> dict[str, Any]:
        """读取会话画布文档，不存在时返回空文档。"""
        self._path(session_id)
        async with self._lock:
            return await asyncio.to_thread(self._read, session_id)

    async def apply(self, session_id: str, operation: object) -> dict[str, Any]:
        """幂等应用 CanvasOperationV1；重复 operationId 且内容一致时直接返回当前文档。"""
        if not isinstance(operation, dict):
            raise CanvasStoreError("invalid_operation", "operation 必须是对象")
        if operation.get("schemaVersion") != SCHEMA_VERSION:
            raise CanvasStoreError(
                "unsupported_schema", "operation.schemaVersion 必须为 1"
            )
        operation_id = _identifier(operation.get("operationId"), "operationId")
        mutations = operation.get("mutations")
        if not isinstance(mutations, list) or not mutations:
            raise CanvasStoreError("invalid_mutations", "mutations 必须是非空数组")
        if len(mutations) > MAX_MUTATIONS:
            raise CanvasStoreError(
                "too_many_mutations", f"mutations 最多 {MAX_MUTATIONS} 条"
            )

        async with self._lock:
            doc = await asyncio.to_thread(self._read, session_id)
            fingerprint = _fingerprint(mutations)
            receipt = next(
                (
                    item
                    for item in doc["appliedOperations"]
                    if item["operationId"] == operation_id
                ),
                None,
            )
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise CanvasStoreError(
                        "operation_id_reused", "operationId 已用于另一组变更，请使用新的 operationId"
                    )
                return doc
            updated = await asyncio.to_thread(
                self._mutate,
                copy.deepcopy(doc),
                session_id,
                operation_id,
                fingerprint,
                mutations,
            )
            await asyncio.to_thread(self._write, session_id, updated)
            return updated

    def _mutate(
        self,
        doc: dict[str, Any],
        session_id: str,
        operation_id: str,
        fingerprint: str,
        mutations: list[object],
    ) -> dict[str, Any]:
        doc["sessionId"] = session_id
        nodes: list[dict[str, Any]] = doc["nodes"]
        by_id = {node["id"]: node for node in nodes}
        for mutation in mutations:
            if not isinstance(mutation, dict):
                raise CanvasStoreError("invalid_mutation", "mutation 必须是对象")
            kind = str(mutation.get("kind") or "")
            node_id = _identifier(mutation.get("nodeId"), "nodeId")
            if kind == "add_file":
                if node_id in by_id:
                    raise CanvasStoreError(
                        "node_already_exists", f"节点已存在: {node_id}"
                    )
                relative_path = _relative_path(mutation.get("relativePath"))
                if any(node["file"]["relativePath"] == relative_path for node in nodes):
                    raise CanvasStoreError("file_already_on_canvas", "此文件已在画布中")
                if self._file_exists is not None and not self._file_exists(
                    relative_path
                ):
                    raise CanvasStoreError(
                        "file_not_found", f"relativePath 指向的文件不存在: {relative_path}"
                    )
                if len(nodes) >= MAX_NODES:
                    raise CanvasStoreError(
                        "too_many_nodes", f"画布节点最多 {MAX_NODES} 个"
                    )
                node = {
                    "id": node_id,
                    "type": "file_ref",
                    "file": _file(mutation.get("fileMeta"), relative_path),
                    "layout": _layout(mutation.get("layout")),
                    "annotations": [],
                }
                nodes.append(node)
                by_id[node_id] = node
            elif kind == "update_layout":
                node = by_id.get(node_id)
                if node is None:
                    raise CanvasStoreError("node_not_found", f"节点不存在: {node_id}")
                node["layout"] = _layout(mutation.get("layout"))
            elif kind == "remove_node":
                if by_id.pop(node_id, None) is None:
                    raise CanvasStoreError("node_not_found", f"节点不存在: {node_id}")
                nodes[:] = [node for node in nodes if node["id"] != node_id]
            else:
                annotation_input = mutation.get("annotation")
                if kind == "remove_annotation":
                    annotation_id = _identifier(
                        mutation.get("annotationId"), "annotationId"
                    )
                    node = by_id.get(node_id)
                    if node is None:
                        raise CanvasStoreError(
                            "node_not_found", f"节点不存在: {node_id}"
                        )
                    remaining = [
                        item
                        for item in node["annotations"]
                        if item["id"] != annotation_id
                    ]
                    if len(remaining) == len(node["annotations"]):
                        raise CanvasStoreError(
                            "annotation_not_found", f"批注不存在: {annotation_id}"
                        )
                    for index, item in enumerate(remaining):
                        item["number"] = index + 1
                    node["annotations"] = remaining
                    continue
                if kind not in {"add_annotation", "update_annotation"}:
                    raise CanvasStoreError(
                        "unknown_mutation", f"不支持的 mutation.kind: {kind}"
                    )
                node = by_id.get(node_id)
                if node is None:
                    raise CanvasStoreError("node_not_found", f"节点不存在: {node_id}")
                annotation = _annotation(annotation_input)
                existing = next(
                    (
                        item
                        for item in node["annotations"]
                        if item["id"] == annotation["id"]
                    ),
                    None,
                )
                if kind == "add_annotation" and not _supports_annotation(node):
                    raise CanvasStoreError("annotation_unsupported", "仅图片节点支持批注")
                if kind == "add_annotation" and existing is not None:
                    raise CanvasStoreError(
                        "annotation_exists", f"批注已存在: {annotation['id']}"
                    )
                if kind == "update_annotation" and existing is None:
                    raise CanvasStoreError(
                        "annotation_not_found", f"批注不存在: {annotation['id']}"
                    )
                if any(
                    item is not existing and item["number"] == annotation["number"]
                    for item in node["annotations"]
                ):
                    raise CanvasStoreError(
                        "annotation_number_conflict",
                        f"批注序号已被占用: {annotation['number']}",
                    )
                if existing is not None:
                    node["annotations"] = [
                        annotation if item is existing else item
                        for item in node["annotations"]
                    ]
                else:
                    if len(node["annotations"]) >= MAX_ANNOTATIONS:
                        raise CanvasStoreError(
                            "too_many_annotations",
                            f"单节点批注最多 {MAX_ANNOTATIONS} 条",
                        )
                    node["annotations"].append(annotation)
        doc["changeSeq"] = int(doc.get("changeSeq", 0)) + 1
        doc["updatedAtMs"] = _now_ms()
        applied = doc.setdefault("appliedOperations", [])
        applied.append({"operationId": operation_id, "fingerprint": fingerprint})
        doc["appliedOperations"] = applied[-MAX_APPLIED_OPERATIONS:]
        return doc
