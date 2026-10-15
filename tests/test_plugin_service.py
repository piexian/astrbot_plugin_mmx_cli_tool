"""Offline SDK contracts through real Main, facade, APIs and shared registry."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from support import plugin_modules


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.main, _ = self.enterContext(plugin_modules(self.root))
        self.tools = []
        self.context = SimpleNamespace(add_llm_tools=lambda *t: self.tools.extend(t))
        self.plugin = self.main.Main(
            self.context,
            {
                "api_key": ["fake-key"],
                "default_image_model": "image-01",
                "default_video_model": "MiniMax-H3",
                "default_speech_model": "speech-2.8-hd",
                "default_speech_voice": "configured",
            },
        )
        self.addAsyncCleanup(self.plugin.terminate)
        self.service = self.plugin.get_service()
        self.calls = []
        self.gate = None
        self.error = None

        async def respond(request):
            body = (
                json.loads(request.content)
                if request.headers.get("content-type", "").startswith(
                    "application/json"
                )
                else None
            )
            self.calls.append((request.method, request.url.path, body, request))
            if self.gate is not None:
                await self.gate.wait()
            if self.error:
                return httpx.Response(200, json=self.error)
            if "speech_to_text" in request.url.path:
                return httpx.Response(200, json={"text": "转写", "duration": 1})
            if "query/video" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "task": {
                            "status": "succeeded",
                            "content": {"url": "https://example.com/video"},
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "audio": "0102",
                        "image_urls": ["https://example.com/image"],
                    },
                    "task_id": "remote-task",
                    "base_resp": {"status_code": 0},
                    "trace_id": "trace",
                },
            )

        await self.plugin._client._client.aclose()
        self.plugin._client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(respond)
        )
        await self.plugin.initialize()
        self.audio = self.plugin._plugin_data_dir / "audio.mp3"
        self.audio.write_bytes(b"audio")

    def assert_code(self, code):
        class Check:
            def __enter__(inner):
                inner.context = self.assertRaises(RuntimeError)
                return inner.context.__enter__()

            def __exit__(inner, *args):
                result = inner.context.__exit__(*args)
                self.assertEqual(code, inner.context.exception.code)
                return result

        return Check()

    async def test_version_identity_and_snapshot(self):
        self.assertIs(self.service, self.plugin.get_service(1))
        for version in (True, False, "1", 1.0, 2, None):
            with self.assert_code("unsupported_version"):
                self.plugin.get_service(version)
        status = self.service.get_status()
        self.assertEqual("ready", status["state"])
        self.assertTrue(status["instance_id"])
        self.assertEqual(status, await self.service.wait_ready(0))
        status["state"] = "closed"
        cap = self.service.capabilities()
        self.assertEqual(12, len(cap["features"]))
        cap["features"].clear()
        self.assertEqual(12, len(self.service.capabilities()["features"]))
        self.assertEqual("ready", self.service.get_status()["state"])
        self.assertEqual(13, len(self.tools))
        for name in ("client", "store", "config", "close", "cancel_task"):
            self.assertFalse(hasattr(self.service, name))

    async def test_status_zero_side_effects_with_fail_fast_probes(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("status touched I/O or credentials")

        with (
            patch("builtins.open", side_effect=forbidden),
            patch.object(Path, "open", side_effect=forbidden),
            patch.object(Path, "stat", side_effect=forbidden),
            patch.object(self.plugin._client, "_resolve", side_effect=forbidden),
            patch.object(httpx.AsyncClient, "send", side_effect=forbidden),
            patch.object(self.main, "MiniMaxClient", side_effect=forbidden),
        ):
            for _ in range(3):
                self.assertTrue(self.service.get_status()["ready"])
                self.service.capabilities()
            self.assertTrue((await self.service.wait_ready(0))["ready"])
        self.assertEqual([], self.calls)

    async def test_initializing_missing_key_and_close_priority(self):
        other = self.main.Main(self.context, {"api_key": []})
        self.addAsyncCleanup(other.terminate)
        svc = other.get_service()
        self.assertEqual("initializing", svc.get_status()["state"])
        with self.assertRaises(TimeoutError):
            await svc.wait_ready(0)
        await other.initialize()
        self.assertEqual("missing_api_key", svc.get_status()["reason"])
        with self.assertRaises(TimeoutError):
            await svc.wait_ready(0.001)
        with self.assert_code("not_ready"):
            await svc.search("query")
        await other.terminate()
        with self.assert_code("service_closed"):
            await svc.wait_ready(0)
        await other.initialize()
        self.assertEqual("closed", svc.get_status()["state"])

    async def test_close_irreversible_even_cleanup_failure(self):
        close = AsyncMock(side_effect=RuntimeError("cleanup failed"))
        original = self.plugin._client.close
        self.plugin._client.close = close
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            await self.plugin.terminate()
        self.assertEqual("closed", self.service.get_status()["state"])
        await self.plugin.initialize()
        with self.assert_code("service_closed"):
            await self.service.generate_image("cat")
        with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
            await self.plugin.terminate()
        close.assert_awaited_once()
        await original()
        self.plugin._terminate_task = asyncio.create_task(asyncio.sleep(0))

    async def test_wait_ready_woken_by_close(self):
        other = self.main.Main(self.context, {})
        self.addAsyncCleanup(other.terminate)
        waiter = asyncio.create_task(other.get_service().wait_ready(10))
        await asyncio.sleep(0)
        await other.terminate()
        with self.assert_code("service_closed"):
            await waiter

    async def test_named_synchronous_api_orchestration(self):
        image = await self.service.generate_image(
            "cat", n=2, subject_ref="https://example.com/cat"
        )
        self.assertEqual("trace", image["trace_id"])
        self.assertEqual("image-01", self.calls[-1][2]["model"])
        self.assertEqual(2, self.calls[-1][2]["n"])
        await self.service.generate_video("scene", duration=5, ratio="16:9")
        self.assertEqual("MiniMax-H3", self.calls[-1][2]["model"])
        await self.service.get_video_task("remote-task", model="MiniMax-H3")
        final = await self.service.wait_video_task(
            "remote-task", model="MiniMax-H3", timeout=1
        )
        self.assertEqual("https://example.com/video", final["video_url"])
        speech = await self.service.synthesize_speech("hello", subtitles=True)
        self.assertEqual("0102", speech["data"]["audio"])
        self.assertEqual("configured", self.calls[-1][2]["voice_setting"]["voice_id"])
        await self.service.list_voices()
        await self.service.search("question")
        await self.service.describe_image(file_id="123")
        quota = await self.service.get_quota()
        self.assertEqual(1, quota["accounts"][0]["index"])
        await self.service.upload_file("audio.mp3")
        self.assertIn(b"audio", self.calls[-1][3].content)
        await self.service.list_files()
        await self.service.delete_file("123")
        self.assertEqual({"file_id": 123}, self.calls[-1][2])
        transcript = await self.service.transcribe(
            "audio.mp3", language="zh", out="result.json"
        )
        self.assertEqual("转写", transcript["data"]["text"])
        self.assertTrue(Path(transcript["file_path"]).is_file())
        json.dumps([image, final, speech, quota, transcript])
        self.assertEqual(13, len(self.calls))

    async def test_download_safe_generated_path(self):
        async def download(file_id, out_path, **kwargs):
            Path(out_path).write_bytes(b"video")
            return out_path

        with patch.object(self.plugin._video, "download", side_effect=download):
            result = await self.service.download_video("../../host")
            self.assertEqual(self.plugin._cache_dir, Path(result["file_path"]).parent)
            explicit = await self.service.download_video("123", out="videos/result.mp4")
            self.assertEqual(
                self.plugin._plugin_data_dir / "videos/result.mp4",
                Path(explicit["file_path"]),
            )

    async def test_upstream_failure_keeps_error_and_no_retry(self):
        self.error = {
            "base_resp": {"status_code": 1008, "status_msg": "insufficient balance"}
        }
        with self.assertRaises(Exception) as raised:
            await self.service.generate_image("cat")
        self.assertEqual(1008, raised.exception.api_code)
        self.assertEqual(1, len(self.calls))

    async def test_save_failure_retains_paid_transcription(self):
        with patch.object(
            self.plugin._transcription, "_save", side_effect=OSError("disk full")
        ):
            result = await self.service.transcribe("audio.mp3", out="result.json")
        self.assertEqual("转写", result["data"]["text"])
        self.assertIn("warning", result)
        self.assertEqual(1, len(self.calls))

    async def test_invalid_parameters_zero_paid_requests(self):
        calls = [
            lambda: self.service.generate_image(""),
            lambda: self.service.generate_image("cat", n=True),
            lambda: self.service.generate_image("cat", width=512),
            lambda: self.service.generate_image("cat", width=513, height=512),
            lambda: self.service.generate_image("cat", endpoint="/arbitrary"),
            lambda: self.service.generate_video(
                "cat", last_frame_image="https://example.com/x"
            ),
            lambda: self.service.generate_video(
                "cat", model="MiniMax-H3", subject_image="https://example.com/x"
            ),
            lambda: self.service.generate_video("cat", duration=100),
            lambda: self.service.generate_video("cat", ratio="bad"),
            lambda: self.service.generate_video(
                "cat", reference_audios=["https://example.com/a"]
            ),
            lambda: self.service.synthesize_speech(""),
            lambda: self.service.synthesize_speech("x" * 10001),
            lambda: self.service.search(""),
            lambda: self.service.describe_image(),
            lambda: self.service.transcribe("audio.mp3", stream=True, out="a.json"),
            lambda: self.service.transcribe("audio.mp3", response_format="bad"),
            lambda: self.service.delete_file("nonnumeric"),
        ]
        calls.extend(
            [
                lambda: self.service.generate_image("cat", model=123),
                lambda: self.service.generate_image("cat", seed=True),
                lambda: self.service.generate_video("cat", model=123),
                lambda: self.service.generate_video("cat", duration=True),
                lambda: self.service.synthesize_speech("hello", volume=float("nan")),
                lambda: self.service.synthesize_speech("hello", subtitles="true"),
                lambda: self.service.download_video("123", video_url="file:///secret"),
                lambda: self.service.delete_file(1.5),
            ]
        )
        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises((ValueError, TypeError)):
                    await call()
        self.assertEqual([], self.calls)

    async def test_paths_symlinks_and_trusted_flags_rejected(self):
        outside = self.root / "outside.mp3"
        outside.write_bytes(b"private")
        (self.plugin._plugin_data_dir / "link.mp3").symlink_to(outside)
        (self.plugin._plugin_data_dir / "outlink").symlink_to(
            self.root, target_is_directory=True
        )
        for name in (str(outside), "link.mp3", "../outside.mp3"):
            for call in (
                lambda: self.service.upload_file(name),
                lambda: self.service.transcribe(name),
                lambda: self.service.generate_image("cat", subject_ref=name),
                lambda: self.service.generate_video("scene", first_frame_image=name),
                lambda: self.service.describe_image(image=name),
            ):
                with (
                    self.subTest(path=name, call=call),
                    self.assertRaises((ValueError, FileNotFoundError)),
                ):
                    await call()
        for out in (str(outside), "../outside.mp3", "outlink/result.mp4"):
            with self.assertRaises(ValueError):
                await self.service.download_video("123", out=out)
        with self.assertRaises(ValueError):
            await self.service.transcribe("https://example.com/audio")
        with self.assertRaises(TypeError):
            await self.service.transcribe("audio.mp3", trusted_attachment=True)
        self.assertEqual([], self.calls)

    async def test_video_wait_timeout_never_generates_again(self):
        await self.service.generate_video("scene")
        with self.assertRaises(TimeoutError):
            await self.service.wait_video_task("remote-task", timeout=0)
        self.assertEqual(1, len(self.calls))

    async def test_multi_key_state_queries_do_not_rotate(self):
        other = self.main.Main(self.context, {"api_key": ["fake-one", "fake-two"]})
        self.addAsyncCleanup(other.terminate)
        await other.initialize()
        svc = other.get_service()

        def forbidden(*args, **kwargs):
            raise AssertionError("status rotated or inspected keys")

        with (
            patch.object(other._key_pool, "get_key", side_effect=forbidden),
            patch.object(other._key_pool, "get_summary", side_effect=forbidden),
            patch.object(other._client, "_key_getter", side_effect=forbidden),
            patch.object(other, "get_kv_data", side_effect=forbidden, create=True),
        ):
            self.assertTrue(svc.get_status()["ready"])
            svc.capabilities()
            await svc.wait_ready(0)
        self.assertEqual(0, sum(s.total_requests for s in other._key_pool._states))

    async def test_termination_caller_cancel_does_not_interrupt_cleanup(self):
        gate = asyncio.Event()
        original = self.plugin._client.close

        async def close():
            await gate.wait()
            await original()

        with patch.object(self.plugin._client, "close", side_effect=close):
            terminate = asyncio.create_task(self.plugin.terminate())
            await asyncio.sleep(0)
            terminate.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await terminate
            self.assertEqual("closing", self.service.get_status()["state"])
            with self.assert_code("service_closed"):
                await self.service.wait_ready(0)
            gate.set()
            await self.plugin.terminate()
        self.assertEqual("closed", self.service.get_status()["state"])

    async def test_actual_video_download_and_vision_http_mock(self):
        async def respond(request):
            return httpx.Response(
                200, content=b"media", headers={"content-type": "image/png"}
            )

        original_client = httpx.AsyncClient

        def client(*args, **kwargs):
            return original_client(
                *args, transport=httpx.MockTransport(respond), **kwargs
            )

        with patch("httpx.AsyncClient", side_effect=client):
            result = await self.service.download_video(
                "", video_url="https://example.com/video"
            )
            self.assertEqual(b"media", Path(result["file_path"]).read_bytes())
            await self.service.describe_image(image="https://example.com/image")
        self.assertTrue(
            self.calls[-1][2]["image_url"].startswith("data:image/png;base64,")
        )

    async def test_initialize_wakes_waiter_and_cannot_reactivate(self):
        other = self.main.Main(self.context, {"api_key": ["fake"]})
        self.addAsyncCleanup(other.terminate)
        svc = other.get_service()
        waiter = asyncio.create_task(svc.wait_ready(1))
        await asyncio.sleep(0)
        await other.initialize()
        self.assertEqual(svc.get_status(), await waiter)
        await other.terminate()
        await other.initialize()
        self.assertEqual("closed", svc.get_status()["state"])

    async def test_temp_inputs_respect_configuration(self):
        temporary_audio = self.plugin._cache_dir / "cached.mp3"
        temporary_audio.write_bytes(b"audio")
        await self.service.upload_file(str(temporary_audio))
        other = self.main.Main(
            self.context, {"api_key": ["fake"], "allow_astrbot_temp_dir": False}
        )
        self.addAsyncCleanup(other.terminate)
        await other.initialize()
        other._client.request_json = AsyncMock(
            side_effect=AssertionError("paid request")
        )
        with self.assertRaises(ValueError):
            await other.get_service().upload_file(str(temporary_audio))
        other._client.request_json.assert_not_awaited()

    async def test_shared_video_mode_validation_preserves_tool_hints(self):
        tool = next(t for t in self.tools if t.name == "mmx_generate_video")
        for tool_options, sdk_options in (
            (
                {"lastFrame": "https://example.com/x"},
                {"last_frame_image": "https://example.com/x"},
            ),
            (
                {
                    "firstFrame": "https://example.com/x",
                    "subjectImage": "https://example.com/y",
                },
                {
                    "first_frame_image": "https://example.com/x",
                    "subject_image": "https://example.com/y",
                },
            ),
            ({"model": "I2V-Fast"}, {"model": "I2V-Fast"}),
            (
                {"model": "MiniMax-H3", "subjectImage": "https://example.com/x"},
                {"model": "MiniMax-H3", "subject_image": "https://example.com/x"},
            ),
        ):
            result = json.loads(await tool.call(None, prompt="scene", **tool_options))
            self.assertFalse(result["ok"])
            with self.assertRaises(ValueError) as raised:
                await self.service.generate_video("scene", **sdk_options)
            self.assertEqual(result["error"], str(raised.exception))
        self.assertEqual([], self.calls)

    async def test_quota_failure_drains_other_owned_requests(self):
        other = self.main.Main(self.context, {"api_key": ["fake-one", "fake-two"]})
        self.addAsyncCleanup(other.terminate)
        await other.initialize()
        started = asyncio.Event()
        stopped = asyncio.Event()

        async def info(key):
            if key == "fake-one":
                await started.wait()
                raise RuntimeError("quota failed")
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with patch.object(other._quota, "info", side_effect=info):
            with self.assertRaisesRegex(RuntimeError, "quota failed"):
                await other.get_service().get_quota()
            self.assertTrue(stopped.is_set())
        self.assertFalse(other.get_service()._active)

    async def test_close_cancels_admitted_sync_request(self):
        self.gate = asyncio.Event()
        call = asyncio.create_task(self.service.search("query"))
        await asyncio.sleep(0)
        await self.plugin.terminate()
        with self.assertRaises(asyncio.CancelledError):
            await call
        self.assertEqual("closed", self.service.get_status()["state"])
        self.assertEqual(set(), self.service._active)

    async def test_parent_review_close_does_not_wait_for_consumer_continuation(self):
        started = asyncio.Event()
        interrupted = asyncio.Event()
        continuation = asyncio.Event()
        self.gate = asyncio.Event()
        original = self.plugin._search.query

        async def query(text):
            started.set()
            return await original(text)

        async def consumer():
            try:
                await self.service.search("query")
            except asyncio.CancelledError:
                interrupted.set()
            await continuation.wait()

        with patch.object(self.plugin._search, "query", side_effect=query):
            caller = asyncio.create_task(consumer())
            await asyncio.wait_for(started.wait(), 1)
            try:
                await asyncio.wait_for(self.plugin.terminate(), 0.2)
                self.assertTrue(interrupted.is_set())
                self.assertFalse(caller.done())
                self.assertEqual("closed", self.service.get_status()["state"])
            finally:
                continuation.set()
                self.gate.set()
                await asyncio.gather(caller, return_exceptions=True)
                await self.plugin.terminate()

    async def test_parent_review_consistent_not_ready_code(self):
        other = self.main.Main(self.context, {"api_key": []})
        self.addAsyncCleanup(other.terminate)
        for phase in ("initializing", "unavailable"):
            if phase == "unavailable":
                await other.initialize()
            with self.subTest(phase=phase):
                with self.assert_code("not_ready"):
                    await other.get_service().search("query")
        self.assertEqual([], self.calls)
