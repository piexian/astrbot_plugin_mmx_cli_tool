from __future__ import annotations

import json
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mmx_cli_tool.mmx.apis.transcription import TranscriptionOptions  # noqa: E402
from astrbot_plugin_mmx_cli_tool.mmx.attachment_input import extract_first_audio_input  # noqa: E402
from astrbot_plugin_mmx_cli_tool.mmx.direct_command_args import (  # noqa: E402
    DirectCommandError, parse_transcription_command,
)
from astrbot_plugin_mmx_cli_tool.mmx.transcription import TranscriptionService  # noqa: E402
from test_transcription_api import Chunks  # noqa: E402


class TranscriptionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data, self.cache = self.root / "data", self.root / "temp"
        self.data.mkdir()
        self.cache.mkdir()
        (self.data / "audio.mp3").write_bytes(b"audio")
        self.api = Mock(transcribe=AsyncMock(return_value={"text": "你好", "duration": 1}))
        self.service = TranscriptionService(self.api, str(self.data), str(self.cache), extra_allowed_dirs=[str(self.cache)])

    async def test_local_audio_and_opt_in_temp_input(self):
        result = await self.service.run("audio.mp3", TranscriptionOptions())
        self.assertEqual({"text": "你好", "duration": 1}, result["data"])
        self.assertNotIn("file_path", result)
        self.assertEqual((b"audio", "audio.mp3"), self.api.transcribe.call_args.args[:2])
        (self.cache / "temp.mp3").write_bytes(b"temp audio")
        await self.service.run("temp.mp3", TranscriptionOptions())
        restricted = TranscriptionService(self.api, str(self.data), str(self.cache))
        with self.assertRaises((ValueError, FileNotFoundError)):
            await restricted.run(str(self.cache / "temp.mp3"), TranscriptionOptions())

    async def test_outside_and_symlink_paths_rejected(self):
        outside = self.root / "outside.mp3"
        outside.write_bytes(b"private")
        (self.data / "link.mp3").symlink_to(outside)
        for source in (str(outside), "../outside.mp3", "link.mp3", outside.as_uri()):
            with self.subTest(source=source), self.assertRaises((ValueError, FileNotFoundError)):
                await self.service.run(source, TranscriptionOptions())
        self.api.transcribe.assert_not_awaited()
        await self.service.run(str(outside), TranscriptionOptions(), trusted_attachment=True)
        self.api.transcribe.assert_awaited_once()

    async def test_unsafe_output_rejected_before_api(self):
        (self.data / "link").symlink_to(self.cache, target_is_directory=True)
        for out in ("../outside.srt", str(self.cache / "outside.srt"), "link/out.srt", "file:///tmp/out", ".", ""):
            with self.subTest(out=out), self.assertRaises(ValueError):
                await self.service.run("audio.mp3", TranscriptionOptions(), out=out)
        self.api.transcribe.assert_not_awaited()

    async def test_subtitle_save_is_byte_exact_and_explicit_output_is_persistent(self):
        subtitle = "\ufeff1\r\n00:00:00,000 --> 00:00:01,000\r\n你好"
        self.api.transcribe.return_value = subtitle
        result = await self.service.run("audio.mp3", TranscriptionOptions(response_format="srt"))
        path = Path(result["file_path"])
        self.assertEqual(self.cache, path.parent)
        self.assertEqual(subtitle.encode(), path.read_bytes())
        result = await self.service.run("audio.mp3", TranscriptionOptions(response_format="srt"), out="transcripts/a.srt")
        self.assertEqual(self.data / "transcripts/a.srt", Path(result["file_path"]))
        self.assertEqual(subtitle.encode(), Path(result["file_path"]).read_bytes())

    async def test_verbose_and_long_text_saved_without_truncation(self):
        self.api.transcribe.return_value = {"text": "你好" * 2000, "duration": 100}
        result = await self.service.run("audio.mp3", TranscriptionOptions())
        self.assertEqual(self.api.transcribe.return_value, json.loads(Path(result["file_path"]).read_text()))
        self.api.transcribe.return_value = {"text": "hi", "duration": 1, "segments": [], "n_speakers": 0}
        result = await self.service.run("audio.mp3", TranscriptionOptions(response_format="verbose_json"))
        self.assertIn("segments", json.loads(Path(result["file_path"]).read_text()))

    async def test_save_failure_retains_paid_result(self):
        self.api.transcribe.return_value = "subtitle"
        with patch.object(self.service, "_save", side_effect=OSError("disk full")):
            result = await self.service.run("audio.mp3", TranscriptionOptions(response_format="srt"))
        self.assertTrue(result["ok"])
        self.assertEqual("subtitle", result["data"])
        self.assertIn("请勿重复", result["warning"])
        self.api.transcribe.assert_awaited_once()

    async def test_arbitrary_url_is_not_downloaded(self):
        with patch.object(self.service, "_download_attachment", new_callable=AsyncMock) as download:
            with self.assertRaises(ValueError):
                await self.service.run("https://example.test/audio.mp3", TranscriptionOptions())
            download.assert_not_awaited()
        self.api.transcribe.assert_not_awaited()

    async def test_file_limit_checked_before_read(self):
        with (self.data / "audio.mp3").open("rb") as audio:
            guarded = MagicMock(wraps=audio)
            guarded.__enter__.return_value = guarded
            with patch("astrbot_plugin_mmx_cli_tool.mmx.apis.transcription.MAX_AUDIO_BYTES", 4), patch.object(Path, "open", return_value=guarded):
                with self.assertRaises(ValueError):
                    await self.service.run("audio.mp3", TranscriptionOptions())
            guarded.read.assert_not_called()
        self.api.transcribe.assert_not_awaited()

    async def test_bounded_attachment_download_has_no_api_auth(self):
        requests = []
        stream = Chunks(b"audio")
        def handler(request):
            requests.append(request)
            return httpx.Response(200, stream=stream)
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("astrbot_plugin_mmx_cli_tool.mmx.transcription.httpx.AsyncClient", return_value=client):
            await self.service.run("https://example.test/audio.mp3", TranscriptionOptions(), trusted_attachment=True)
        self.assertTrue(stream.closed)
        self.assertNotIn("authorization", requests[0].headers)
        self.assertNotIn("x-api-key", requests[0].headers)
        self.assertEqual(b"audio", self.api.transcribe.call_args.args[0])

    async def test_attachment_redirects_are_rejected_before_follow_or_upload(self):
        real_client = httpx.AsyncClient
        source = "https://cdn.example.test/audio.mp3"
        destinations = (
            "http://127.0.0.1/private.wav", "http://10.0.0.1/private.wav",
            "https://other.example.test/audio.mp3", "/other.mp3",
        )
        for status in (301, 302, 303, 307, 308):
            for destination in destinations:
                with self.subTest(status=status, destination=destination):
                    requests = []
                    redirect_body = Chunks(b"must not read")
                    self.api.transcribe.reset_mock()

                    def handler(request):
                        requests.append(str(request.url))
                        if str(request.url) == source:
                            return httpx.Response(status, headers={"location": destination}, stream=redirect_body)
                        return httpx.Response(200, content=b"must not upload")

                    def client_factory(**kwargs):
                        return real_client(transport=httpx.MockTransport(handler), **kwargs)

                    with patch("astrbot_plugin_mmx_cli_tool.mmx.transcription.httpx.AsyncClient", side_effect=client_factory):
                        with self.assertRaisesRegex(ValueError, "重定向"):
                            await self.service.run(source, TranscriptionOptions(), trusted_attachment=True)
                    self.assertEqual([source], requests)
                    self.assertEqual(0, redirect_body.reads)
                    self.assertTrue(redirect_body.closed)
                    self.api.transcribe.assert_not_awaited()

    async def test_download_checks_header_and_chunked_size(self):
        for headers in ({"content-length": "100"}, {}):
            with self.subTest(headers=headers):
                stream = Chunks(b"oversized audio")
                client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, headers=headers, stream=stream)))
                with patch("astrbot_plugin_mmx_cli_tool.mmx.transcription.MAX_AUDIO_BYTES", 4), patch("astrbot_plugin_mmx_cli_tool.mmx.transcription.httpx.AsyncClient", return_value=client):
                    with self.assertRaises(ValueError):
                        await self.service.run("https://example.test/a.mp3", TranscriptionOptions(), trusted_attachment=True)
                self.assertTrue(stream.closed)
        self.api.transcribe.assert_not_awaited()

    async def test_attachment_download_timeout_closes_before_upload(self):
        stream = Chunks(None)
        client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        ))
        real_wait = asyncio.wait_for

        async def fast_timeout(coro, timeout):
            self.assertEqual(60, timeout)
            return await real_wait(coro, timeout=0.01)

        with patch("astrbot_plugin_mmx_cli_tool.mmx.transcription.httpx.AsyncClient", return_value=client), patch("astrbot_plugin_mmx_cli_tool.mmx.transcription.asyncio.wait_for", side_effect=fast_timeout):
            with self.assertRaises(TimeoutError):
                await self.service.run("https://example.test/a.mp3", TranscriptionOptions(), trusted_attachment=True)
        self.assertTrue(stream.closed)
        self.api.transcribe.assert_not_awaited()


class TranscriptionParserTests(unittest.TestCase):
    def test_options_and_positional_paths(self):
        args = parse_transcription_command('--file "meeting notes.mp3" --language zh --response-format verbose_json --timestamp-level word')
        self.assertEqual("meeting notes.mp3", args.file)
        self.assertEqual(("zh", "verbose_json", "word"), (args.language, args.response_format, args.timestamp_level))
        self.assertEqual("meeting.mp3", parse_transcription_command("meeting.mp3").file)
        self.assertIsNone(parse_transcription_command("").file)
        self.assertTrue(parse_transcription_command("--stream").stream)
        self.assertFalse(parse_transcription_command("--stream=false").stream)

    def test_invalid_options_and_stream_output_conflicts(self):
        for text in ("--unknown", "--stream --out x.txt", "--stream --response-format srt", "--response-format text", "--timestamp-level wrong", "--file"):
            with self.subTest(text=text), self.assertRaises(DirectCommandError):
                parse_transcription_command(text)


class AttachmentCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.audio = Path(temp.name) / "audio.mp3"
        self.audio.write_bytes(b"audio")

    class LegacyFile:
        def __init__(self, value):
            self._value = value
            self.calls = 0

        async def get_file(self):
            self.calls += 1
            return self._value

    async def resolve(self, component, *, prefer_url=True):
        return await extract_first_audio_input(
            [component], record_type=self.LegacyFile, file_type=self.LegacyFile,
            reply_type=type(None), prefer_url=prefer_url,
        )

    async def test_unsupported_legacy_getter_fails_without_unbounded_download(self):
        component = self.LegacyFile(str(self.audio))
        with self.assertRaisesRegex(ValueError, "无法安全解析") as caught:
            await self.resolve(component)
        self.assertIn("--file", str(caught.exception))
        self.assertEqual(0, component.calls)

    async def test_public_legacy_fields_do_not_require_getter(self):
        for name, value in (("file_", str(self.audio)), ("url", "https://cdn.example.test/audio.mp3")):
            with self.subTest(field=name):
                component = self.LegacyFile(str(self.audio))
                setattr(component, name, value)
                self.assertEqual((value, True), await self.resolve(component))
                self.assertEqual(0, component.calls)

    async def test_non_asr_legacy_fallback_unchanged(self):
        component = self.LegacyFile(str(self.audio))
        self.assertEqual((str(self.audio), True), await self.resolve(component, prefer_url=False))
        self.assertEqual(1, component.calls)
