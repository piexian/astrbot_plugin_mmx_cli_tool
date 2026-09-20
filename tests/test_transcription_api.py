from __future__ import annotations

import asyncio
import json
import sys
import unittest
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mmx_cli_tool.mmx.apis.transcription import (  # noqa: E402
    SpeechToTextAPI, TranscriptionOptions, validate_audio_size,
)
from astrbot_plugin_mmx_cli_tool.mmx.client import MiniMaxClient  # noqa: E402
from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError  # noqa: E402


def event(index=0, delta="", finish=False, **extra):
    return ("data: " + json.dumps({"index": index, "delta": delta, "finish": finish, **extra}) + "\n\n").encode()


class Chunks(httpx.AsyncByteStream):
    def __init__(self, *chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False
        self.started = asyncio.Event()
        self.block = asyncio.Event()

    async def __aiter__(self):
        self.started.set()
        for chunk in self.chunks:
            self.reads += 1
            if chunk is None:
                await self.block.wait()
            elif isinstance(chunk, Exception):
                raise chunk
            else:
                yield chunk

    async def aclose(self):
        self.closed = True


class TranscriptionAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.response = httpx.Response(200, json={"text": "你好", "duration": 1.2, "trace_id": "test"})
        self.key_getter = AsyncMock(return_value=("test-key", 0))
        self.client = MiniMaxClient(key_getter=self.key_getter, base_url="https://api.test/gateway")
        await self.client._client.aclose()
        self.client._client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle))
        self.addAsyncCleanup(self.client.close)
        self.api = SpeechToTextAPI(self.client)

    async def handle(self, request):
        await request.aread()
        self.requests.append(request)
        return self.response

    def stream_response(self, *chunks, content_type="text/event-stream"):
        stream = Chunks(*chunks)
        self.response = httpx.Response(200, headers={"content-type": content_type}, stream=stream)
        return stream

    async def test_multipart_and_header_contract(self):
        result = await self.api.transcribe(b"audio", "meeting.mp3", TranscriptionOptions(language="zh", timestamp_level="word"))
        self.assertEqual("你好", result["text"])
        request = self.requests[0]
        self.assertEqual("https://api.test/gateway/v1/speech_to_text", str(request.url))
        self.assertEqual("zh", request.headers["language"])
        self.assertEqual("Bearer test-key", request.headers["authorization"])
        self.key_getter.assert_awaited_once_with("asr-1.0")
        message = BytesParser().parsebytes(("Content-Type: " + request.headers["content-type"] + "\r\n\r\n").encode() + request.content)
        fields = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in message.get_payload()}
        self.assertEqual({"model": b"asr-1.0", "response_format": b"json", "timestamp_level": b"word", "file": b"audio"}, fields)
        self.assertNotIn("language", fields)

    async def test_verbose_json_preserves_speakers_and_segments(self):
        data = {"text": "hello", "duration": 1, "n_speakers": 1, "segments": [{"id": 0, "start": 0, "end": 1, "speaker": "0", "text": "hello"}]}
        self.response = httpx.Response(200, json=data)
        self.assertEqual(data, await self.api.transcribe(b"audio", "a.wav", TranscriptionOptions(response_format="verbose_json")))

    async def test_subtitles_are_not_json_and_preserve_bytes(self):
        for fmt, kind in (("srt", "text/plain"), ("vtt", "text/vtt")):
            content = "\ufeff字幕\r\n第二行".encode()
            self.response = httpx.Response(200, headers={"content-type": kind}, content=content)
            result = await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(response_format=fmt))
            self.assertEqual(content, result.encode())

    async def test_invalid_options_fail_before_request(self):
        options = [
            TranscriptionOptions(response_format="text"), TranscriptionOptions(stream=True, response_format="srt"),
            TranscriptionOptions(stream="true"), TranscriptionOptions(language="zh\r\nInjected: header"),
            TranscriptionOptions(model=""), TranscriptionOptions(timestamp_level="invalid"),
        ]
        for value in options:
            with self.subTest(options=value), self.assertRaises(ValueError):
                await self.api.transcribe(b"audio", "a.mp3", value)
        self.assertFalse(self.requests)

    async def test_size_checked_at_api_boundary(self):
        with patch("astrbot_plugin_mmx_cli_tool.mmx.apis.transcription.MAX_AUDIO_BYTES", 5):
            validate_audio_size(5)
            for audio in (b"", b"123456"):
                with self.assertRaises(ValueError):
                    await self.api.transcribe(audio, "a.mp3", TranscriptionOptions())
        self.assertFalse(self.requests)

    async def test_stream_stops_at_finish_and_releases_connection(self):
        chunks = self.stream_response(event(0, "你"), event(1, "好"), event(2, finish=True, duration=2), b"must not read")
        result = await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
        self.assertEqual({"text": "你好", "duration": 2}, result)
        self.assertEqual(3, chunks.reads)
        self.assertTrue(chunks.closed)
        self.assertFalse(self.client._streams)
        self.assertIn(b'name="stream"\r\n\r\ntrue', self.requests[0].content)

    async def test_stream_orders_deltas_by_index_and_accepts_eof_event(self):
        chunks = self.stream_response(b": comment\n\n" + event(1, "B"), event(0, "A"), event(2, finish=True, duration=2).rstrip(b"\n"))
        result = await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
        self.assertEqual("AB", result["text"])
        self.assertTrue(chunks.closed)

    async def test_bad_and_incomplete_streams_fail_closed(self):
        cases = [
            [event(0, "partial")], [b"data: [DONE]\n\n"], [b"data: not-json\n\n"],
            [b"data: []\n\n"], [event(1, "gap", True, duration=1)],
            [event(0, "A"), event(0, finish=True, duration=1)],
            [event(0, finish=True)], [event(-1, finish=True, duration=1)],
            [event(True, finish=True, duration=1)], [event(0, finish=True, duration=-1)],
        ]
        for case in cases:
            with self.subTest(case=case):
                chunks = self.stream_response(*case)
                with self.assertRaises(MiniMaxError):
                    await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
                self.assertTrue(chunks.closed)
                self.assertFalse(self.client._streams)

    async def test_cancellation_and_timeout_close_stream(self):
        chunks = self.stream_response(None)
        task = asyncio.create_task(self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True)))
        await chunks.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(chunks.closed)
        chunks = self.stream_response(httpx.ReadTimeout("test timeout"))
        with self.assertRaises(httpx.ReadTimeout):
            await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
        self.assertTrue(chunks.closed)

    async def test_non_sse_and_stream_business_errors(self):
        chunks = self.stream_response(b'{"text":"not SSE","duration":1}', content_type="application/json")
        with self.assertRaisesRegex(MiniMaxError, "SSE"):
            await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
        self.assertTrue(chunks.closed)
        chunks = self.stream_response(b'data: {"base_resp":{"status_code":1026,"status_msg":"sensitive"}}\n\n')
        with self.assertRaises(MiniMaxError) as caught:
            await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
        self.assertEqual(ErrorCategory.CONTENT_FILTER, caught.exception.category)
        self.assertTrue(chunks.closed)

    async def test_http_errors_and_subtitle_business_errors(self):
        for status, category in ((413, ErrorCategory.USAGE), (422, ErrorCategory.CONTENT_FILTER), (429, ErrorCategory.QUOTA)):
            self.response = httpx.Response(status, json={"error": {"message": "test server error"}})
            with self.assertRaises(MiniMaxError) as caught:
                await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(stream=True))
            self.assertEqual(category, caught.exception.category)
            self.assertTrue(self.response.is_closed)
        self.response = httpx.Response(200, json={"base_resp": {"status_code": 1027, "status_msg": "sensitive"}})
        with self.assertRaises(MiniMaxError) as caught:
            await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(response_format="srt"))
        self.assertEqual(ErrorCategory.CONTENT_FILTER, caught.exception.category)

    async def test_missing_json_fields_and_wrong_subtitle_type(self):
        self.response = httpx.Response(200, json={"text": "partial"})
        with self.assertRaises(MiniMaxError):
            await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions())
        self.response = httpx.Response(200, headers={"content-type": "text/html"}, text="<html>error</html>")
        with self.assertRaises(MiniMaxError):
            await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(response_format="srt"))

    async def test_client_raw_context_and_termination_cleanup(self):
        chunks = self.stream_response(event(0, finish=True, duration=0))
        async with self.client.stream("GET", "/stream") as response:
            self.assertEqual(0, chunks.reads)
            self.assertFalse(response.is_closed)
        self.assertTrue(chunks.closed)
        chunks = self.stream_response(event(0, finish=True, duration=0))
        await self.client.request("GET", "/stream", stream=True)
        await self.client.close()
        self.assertTrue(chunks.closed)

    async def test_empty_language_and_legacy_request_contract(self):
        await self.api.transcribe(b"audio", "a.mp3", TranscriptionOptions(language=""))
        self.assertNotIn("language", self.requests[0].headers)
        self.key_getter.reset_mock()
        await self.client.request_json(
            "POST", "/legacy", body={"prompt": "hello"},
            auth_style="x-api-key", api_key_override="override-test-key",
            headers={"anthropic-version": "2023-06-01"},
        )
        request = self.requests[-1]
        self.assertEqual({"prompt": "hello"}, json.loads(request.content))
        self.assertEqual("application/json", request.headers["content-type"])
        self.assertEqual("override-test-key", request.headers["x-api-key"])
        self.assertNotIn("authorization", request.headers)
        self.key_getter.assert_not_awaited()

    async def test_invalid_json_is_a_structured_error(self):
        for response in (httpx.Response(200, text="not JSON"), httpx.Response(200, json=[])):
            self.response = response
            with self.assertRaises(MiniMaxError):
                await self.client.request_json("GET", "/test")
