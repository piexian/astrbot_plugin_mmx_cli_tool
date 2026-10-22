"""MCode 无限画布文档存储 — CanvasDocumentV1 落盘与 CanvasOperationV1 幂等应用。"""

from __future__ import annotations

import asyncio
import copy
import json
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_NODES = 64
MAX_ANNOTATIONS = 32
MAX_MUTATIONS = 64
MAX_ID_LENGTH = 64
MAX_COMMENT_LENGTH = 4000
MAX_TEXT_LENGTH = 100_000
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
    for key in ("assetId", "fileName", "mimeType", "sha256", "previewUrl"):
        if raw.get(key) is not None:
            item[key] = _text(raw.get(key), 1024)
    if raw.get("textContent") is not None:
        item["textContent"] = _text(raw.get("textContent"), MAX_TEXT_LENGTH)
    size = raw.get("sizeBytes")
    if isinstance(size, (int, float)) and not isinstance(size, bool) and size >= 0:
        item["sizeBytes"] = int(size)
    return item


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
            _identifier(item, "operationId")
            for item in (raw.get("appliedOperations") or [])
            if isinstance(item, str)
        ][-MAX_APPLIED_OPERATIONS:],
    }
    doc["nodes"] = doc["nodes"][:MAX_NODES]
    return doc


class CanvasStore:
    """按会话持久化 CanvasDocumentV1，写入原子化并保证操作幂等。"""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
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
        """幂等应用 CanvasOperationV1；重复 operationId 直接返回当前文档。"""
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
            if operation_id in doc["appliedOperations"]:
                return doc
            updated = await asyncio.to_thread(
                self._mutate, copy.deepcopy(doc), session_id, operation_id, mutations
            )
            await asyncio.to_thread(self._write, session_id, updated)
            return updated

    @staticmethod
    def _mutate(
        doc: dict[str, Any], session_id: str, operation_id: str, mutations: list[object]
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
                file = _file(
                    mutation.get("fileMeta"),
                    _relative_path(mutation.get("relativePath")),
                )
                layout = _layout(mutation.get("layout"))
                existing = by_id.get(node_id)
                if existing:
                    existing["file"] = {**existing["file"], **file}
                    existing["layout"] = layout
                else:
                    if len(nodes) >= MAX_NODES:
                        raise CanvasStoreError(
                            "too_many_nodes", f"画布节点最多 {MAX_NODES} 个"
                        )
                    node = {
                        "id": node_id,
                        "type": "file_ref",
                        "file": file,
                        "layout": layout,
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
                if by_id.pop(node_id, None) is not None:
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
                    node["annotations"] = [
                        item
                        for item in node["annotations"]
                        if item["id"] != annotation_id
                    ]
                    continue
                if kind not in {"add_annotation", "update_annotation"}:
                    raise CanvasStoreError(
                        "unknown_mutation", f"不支持的 mutation.kind: {kind}"
                    )
                node = by_id.get(node_id)
                if kind == "update_annotation" and node is None:
                    raise CanvasStoreError("node_not_found", f"节点不存在: {node_id}")
                if node is None:
                    raise CanvasStoreError("node_not_found", f"节点不存在: {node_id}")
                annotation = _annotation(annotation_input)
                exists = any(
                    item["id"] == annotation["id"] for item in node["annotations"]
                )
                if kind == "update_annotation" and not exists:
                    raise CanvasStoreError(
                        "annotation_not_found", f"批注不存在: {annotation['id']}"
                    )
                if exists:
                    node["annotations"] = [
                        annotation if item["id"] == annotation["id"] else item
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
        applied.append(operation_id)
        doc["appliedOperations"] = applied[-MAX_APPLIED_OPERATIONS:]
        return doc
