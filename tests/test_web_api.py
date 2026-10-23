"""MCode 工作台 Web API 路由测试：契约字段、幂等、白名单与降级（全部 Mock，无真实网络）。"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from support import UploadFile, WebApiContext, plugin_modules, web_request

from astrbot_plugin_mmx_cli_tool.mmx.canvas_store import (
    MAX_APPLIED_OPERATIONS,
    CanvasStoreError,
)
from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError
from astrbot_plugin_mmx_cli_tool.mmx.oauth import (
    DeviceAuthInfo,
    OAuthError,
    OAuthToken,
)

PREFIX = "/astrbot_plugin_mmx_cli_tool"

TEXT_QUOTA_ITEM = {
    "model_name": "general",
    "current_interval_total_count": 100,
    "current_interval_usage_count": 25,
    "current_interval_remaining_count": 75,
    "current_interval_remaining_percent": 75,
    "current_interval_status": 1,
    "remains_time": 3_600_000,
    "start_time": 0,
    "end_time": 43_200_000,
    "current_weekly_total_count": 0,
    "current_weekly_usage_count": 0,
    "current_weekly_status": 3,
}

VIDEO_QUOTA_ITEM = {
    "model_name": "MiniMax-Hailuo-2.3",
    "current_interval_total_count": 0,
    "current_interval_usage_count": 0,
    "current_interval_status": 3,
    "current_weekly_total_count": 50,
    "current_weekly_usage_count": 20,
    "current_weekly_remaining_count": 30,
    "current_weekly_remaining_percent": 60,
    "current_weekly_status": 1,
    "weekly_remains_time": 86_400_000,
    "weekly_start_time": 0,
    "weekly_end_time": 604_800_000,
}


class WebApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main, self.tools = self.enterContext(plugin_modules(self.root))
        self.context = WebApiContext()
        self.plugin = self.main.Main(
            self.context,
            {"api_key": ["sk-test-key"], "region": "cn", "thinking_effort": "high"},
        )
        await self.plugin.initialize()
        self.addAsyncCleanup(self.plugin.terminate)
        self.web = self.plugin._web_api
        web_request.reset()

    async def call(self, endpoint: str):
        return await self.context.handler(f"{PREFIX}/{endpoint}")()

    def touch_cache(self, relative: str) -> Path:
        """在临时目录创建画布可引用的资产文件（add_file 要求文件真实存在）。"""
        path = self.plugin._cache_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"png")
        return path

    # ------------------------------------------------------------------ 注册与生命周期

    async def test_registers_every_bridge_route(self):
        routes = {(api[0], tuple(api[2])) for api in self.context.registered_web_apis}
        self.assertEqual(len(routes), len(self.web.ROUTES))
        for endpoint, _name, methods, _desc in self.web.ROUTES:
            self.assertIn((f"{PREFIX}/{endpoint}", tuple(methods)), routes)

    async def test_close_unregisters_only_own_routes(self):
        other = ("OTHER/status", lambda: {}, ["GET"], "其他插件")
        self.context.registered_web_apis.append(other)
        handler = self.context.handler(f"{PREFIX}/status")
        await self.web.close()
        self.assertEqual(self.context.registered_web_apis, [other])
        response = await handler()
        self.assertEqual(response.status_code, 503)

    # ------------------------------------------------------------------ 状态

    async def test_status_reports_api_key_mode(self):
        status = await self.call("status")
        self.assertEqual(status["auth_mode"], "api_key")
        self.assertFalse(status["oauth_connected"])
        self.assertIsNone(status["oauth_expires_at"])
        self.assertEqual(status["region"], "cn")
        self.assertEqual(status["thinking_effort"], "high")
        self.assertEqual(status["state"], "ready")
        self.assertTrue(status["ready"])
        self.assertIn("image.generate", status["capabilities"])
        self.assertEqual(status["views"], ["mcode_dashboard"])

    async def test_status_reports_oauth_mode_and_degraded_state(self):
        self.plugin._token_store.save(
            OAuthToken(
                access_token="a",
                refresh_token="r",
                expires_at=time.time() + 3600,
                region="global",
            )
        )
        self.plugin._service._state = "unavailable"
        status = await self.call("status")
        self.assertEqual(status["auth_mode"], "oauth")
        self.assertEqual(status["region"], "global")
        self.assertTrue(status["oauth_connected"])
        self.assertGreater(status["oauth_expires_at"], int(time.time()))
        self.assertEqual(status["state"], "degraded")
        self.assertFalse(status["ready"])

    # ------------------------------------------------------------------ 额度

    async def test_quota_merges_dual_cycle_and_credit_balance(self):
        self.plugin._key_pool = SimpleNamespace(
            _states=[
                SimpleNamespace(key="sk-a", index=0),
                SimpleNamespace(key="sk-b", index=1),
            ]
        )
        self.plugin._quota.info = AsyncMock(
            side_effect=[
                {"model_remains": [TEXT_QUOTA_ITEM, VIDEO_QUOTA_ITEM]},
                {
                    "kind": "account_balance",
                    "available_amount": "1420.5",
                    "credit_balance": "0",
                    "owed_amount": "0",
                },
            ]
        )
        quota = await self.call("quota")
        interval = quota["interval_5h"]
        self.assertEqual(
            (interval["total"], interval["used"], interval["remaining"]), (100, 25, 75)
        )
        self.assertEqual(interval["remaining_percent"], 75.0)
        self.assertEqual(interval["status"], 1)
        self.assertEqual(interval["label"], "12小时额度")
        self.assertAlmostEqual(
            interval["reset_at_ms"] - time.time() * 1000, 3_600_000, delta=5_000
        )
        video = quota["weekly_video"]
        self.assertEqual((video["total"], video["remaining"]), (50, 30))
        self.assertEqual(video["status"], 1)
        self.assertTrue(video["label"].endswith("周额度"))
        self.assertEqual(
            quota["credit_fallback"],
            {
                "enabled": True,
                "active_deducting": False,
                "balance": "1,420.50",
                "consumed_today": "未知",
            },
        )
        self.assertEqual(quota["keys"], {"total": 2, "failed": 0})

    async def test_quota_reports_failure_when_no_data(self):
        self.plugin._quota.info = AsyncMock(side_effect=RuntimeError("网关 500"))
        response = await self.call("quota")
        self.assertEqual(response.status_code, 502)

    async def test_quota_without_credentials_is_not_found_as_error(self):
        self.plugin._key_pool = None
        self.plugin._client._api_key = None
        response = await self.call("quota")
        self.assertEqual(response.status_code, 503)

    # ------------------------------------------------------------------ OAuth

    async def test_oauth_start_poll_and_logout(self):
        auth = DeviceAuthInfo(
            device_code="device-code",
            user_code="ABCD-1234",
            code_verifier="verifier",
            verification_uri="https://account.minimax.cn/oauth2/device",
            verification_uri_complete="https://account.minimax.cn/oauth2/device?user_code=ABCD-1234",
            expires_in=600,
            interval=4.0,
        )
        token = OAuthToken(
            access_token="access",
            refresh_token="refresh",
            expires_at=time.time() + 7200,
            region="cn",
        )
        self.plugin._oauth.start_device_authorization = AsyncMock(return_value=auth)
        self.plugin._oauth.poll_device_token = AsyncMock(return_value=token)
        self.plugin._oauth.revoke = AsyncMock()
        web_request.reset(body={"region": "cn"})

        started = await self.call("oauth/start")
        self.assertEqual(started["user_code"], "ABCD-1234")
        self.assertEqual(started["interval"], 4)
        self.assertEqual(started["expires_in"], 600)
        self.assertTrue(
            started["verification_uri_complete"].endswith("user_code=ABCD-1234")
        )
        self.assertEqual(started["region"], "cn")

        session = self.web._login_sessions[started["session_id"]]
        await session.task
        web_request.reset(query={"session_id": started["session_id"]})
        polled = await self.call("oauth/poll")
        self.assertEqual(polled["status"], "authorized")
        self.assertEqual(polled["region"], "cn")
        self.assertGreater(polled["expires_at"], int(time.time()))
        self.assertEqual(self.plugin._token_store.load().access_token, "access")

        web_request.reset(body={})
        self.assertEqual(await self.call("oauth/logout"), {"ok": True})
        self.plugin._oauth.revoke.assert_awaited_once_with("refresh", "cn")
        self.assertIsNone(self.plugin._token_store.load())

    async def test_oauth_denied_status_and_unknown_session(self):
        auth = DeviceAuthInfo(
            device_code="d",
            user_code="U-1",
            code_verifier="v",
            verification_uri="https://account.minimax.cn/oauth2/device",
            verification_uri_complete="https://account.minimax.cn/oauth2/device?user_code=U-1",
            expires_in=120,
        )
        self.plugin._oauth.start_device_authorization = AsyncMock(return_value=auth)
        self.plugin._oauth.poll_device_token = AsyncMock(
            side_effect=OAuthError("access_denied")
        )
        started = await self.call("oauth/start")
        await self.web._login_sessions[started["session_id"]].task
        web_request.reset(query={"session_id": started["session_id"]})
        self.assertEqual((await self.call("oauth/poll"))["status"], "denied")

        web_request.reset(query={"session_id": "missing"})
        response = await self.call("oauth/poll")
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------ 配置

    async def test_config_effort_validates_and_updates(self):
        web_request.reset(body={"thinking_effort": "MAX"})
        result = await self.call("config/effort")
        self.assertEqual(result["thinking_effort"], "max")
        self.assertTrue(result["ok"])
        self.assertEqual(self.plugin._thinking_effort, "max")
        self.assertEqual(self.plugin._config["thinking_effort"], "max")

        web_request.reset(body={"thinking_effort": "ultra"})
        response = await self.call("config/effort")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.plugin._thinking_effort, "max")

    # ------------------------------------------------------------------ 画布

    async def test_canvas_document_round_trip_is_idempotent(self):
        self.touch_cache(".mavis/canvas/assets/a.png")
        web_request.reset(query={"session_id": "sess_1"})
        empty = await self.call("canvas/doc")
        self.assertEqual(empty["schemaVersion"], 1)
        self.assertEqual(empty["nodes"], [])
        self.assertEqual(empty["sessionId"], "sess_1")

        operation = {
            "schemaVersion": 1,
            "operationId": "op_1",
            "mutations": [
                {
                    "kind": "add_file",
                    "nodeId": "node_a",
                    "relativePath": ".mavis/canvas/assets/a.png",
                    "layout": {
                        "x": 10,
                        "y": 20,
                        "width": 300,
                        "height": 200,
                        "zIndex": 2,
                    },
                    "fileMeta": {"fileName": "a.png", "mimeType": "image/png"},
                }
            ],
        }
        web_request.reset(body={"sessionId": "sess_1", "operation": operation})
        mutated = await self.call("canvas/mutate")
        self.assertEqual(mutated["operationId"], "op_1")
        self.assertEqual(mutated["document"]["changeSeq"], 1)
        node = mutated["document"]["nodes"][0]
        self.assertEqual(node["file"]["relativePath"], ".mavis/canvas/assets/a.png")
        self.assertEqual(node["layout"]["width"], 300)

        web_request.reset(body={"sessionId": "sess_1", "operation": operation})
        repeated = await self.call("canvas/mutate")
        self.assertEqual(repeated["document"]["changeSeq"], 1)
        self.assertEqual(len(repeated["document"]["nodes"]), 1)

        annotation = {
            "id": "ann_1",
            "number": 1,
            "normalizedRect": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            "comment": "边框太硬",
            "action": "comment",
            "createdAtMs": 1,
        }
        web_request.reset(
            body={
                "sessionId": "sess_1",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_2",
                    "mutations": [
                        {
                            "kind": "add_annotation",
                            "nodeId": "node_a",
                            "annotation": annotation,
                        },
                        {
                            "kind": "update_layout",
                            "nodeId": "node_a",
                            "layout": {
                                "x": 1,
                                "y": 2,
                                "width": 400,
                                "height": 300,
                                "zIndex": 5,
                            },
                        },
                    ],
                },
            }
        )
        annotated = await self.call("canvas/mutate")
        self.assertEqual(annotated["document"]["changeSeq"], 2)
        self.assertEqual(
            annotated["document"]["nodes"][0]["annotations"][0]["comment"], "边框太硬"
        )

        stored = json.loads(
            (self.plugin._plugin_data_dir / "canvas" / "sess_1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(stored["changeSeq"], 2)

    async def test_canvas_mutate_rejects_bad_payloads(self):
        cases = [
            (
                {
                    "sessionId": "sess_1",
                    "operation": {
                        "schemaVersion": 2,
                        "operationId": "x",
                        "mutations": [],
                    },
                },
                400,
            ),
            (
                {
                    "sessionId": "sess_1",
                    "operation": {
                        "schemaVersion": 1,
                        "operationId": "x",
                        "mutations": [],
                    },
                },
                400,
            ),
            (
                {
                    "sessionId": "sess_1",
                    "operation": {
                        "schemaVersion": 1,
                        "operationId": "x",
                        "mutations": [
                            {
                                "kind": "add_file",
                                "nodeId": "n",
                                "relativePath": "../../etc/passwd",
                                "layout": {},
                            }
                        ],
                    },
                },
                400,
            ),
            (
                {
                    "sessionId": "sess_1",
                    "operation": {
                        "schemaVersion": 1,
                        "operationId": "x",
                        "mutations": [
                            {"kind": "update_layout", "nodeId": "missing", "layout": {}}
                        ],
                    },
                },
                400,
            ),
        ]
        for body, status in cases:
            with self.subTest(
                kind=body["operation"]["mutations"]
                or body["operation"]["schemaVersion"]
            ):
                web_request.reset(body=body)
                response = await self.call("canvas/mutate")
                self.assertEqual(response.status_code, status)

        web_request.reset(query={"session_id": "../escape"})
        response = await self.call("canvas/doc")
        self.assertEqual(response.status_code, 400)

    async def test_canvas_iterate_sends_annotations_to_vlm(self):
        described = AsyncMock(return_value={"content": "建议降低边框对比度"})
        self.plugin._vision.describe = described
        web_request.reset(
            body={
                "session_id": "sess_1",
                "instruction": "保持暖色调",
                "nodes": [
                    {
                        "node_id": "node_a",
                        "relative_path": "a.png",
                        "file_name": "a.png",
                        "annotations": [
                            {
                                "id": "ann_1",
                                "number": 1,
                                "normalizedRect": {
                                    "x": 0.64,
                                    "y": 0.16,
                                    "width": 0.28,
                                    "height": 0.42,
                                },
                                "comment": "这里过曝",
                                "action": "erase",
                            }
                        ],
                    }
                ],
            }
        )
        result = await self.call("canvas/iterate")
        self.assertEqual(result["submitted"], 1)
        self.assertIn(
            "[a.png #1 (erase) rect=(0.64,0.16,0.28,0.42)]: 这里过曝",
            result["prompt_preview"],
        )
        self.assertEqual(result["results"][0]["text"], "建议降低边框对比度")
        self.assertEqual(result["results"][0]["file_name"], "a.png")
        self.assertEqual(result["skipped"], 0)
        kwargs = described.call_args.kwargs
        self.assertEqual(kwargs["image"], "a.png")
        self.assertEqual(kwargs["data_dir"], str(self.plugin._plugin_data_dir))
        self.assertIn("保持暖色调", kwargs["prompt"])

    @staticmethod
    def _iterate_node(node_id, file_name, comment=None):
        annotations = []
        if comment:
            annotations.append(
                {
                    "id": f"ann_{node_id}",
                    "number": 1,
                    "normalizedRect": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
                    "comment": comment,
                    "action": "comment",
                }
            )
        return {
            "node_id": node_id,
            "relative_path": file_name,
            "file_name": file_name,
            "annotations": annotations,
        }

    async def test_canvas_iterate_builds_prompt_per_node(self):
        described = AsyncMock(side_effect=[{"content": "结果 A"}, {"content": "结果 B"}])
        self.plugin._vision.describe = described
        web_request.reset(
            body={
                "instruction": "保持暖色调",
                "nodes": [
                    self._iterate_node("node_a", "a.png", "A 图过曝"),
                    self._iterate_node("node_b", "b.png", "B 图偏色"),
                ],
            }
        )
        result = await self.call("canvas/iterate")
        self.assertEqual(described.await_count, 2)
        by_image = {
            call.kwargs["image"]: call.kwargs["prompt"]
            for call in described.call_args_list
        }
        self.assertIn("A 图过曝", by_image["a.png"])
        self.assertNotIn("B 图偏色", by_image["a.png"])
        self.assertIn("B 图偏色", by_image["b.png"])
        self.assertNotIn("A 图过曝", by_image["b.png"])
        for prompt in by_image.values():
            self.assertTrue(prompt.startswith("保持暖色调\n\n以下是画布上的图片批注"))
        self.assertEqual(result["submitted"], 2)
        self.assertEqual(result["skipped"], 0)
        self.assertEqual(
            [(item["node_id"], item["file_name"]) for item in result["results"]],
            [("node_a", "a.png"), ("node_b", "b.png")],
        )
        self.assertEqual(
            result["prompt_preview"], "\n\n".join(by_image[name] for name in ("a.png", "b.png"))
        )

    async def test_canvas_iterate_skips_nodes_without_annotations(self):
        described = AsyncMock(return_value={"content": "结果"})
        self.plugin._vision.describe = described
        web_request.reset(
            body={
                "nodes": [
                    self._iterate_node("node_a", "a.png", "A 图过曝"),
                    self._iterate_node("node_empty", "empty.png"),
                ]
            }
        )
        result = await self.call("canvas/iterate")
        described.assert_awaited_once()
        self.assertEqual(described.call_args.kwargs["image"], "a.png")
        self.assertEqual([item["node_id"] for item in result["results"]], ["node_a"])
        self.assertEqual(result["submitted"], 1)
        self.assertEqual(result["skipped"], 1)

    async def test_canvas_iterate_skipped_counts_nodes_over_limit(self):
        described = AsyncMock(return_value={"content": "结果"})
        self.plugin._vision.describe = described
        limit = sys.modules[type(self.web).__module__].MAX_ITERATE_NODES
        # 第 1 个节点无批注，超出上限的 2 个节点不会被提交
        nodes = [self._iterate_node("node_0", "0.png")] + [
            self._iterate_node(f"node_{index}", f"{index}.png", f"批注 {index}")
            for index in range(1, limit + 2)
        ]
        web_request.reset(body={"nodes": nodes})
        result = await self.call("canvas/iterate")
        self.assertEqual(described.await_count, limit - 1)
        self.assertEqual(result["submitted"], limit - 1)
        self.assertEqual(result["skipped"], len(nodes) - (limit - 1))

    async def test_canvas_iterate_reports_failed_node_with_file_name(self):
        described = AsyncMock(side_effect=[{"content": "结果 A"}, RuntimeError("vlm 失败")])
        self.plugin._vision.describe = described
        web_request.reset(
            body={
                "nodes": [
                    self._iterate_node("node_a", "a.png", "A 图过曝"),
                    self._iterate_node("node_b", "b.png", "B 图偏色"),
                ]
            }
        )
        result = await self.call("canvas/iterate")
        self.assertEqual(result["submitted"], 1)
        failed = result["results"][1]
        self.assertEqual(
            (failed["node_id"], failed["file_name"], failed["ok"], failed["error"]),
            ("node_b", "b.png", False, "vlm 失败"),
        )

    async def test_canvas_keeps_long_preview_url(self):
        self.touch_cache("a.png")
        self.touch_cache("b.png")
        preview_url = "data:image/png;base64," + "A" * 40_000
        web_request.reset(
            body={
                "sessionId": "sess_preview",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_preview",
                    "mutations": [
                        {
                            "kind": "add_file",
                            "nodeId": "node_preview",
                            "relativePath": "a.png",
                            "layout": {"x": 0, "y": 0, "width": 100, "height": 100, "zIndex": 1},
                            "fileMeta": {"previewUrl": preview_url},
                        }
                    ],
                },
            }
        )
        node = (await self.call("canvas/mutate"))["document"]["nodes"][0]
        self.assertEqual(node["file"]["previewUrl"], preview_url)

        web_request.reset(query={"session_id": "sess_preview"})
        reloaded = await self.call("canvas/doc")
        self.assertEqual(reloaded["nodes"][0]["file"]["previewUrl"], preview_url)

        oversized = "https://cdn.example.com/" + "B" * 70_000
        web_request.reset(
            body={
                "sessionId": "sess_preview",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_preview_2",
                    "mutations": [
                        {
                            "kind": "add_annotation",
                            "nodeId": "node_preview",
                            "annotation": {"id": "ann_p", "normalizedRect": {}, "comment": "c"},
                        }
                    ],
                },
            }
        )
        annotated = await self.call("canvas/mutate")
        self.assertEqual(annotated["document"]["nodes"][0]["file"]["previewUrl"], preview_url)
        self.assertEqual(len(annotated["document"]["nodes"][0]["annotations"]), 1)
        web_request.reset(
            body={
                "sessionId": "sess_oversize",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_oversize",
                    "mutations": [
                        {
                            "kind": "add_file",
                            "nodeId": "node_over",
                            "relativePath": "b.png",
                            "layout": {},
                            "fileMeta": {"previewUrl": oversized, "fileName": "x" * 5000},
                        }
                    ],
                },
            }
        )
        over_node = (await self.call("canvas/mutate"))["document"]["nodes"][0]
        self.assertEqual(len(over_node["file"]["fileName"]), 1024)
        self.assertEqual(len(over_node["file"]["previewUrl"]), 65_536)
        self.assertEqual(over_node["file"]["previewUrl"], oversized[:65_536])

    async def test_canvas_remove_annotation_renumbers_remaining(self):
        self.touch_cache("a.png")
        async def add_annotation(operation_id, annotation_id, number):
            web_request.reset(
                body={
                    "sessionId": "sess_numbering",
                    "operation": {
                        "schemaVersion": 1,
                        "operationId": operation_id,
                        "mutations": [
                            {
                                "kind": "add_annotation",
                                "nodeId": "node_a",
                                "annotation": {
                                    "id": annotation_id,
                                    "number": number,
                                    "normalizedRect": {"x": 0, "y": 0, "width": 0.2, "height": 0.2},
                                    "comment": f"批注 {annotation_id}",
                                    "action": "comment",
                                },
                            }
                        ],
                    },
                }
            )
            return await self.call("canvas/mutate")

        web_request.reset(
            body={
                "sessionId": "sess_numbering",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_seed",
                    "mutations": [
                        {
                            "kind": "add_file",
                            "nodeId": "node_a",
                            "relativePath": "a.png",
                            "layout": {"x": 0, "y": 0, "width": 100, "height": 100, "zIndex": 1},
                        }
                    ],
                },
            }
        )
        await self.call("canvas/mutate")
        for index, annotation_id in enumerate(("ann_1", "ann_2", "ann_3"), start=1):
            await add_annotation(f"op_add_{index}", annotation_id, index)

        web_request.reset(
            body={
                "sessionId": "sess_numbering",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_remove",
                    "mutations": [
                        {"kind": "remove_annotation", "nodeId": "node_a", "annotationId": "ann_2"}
                    ],
                },
            }
        )
        remaining = (await self.call("canvas/mutate"))["document"]["nodes"][0]["annotations"]
        self.assertEqual([item["id"] for item in remaining], ["ann_1", "ann_3"])
        self.assertEqual([item["number"] for item in remaining], [1, 2])
        self.assertEqual([item["comment"] for item in remaining], ["批注 ann_1", "批注 ann_3"])

    @staticmethod
    def _add_file(node_id, path, file_name=None):
        return {
            "kind": "add_file",
            "nodeId": node_id,
            "relativePath": path,
            "layout": {},
            "fileMeta": {"fileName": file_name or path},
        }

    @staticmethod
    def _annotation_mutation(node_id, annotation_id, number, kind="add_annotation", comment=None):
        return {
            "kind": kind,
            "nodeId": node_id,
            "annotation": {
                "id": annotation_id,
                "number": number,
                "normalizedRect": {"x": 0, "y": 0, "width": 0.2, "height": 0.2},
                "comment": comment or annotation_id,
            },
        }

    async def canvas_apply(self, operation_id, mutations, session_id="sess_rules"):
        return await self.web._canvas.apply(
            session_id,
            {"schemaVersion": 1, "operationId": operation_id, "mutations": mutations},
        )

    async def assert_canvas_rejected(self, code, operation_id, mutations, session_id="sess_rules"):
        """变更被拒绝，且文档（含 changeSeq 与回执）保持原样。"""
        before = await self.web._canvas.load(session_id)
        with self.assertRaises(CanvasStoreError) as raised:
            await self.canvas_apply(operation_id, mutations, session_id)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(await self.web._canvas.load(session_id), before)
        return raised.exception

    async def test_canvas_operation_id_replay_is_idempotent_and_reuse_rejected(self):
        self.touch_cache("a.png")
        self.touch_cache("b.png")
        first = await self.canvas_apply("op_1", [self._add_file("node_a", "a.png")])
        self.assertEqual(first["changeSeq"], 1)
        receipts = first["appliedOperations"]
        self.assertEqual([item["operationId"] for item in receipts], ["op_1"])
        self.assertRegex(receipts[0]["fingerprint"], r"^[0-9a-f]{64}$")

        # 同一内容（键顺序不同）重放：返回当前文档，不再递增 changeSeq
        reordered = {
            "relativePath": "a.png",
            "fileMeta": {"fileName": "a.png"},
            "layout": {},
            "nodeId": "node_a",
            "kind": "add_file",
        }
        replayed = await self.canvas_apply("op_1", [reordered])
        self.assertEqual(replayed, first)

        await self.assert_canvas_rejected(
            "operation_id_reused", "op_1", [self._add_file("node_b", "b.png")]
        )

    async def test_canvas_operation_receipts_are_pruned_and_legacy_dropped(self):
        self.touch_cache("a.png")
        await self.canvas_apply("op_seed", [self._add_file("node_a", "a.png")])
        for index in range(MAX_APPLIED_OPERATIONS):
            layout = {"x": index, "y": 0, "width": 100, "height": 100, "zIndex": 1}
            document = await self.canvas_apply(
                f"op_move_{index}",
                [{"kind": "update_layout", "nodeId": "node_a", "layout": layout}],
            )
        ids = [item["operationId"] for item in document["appliedOperations"]]
        self.assertEqual(len(ids), MAX_APPLIED_OPERATIONS)
        self.assertNotIn("op_seed", ids)
        self.assertEqual(ids[-1], f"op_move_{MAX_APPLIED_OPERATIONS - 1}")

        legacy = self.plugin._plugin_data_dir / "canvas" / "sess_legacy.json"
        legacy.write_text(
            json.dumps(
                {
                    "nodes": [],
                    "appliedOperations": [
                        "op_old",
                        {"operationId": "op_partial"},
                        {"operationId": "op_ok", "fingerprint": "f" * 64},
                    ],
                }
            ),
            encoding="utf-8",
        )
        loaded = await self.web._canvas.load("sess_legacy")
        self.assertEqual(
            loaded["appliedOperations"], [{"operationId": "op_ok", "fingerprint": "f" * 64}]
        )

    async def test_canvas_add_file_rejects_existing_node_id(self):
        self.touch_cache("a.png")
        self.touch_cache("b.png")
        await self.canvas_apply("op_1", [self._add_file("node_a", "a.png")])
        await self.assert_canvas_rejected(
            "node_already_exists", "op_2", [self._add_file("node_a", "b.png")]
        )
        # 同一次操作内先新增再重复新增同样被拒绝，且前一条变更不落盘
        await self.assert_canvas_rejected(
            "node_already_exists",
            "op_3",
            [self._add_file("node_b", "b.png"), self._add_file("node_b", "b.png")],
        )

    async def test_canvas_add_file_rejects_file_already_on_canvas(self):
        self.touch_cache("a.png")
        self.touch_cache("b.png")
        await self.canvas_apply("op_1", [self._add_file("node_a", "a.png")])
        error = await self.assert_canvas_rejected(
            "file_already_on_canvas", "op_2", [self._add_file("node_b", "a.png")]
        )
        self.assertEqual(str(error), "此文件已在画布中")
        await self.assert_canvas_rejected(
            "file_already_on_canvas",
            "op_3",
            [self._add_file("node_c", "b.png"), self._add_file("node_d", "b.png")],
        )

        # 同一次操作内先移除占用节点，则文件可重新置入
        document = await self.canvas_apply(
            "op_4",
            [
                {"kind": "remove_node", "nodeId": "node_a"},
                self._add_file("node_e", "a.png"),
            ],
        )
        self.assertEqual([node["id"] for node in document["nodes"]], ["node_e"])

    async def test_canvas_remove_node_requires_existing_node(self):
        self.touch_cache("a.png")
        await self.canvas_apply("op_1", [self._add_file("node_a", "a.png")])
        await self.assert_canvas_rejected(
            "node_not_found", "op_2", [{"kind": "remove_node", "nodeId": "missing"}]
        )
        await self.assert_canvas_rejected(
            "node_not_found",
            "op_3",
            [
                {"kind": "remove_node", "nodeId": "node_a"},
                {"kind": "remove_node", "nodeId": "node_a"},
            ],
        )

    async def test_canvas_remove_annotation_requires_existing_annotation(self):
        self.touch_cache("a.png")
        await self.canvas_apply(
            "op_1",
            [self._add_file("node_a", "a.png"), self._annotation_mutation("node_a", "ann_1", 1)],
        )
        await self.assert_canvas_rejected(
            "annotation_not_found",
            "op_2",
            [{"kind": "remove_annotation", "nodeId": "node_a", "annotationId": "ann_missing"}],
        )

    async def test_canvas_add_annotation_rejects_duplicates_and_non_images(self):
        self.touch_cache("a.png")
        self.touch_cache("voice.mp3")
        self.touch_cache("b.PNG")
        self.touch_cache("c.png")
        await self.canvas_apply(
            "op_1",
            [
                self._add_file("node_a", "a.png"),
                self._add_file("node_audio", "voice.mp3"),
                # 无 fileName 时回退 relativePath；扩展名大小写不敏感
                {**self._add_file("node_upper", "b.PNG"), "fileMeta": {}},
                # fileName 优先于 relativePath
                self._add_file("node_note", "c.png", "note.md"),
                self._annotation_mutation("node_a", "ann_1", 1),
            ],
        )
        await self.assert_canvas_rejected(
            "annotation_exists", "op_2", [self._annotation_mutation("node_a", "ann_1", 2)]
        )
        await self.assert_canvas_rejected(
            "annotation_number_conflict",
            "op_3",
            [self._annotation_mutation("node_a", "ann_2", 1)],
        )
        for index, node_id in enumerate(("node_audio", "node_note")):
            with self.subTest(node=node_id):
                error = await self.assert_canvas_rejected(
                    "annotation_unsupported",
                    f"op_unsupported_{index}",
                    [self._annotation_mutation(node_id, "ann_x", 1)],
                )
                self.assertEqual(str(error), "仅图片节点支持批注")

        document = await self.canvas_apply(
            "op_4", [self._annotation_mutation("node_upper", "ann_u", 1)]
        )
        by_id = {node["id"]: node for node in document["nodes"]}
        self.assertEqual(len(by_id["node_upper"]["annotations"]), 1)

    async def test_canvas_update_annotation_rejects_number_conflict(self):
        self.touch_cache("a.png")
        await self.canvas_apply(
            "op_1",
            [
                self._add_file("node_a", "a.png"),
                self._annotation_mutation("node_a", "ann_1", 1),
                self._annotation_mutation("node_a", "ann_2", 2),
            ],
        )
        await self.assert_canvas_rejected(
            "annotation_number_conflict",
            "op_2",
            [self._annotation_mutation("node_a", "ann_2", 1, "update_annotation")],
        )
        # 保持自身序号的更新不算冲突
        document = await self.canvas_apply(
            "op_3",
            [self._annotation_mutation("node_a", "ann_2", 2, "update_annotation", "新批注")],
        )
        self.assertEqual(
            [(item["id"], item["number"], item["comment"]) for item in document["nodes"][0]["annotations"]],
            [("ann_1", 1, "ann_1"), ("ann_2", 2, "新批注")],
        )

    async def test_canvas_failed_operation_persists_nothing(self):
        self.touch_cache("a.png")
        self.touch_cache("b.png")
        await self.canvas_apply("op_1", [self._add_file("node_a", "a.png")])
        path = self.plugin._plugin_data_dir / "canvas" / "sess_rules.json"
        stored = path.read_text(encoding="utf-8")
        mutations = [
            self._add_file("node_b", "b.png"),
            {"kind": "update_layout", "nodeId": "missing", "layout": {}},
        ]
        await self.assert_canvas_rejected("node_not_found", "op_fail", mutations)
        self.assertEqual(path.read_text(encoding="utf-8"), stored)

        # 失败的操作不留回执：同一 operationId 可用修正后的内容重新提交
        document = await self.canvas_apply("op_fail", [self._add_file("node_b", "b.png")])
        self.assertEqual(document["changeSeq"], 2)
        self.assertEqual([node["id"] for node in document["nodes"]], ["node_a", "node_b"])

    async def test_canvas_rule_violations_map_to_http_400(self):
        self.touch_cache("a.png")
        await self.canvas_apply("op_1", [self._add_file("node_a", "a.png")])
        operation = {
            "schemaVersion": 1,
            "operationId": "op_1",
            "mutations": [self._add_file("node_b", "a.png")],
        }
        web_request.reset(body={"sessionId": "sess_rules", "operation": operation})
        response = await self.call("canvas/mutate")
        self.assertEqual(response.status_code, 400)
        self.assertIn("operationId", response.content["message"])

        operation = {**operation, "operationId": "op_2"}
        web_request.reset(body={"sessionId": "sess_rules", "operation": operation})
        response = await self.call("canvas/mutate")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.content["message"], "此文件已在画布中")

    async def test_canvas_iterate_requires_annotations(self):
        web_request.reset(
            body={
                "nodes": [{"node_id": "n", "relative_path": "a.png", "annotations": []}]
            }
        )
        response = await self.call("canvas/iterate")
        self.assertEqual(response.status_code, 400)

    # ------------------------------------------------------------------ 媒体工具

    async def test_image_search_returns_contract_items(self):

        async def _save(url, dest, *, allowed_dir=None, **_):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"png")
            return Path(dest)

        self.plugin._matrix.images_search_and_download = AsyncMock(
            return_value=[
                {
                    "images": [
                        {
                            "image_url": "https://cdn.example.com/a.png",
                            "title": "A",
                            "width": 1440,
                        },
                        {"image_url": "https://other.example.com/b.jpg"},
                    ]
                }
            ]
        )
        self.plugin._matrix_media.download_to_file = AsyncMock(side_effect=_save)
        web_request.reset(body={"query": "暖光材质", "count": 5})
        result = await self.call("tools/image-search")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["downloaded"], 2)
        first = result["items"][0]
        self.assertEqual(first["title"], "A")
        self.assertEqual(first["source_domain"], "cdn.example.com")
        self.assertEqual(first["width"], 1440)
        self.assertTrue(first["local_path"].endswith(".png"))
        self.assertEqual(result["items"][1]["title"], "暖光材质 · #2")
        self.assertEqual(self.web.events.history()[-1]["level"], "ok")

    async def test_image_search_reports_empty_and_missing_query(self):
        self.plugin._matrix.images_search_and_download = AsyncMock(
            return_value=[{"images": []}]
        )
        web_request.reset(body={"query": "无结果"})
        empty = await self.call("tools/image-search")
        self.assertEqual(empty.status_code, 502)
        web_request.reset(body={})
        missing = await self.call("tools/image-search")
        self.assertEqual(missing.status_code, 400)

    async def test_image_reverse_by_path_and_upload(self):
        self.plugin._reverse_image_tool.resolve_image_url = AsyncMock(
            return_value="https://cdn.example.com/a.png"
        )
        self.plugin._matrix.image_reverse_search = AsyncMock(
            return_value=[
                {
                    "title": "来源页",
                    "link": "https://src.example.com",
                    "source": "src.example.com",
                },
                "not-a-dict",
            ]
        )
        web_request.reset(body={"image": "a.png"})
        result = await self.call("tools/image-reverse")
        self.assertEqual(result["matches"][0]["title"], "来源页")
        self.assertEqual(
            result["matches"][1],
            {"title": "", "link": "", "source": "", "image_url": "", "snippet": ""},
        )
        self.plugin._reverse_image_tool.resolve_image_url.assert_awaited_once_with(
            "a.png"
        )

        upload = UploadFile("photo.PNG", b"png-bytes")
        web_request.reset(uploads={"file": upload})
        uploaded = await self.call("tools/image-reverse-upload")
        self.assertEqual(uploaded["matches"][0]["link"], "https://src.example.com")
        self.assertEqual(upload.filename, "photo.PNG")
        self.assertFalse(upload.saved.exists())
        self.plugin._reverse_image_tool.resolve_image_url.assert_awaited_with(
            str(upload.saved), trusted_local=True
        )

        web_request.reset(uploads={"file": UploadFile("auth.json")})
        rejected = await self.call("tools/image-reverse-upload")
        self.assertEqual(rejected.status_code, 415)

        web_request.reset(uploads={})
        self.assertEqual(
            (await self.call("tools/image-reverse-upload")).status_code, 400
        )

    async def test_music_and_sfx_return_track_payload(self):
        async def _save(url, dest, *, allowed_dir=None, **_):
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"audio")
            return Path(dest)

        self.plugin._matrix.batch_text_to_music = AsyncMock(
            return_value={"output_url": "https://cdn.example.com/slot_0.mp3"}
        )
        self.plugin._matrix.batch_text_to_audio = AsyncMock(
            return_value={"output_url": "https://cdn.example.com/slot_0.mp3"}
        )
        self.plugin._matrix_media.download_to_file = AsyncMock(side_effect=_save)

        web_request.reset(
            body={
                "kind": "music",
                "prompt": "温暖民谣",
                "lyrics": "[Verse] 风",
                "format": "wav",
            }
        )
        music = await self.call("tools/music")
        self.assertEqual(music["kind"], "music")
        self.assertEqual(music["format"], "wav")
        self.assertEqual(music["lyrics"], "[Verse] 风")
        self.assertTrue(music["local_path"].endswith(".wav"))
        self.plugin._matrix.batch_text_to_music.assert_awaited_once_with(
            "温暖民谣", "[Verse] 风", "wav"
        )

        web_request.reset(body={"kind": "sfx", "prompt": "木门关闭的闷响"})
        sfx = await self.call("tools/sfx")
        self.assertEqual(sfx["kind"], "sfx")
        self.assertEqual(sfx["format"], "mp3")
        self.plugin._matrix.batch_text_to_audio.assert_awaited_once_with(
            "木门关闭的闷响"
        )

        web_request.reset(body={"prompt": "abc"})
        self.assertEqual((await self.call("tools/music")).status_code, 400)

    async def test_auth_error_from_matrix_surfaces_login_hint(self):

        self.plugin._matrix.batch_text_to_music = AsyncMock(
            side_effect=MiniMaxError(
                ErrorCategory.AUTH, "该功能需要 MiniMax 账号授权，请发送 /mmx login"
            )
        )
        web_request.reset(body={"prompt": "钢琴独奏"})
        response = await self.call("tools/music")
        self.assertEqual(response.status_code, 401)
        self.assertIn("/mmx login", response.content["message"])

    # ------------------------------------------------------------------ 文件下载白名单

    async def test_media_relative_paths_round_trip_through_canvas(self):
        """搜图/音频返回的文件名可直接作为画布 relativePath 并经 canvas/download 取回。"""

        async def _save(url, dest, *, allowed_dir=None, **_):
            Path(dest).write_bytes(b"png")
            return Path(dest)

        self.plugin._matrix.images_search_and_download = AsyncMock(
            return_value=[{"images": [{"image_url": "https://cdn.example.com/a.jpg"}]}]
        )
        self.plugin._matrix.batch_text_to_audio = AsyncMock(
            return_value={"output_url": "https://cdn.example.com/s.mp3"}
        )
        self.plugin._matrix_media.download_to_file = AsyncMock(side_effect=_save)

        web_request.reset(body={"query": "海报", "count": 1})
        item = (await self.call("tools/image-search"))["items"][0]
        self.assertEqual(item["local_path"], Path(item["local_path"]).name)
        self.assertFalse(Path(item["local_path"]).is_absolute())
        self.assertTrue(
            (self.plugin._cache_dir / item["local_path"]).is_file(),
        )

        web_request.reset(body={"prompt": "木门关闭的闷响"})
        sfx = await self.call("tools/sfx")
        self.assertEqual(sfx["local_path"], Path(sfx["local_path"]).name)

        self.plugin._reverse_image_tool.resolve_image_url = AsyncMock(
            return_value="https://cdn.example.com/a.jpg"
        )
        self.plugin._matrix.image_reverse_search = AsyncMock(return_value=[])
        web_request.reset(body={"image": item["local_path"]})
        self.assertEqual((await self.call("tools/image-reverse"))["matches"], [])
        self.plugin._reverse_image_tool.resolve_image_url.assert_awaited_once_with(
            item["local_path"]
        )

        web_request.reset(
            body={
                "sessionId": "sess_round",
                "operation": {
                    "schemaVersion": 1,
                    "operationId": "op_round",
                    "mutations": [
                        {
                            "kind": "add_file",
                            "nodeId": "node_round",
                            "relativePath": item["local_path"],
                            "layout": {
                                "x": 0,
                                "y": 0,
                                "width": 300,
                                "height": 200,
                                "zIndex": 1,
                            },
                            "fileMeta": {"fileName": "a.jpg"},
                        }
                    ],
                },
            }
        )
        node = (await self.call("canvas/mutate"))["document"]["nodes"][0]
        self.assertEqual(node["file"]["relativePath"], item["local_path"])

        web_request.reset(query={"path": item["local_path"]})
        served = await self.call("canvas/download")
        self.assertEqual(Path(served.path).name, item["local_path"])

        web_request.reset(query={"path": item["local_path"]})
        temp_served = await self.call("tools/download-temp")
        self.assertEqual(Path(temp_served.path).name, item["local_path"])

    async def test_canvas_add_file_requires_existing_file(self):
        """add_file 只接受白名单目录内真实存在的文件，拒绝悬空引用与绝对路径。"""
        data_file = self.plugin._plugin_data_dir / "asset_in_data.png"
        data_file.write_bytes(b"png")
        accepted = (self.touch_cache("cached.png").name, data_file.name)
        rejected = ("missing.png", "/tmp/astrbot_mmx_media/search_01.png")

        def add_file(name: str, index: int) -> None:
            web_request.reset(
                body={
                    "sessionId": "sess_files",
                    "operation": {
                        "schemaVersion": 1,
                        "operationId": f"op_file_{index}",
                        "mutations": [
                            {
                                "kind": "add_file",
                                "nodeId": f"node_file_{index}",
                                "relativePath": name,
                                "layout": {},
                            }
                        ],
                    },
                }
            )

        for index, name in enumerate(accepted):
            with self.subTest(accepted=name):
                add_file(name, index)
                result = await self.call("canvas/mutate")
                self.assertEqual(result["document"]["nodes"][-1]["file"]["relativePath"], name)

        for index, name in enumerate(rejected, start=len(accepted)):
            with self.subTest(rejected=name):
                add_file(name, index)
                self.assertEqual((await self.call("canvas/mutate")).status_code, 400)

        web_request.reset(query={"session_id": "sess_files"})
        document = await self.call("canvas/doc")
        self.assertEqual(
            [node["file"]["relativePath"] for node in document["nodes"]], list(accepted)
        )

    async def test_playground_demo_items_carry_no_local_path(self):
        """试验台预置与离线回退数据不得伪造 local_path，只有后端真实缓存的文件可置入画布。"""
        source = Path(__file__).resolve().parents[1] / "web/src/components/MediaPlayground.vue"
        if not source.is_file():
            self.skipTest(f"缺少前端源码: {source}")
        assignments = re.findall(r"\blocal_path\s*:[^\n]*", source.read_text(encoding="utf-8"))
        self.assertEqual(assignments, [])

    async def test_download_endpoints_only_serve_whitelisted_files(self):
        cache = self.plugin._cache_dir
        cache.mkdir(parents=True, exist_ok=True)
        media = cache / "mmx_web_media.png"
        media.write_bytes(b"png")
        data_file = self.plugin._plugin_data_dir / "auth-preview.png"
        data_file.write_bytes(b"png")

        web_request.reset(query={"path": media.name})
        served = await self.call("tools/download-temp")
        self.assertEqual(Path(served.path), media)
        self.assertEqual(served.filename, media.name)

        for path in (f"../{self.root.name}/secret.txt", "/etc/passwd", "nope.png"):
            with self.subTest(path=path):
                web_request.reset(query={"path": path})
                self.assertEqual(
                    (await self.call("tools/download-temp")).status_code, 404
                )

        web_request.reset(query={})
        self.assertEqual((await self.call("tools/download-temp")).status_code, 400)

        web_request.reset(query={"path": data_file.name})
        canvas_file = await self.call("canvas/download")
        self.assertEqual(Path(canvas_file.path), data_file)

    # ------------------------------------------------------------------ SSE

    async def test_events_stream_pushes_published_entries(self):
        response = await self.call("events")
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(self.web.events.subscriber_count, 1)

        stream = response.stream
        self.assertEqual(await stream.__anext__(), ": mcode-events\n\n")
        self.web.events.publish(
            source="matrix_mcp",
            level="ok",
            command="POST images_search_and_download",
            detail="3 张已缓存",
            latency_ms=120,
        )
        chunk = await stream.__anext__()
        self.assertTrue(chunk.startswith("data: "))
        entry = json.loads(chunk[len("data: ") :].strip())
        self.assertEqual(entry["command"], "POST images_search_and_download")
        self.assertEqual(entry["latency_ms"], 120)
        await stream.aclose()
        self.assertEqual(self.web.events.subscriber_count, 0)


if __name__ == "__main__":
    unittest.main()
