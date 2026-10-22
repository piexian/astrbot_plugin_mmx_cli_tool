"""MCode 工作台 Web API 路由测试：契约字段、幂等、白名单与降级（全部 Mock，无真实网络）。"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from support import UploadFile, WebApiContext, plugin_modules, web_request

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
        kwargs = described.call_args.kwargs
        self.assertEqual(kwargs["image"], "a.png")
        self.assertEqual(kwargs["data_dir"], str(self.plugin._plugin_data_dir))
        self.assertIn("保持暖色调", kwargs["prompt"])

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
