"""MiniMax file management FunctionTools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import FunctionTool, logger
from astrbot.core.agent.run_context import ContextWrapper
from astrbot.core.agent.tool import ToolExecResult
from astrbot.core.astr_agent_context import AstrAgentContext

from ..mmx.files import FileAPI
from ..mmx.utils import is_safe_data_path
from .result import tool_result
from .schema import object_parameters, string_param


def _safe_data_file(data_dir: str, file_path: str) -> Path | None:
    if not file_path or not is_safe_data_path(data_dir, file_path):
        return None
    return (Path(data_dir) / file_path).resolve()


def _admin_only_error(context: ContextWrapper[AstrAgentContext]) -> str | None:
    event = getattr(context.context, "event", None)
    if getattr(event, "role", None) == "admin":
        return None
    return json.dumps(
        {
            "ok": False,
            "error": "权限不足",
            "hint": "MiniMax 文件管理工具仅管理员可用。",
        },
        ensure_ascii=False,
    )


@dataclass
class UploadFileTool(FunctionTool):
    """LLM tool: upload a trusted local data-dir file to MiniMax storage."""

    def __init__(self, api: FileAPI, data_dir: str = "."):
        super().__init__(
            name="mmx_file_upload",
            description=(
                "Upload a local file from the plugin data directory to MiniMax storage. "
                "Returns file metadata including file_id."
            ),
            parameters=object_parameters(
                {
                    "file": string_param(
                        "Path to a file under the plugin data directory. Absolute paths and '..' are rejected."
                    ),
                    "purpose": string_param(
                        "File purpose, for example retrieval or vision. Defaults to retrieval."
                    ),
                },
                required=["file"],
            ),
        )
        self._api = api
        self._data_dir = data_dir

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        permission_error = _admin_only_error(context)
        if permission_error:
            return tool_result(permission_error)

        file_path = str(kwargs.get("file") or "").strip()
        safe_path = _safe_data_file(self._data_dir, file_path)
        if safe_path is None:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "file 路径不合法",
                        "hint": f"file 必须位于插件数据目录内：{self._data_dir}",
                    },
                    ensure_ascii=False,
                )
            )

        try:
            result = await self._api.upload(
                str(safe_path),
                purpose=str(kwargs.get("purpose") or "retrieval"),
            )
        except Exception as e:
            logger.error(f"[mmx] 文件上传失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"文件上传失败: {e}"},
                    ensure_ascii=False,
                )
            )

        return tool_result(json.dumps({"ok": True, "data": result}, ensure_ascii=False))


@dataclass
class ListFilesTool(FunctionTool):
    """LLM tool: list uploaded MiniMax files."""

    def __init__(self, api: FileAPI):
        super().__init__(
            name="mmx_file_list",
            description="List uploaded files in MiniMax storage.",
            parameters=object_parameters({}),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        permission_error = _admin_only_error(context)
        if permission_error:
            return tool_result(permission_error)

        try:
            result = await self._api.list()
        except Exception as e:
            logger.error(f"[mmx] 文件列表查询失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"文件列表查询失败: {e}"},
                    ensure_ascii=False,
                )
            )
        return tool_result(json.dumps({"ok": True, "data": result}, ensure_ascii=False))


@dataclass
class DeleteFileTool(FunctionTool):
    """LLM tool: delete an uploaded MiniMax file."""

    def __init__(self, api: FileAPI):
        super().__init__(
            name="mmx_file_delete",
            description="Delete an uploaded file from MiniMax storage by file_id.",
            parameters=object_parameters(
                {
                    "fileId": string_param("File ID to delete."),
                },
                required=["fileId"],
            ),
        )
        self._api = api

    async def call(
        self, context: ContextWrapper[AstrAgentContext], **kwargs
    ) -> ToolExecResult:
        permission_error = _admin_only_error(context)
        if permission_error:
            return tool_result(permission_error)

        file_id = str(kwargs.get("fileId") or "").strip()
        if not file_id:
            return tool_result(
                json.dumps(
                    {
                        "ok": False,
                        "error": "缺少 fileId 参数",
                        "hint": "请提供 mmx_file_list 或上传结果中的 file_id",
                    },
                    ensure_ascii=False,
                )
            )

        try:
            result = await self._api.delete(file_id)
        except Exception as e:
            logger.error(f"[mmx] 文件删除失败: {e}")
            return tool_result(
                json.dumps(
                    {"ok": False, "error": f"文件删除失败: {e}"},
                    ensure_ascii=False,
                )
            )
        return tool_result(json.dumps({"ok": True, "data": result}, ensure_ascii=False))
