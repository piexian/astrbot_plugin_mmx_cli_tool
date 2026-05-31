"""In-process background task registry for MiniMax tools."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class BackgroundTaskRecord:
    task_id: str
    tool_name: str
    label: str
    status: str
    created_at: float
    updated_at: float
    max_wait_seconds: int
    poll_after_seconds: int
    result: dict[str, Any] | None = None
    error: str | None = None

    def public(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.status != "failed",
            "task_id": self.task_id,
            "tool_name": self.tool_name,
            "label": self.label,
            "status": self.status,
            "created_at": int(self.created_at),
            "updated_at": int(self.updated_at),
            "elapsed_seconds": max(0, int(time.time() - self.created_at)),
            "max_wait_seconds": self.max_wait_seconds,
            "poll_after_seconds": self.poll_after_seconds,
        }
        if self.result is not None:
            payload["result"] = self.result
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass
class BackgroundTaskRegistry:
    max_records: int = 100
    _records: dict[str, BackgroundTaskRecord] = field(default_factory=dict)

    def create(
        self,
        *,
        tool_name: str,
        label: str,
        max_wait_seconds: int,
        poll_after_seconds: int,
    ) -> BackgroundTaskRecord:
        self._prune()
        now = time.time()
        record = BackgroundTaskRecord(
            task_id=uuid.uuid4().hex,
            tool_name=tool_name,
            label=label,
            status="running",
            created_at=now,
            updated_at=now,
            max_wait_seconds=max_wait_seconds,
            poll_after_seconds=poll_after_seconds,
        )
        self._records[record.task_id] = record
        return record

    def get(self, task_id: str) -> BackgroundTaskRecord | None:
        return self._records.get(task_id)

    def complete(self, task_id: str, result_text: str) -> BackgroundTaskRecord | None:
        record = self._records.get(task_id)
        if record is None:
            return None

        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            result = {"ok": True, "text": result_text}

        record.result = result
        record.updated_at = time.time()
        if isinstance(result, dict) and result.get("ok") is False:
            record.status = "failed"
            record.error = str(result.get("error") or "后台任务失败")
        else:
            record.status = "succeeded"
            record.error = None
        return record

    def fail(self, task_id: str, error: str) -> BackgroundTaskRecord | None:
        record = self._records.get(task_id)
        if record is None:
            return None
        record.status = "failed"
        record.error = error
        record.result = {"ok": False, "error": error}
        record.updated_at = time.time()
        return record

    def _prune(self) -> None:
        if len(self._records) < self.max_records:
            return
        for task_id, _ in sorted(
            self._records.items(),
            key=lambda item: item[1].updated_at,
        )[: len(self._records) - self.max_records + 1]:
            self._records.pop(task_id, None)


BACKGROUND_TASKS = BackgroundTaskRegistry()
