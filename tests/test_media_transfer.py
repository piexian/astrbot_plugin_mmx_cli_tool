"""媒体传输与安全路径沙箱测试（MockTransport，无真实网络）。"""

from __future__ import annotations

import asyncio
import base64
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mmx_cli_tool.mmx.media_transfer import (
    INLINE_LIMIT_BYTES,
    MAX_UPLOAD_BYTES,
    MatrixMediaClient,
    MediaTransferError,
    guess_mime_type,
    sanitize_filename,
)


class _FakeAPI:
    """MatrixAPI 桩：记录 get_upload_url 调用。"""

    def __init__(self, presign: dict | None = None):
        self.calls: list[dict] = []
        self._presign = presign or {
            "put_url": "https://oss.example.com/put/signed",
            "cdn_url": "https://cdn.example.com/file.png",
            "content_type": "image/png",
            "oss_key": "oss-key-1",
        }

    async def get_upload_url(self, filename: str, mime_type: str, size_bytes: int) -> dict:
        self.calls.append(
            {"filename": filename, "mime_type": mime_type, "size_bytes": size_bytes}
        )
        return self._presign


def _make_media(api, handler=None) -> MatrixMediaClient:
    transport = httpx.MockTransport(handler or (lambda req: httpx.Response(200)))
    return MatrixMediaClient(api, http=httpx.AsyncClient(transport=transport))


class HelpersTests(unittest.TestCase):
    def test_guess_mime_type(self):
        self.assertEqual("image/png", guess_mime_type("a.PNG"))
        self.assertEqual("audio/mpeg", guess_mime_type("b.mp3"))
        self.assertEqual("application/octet-stream", guess_mime_type("c.xyz"))

    def test_sanitize_filename(self):
        self.assertEqual("a_b-c1.png", sanitize_filename("a b-c1.png"))
        self.assertEqual("__.bin", sanitize_filename("..."))
        self.assertEqual("file", sanitize_filename(""))


class UploadTests(unittest.TestCase):
    def test_small_file_inline_base64(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "pic.png"
            raw = b"\x89PNG" + b"0" * 100
            target.write_bytes(raw)
            api = _FakeAPI()

            def forbidden(request):
                raise AssertionError("内联模式不应发起 HTTP 请求")

            media = _make_media(api, forbidden)
            result = asyncio.run(media.upload_file(target))
            self.assertEqual(raw, base64.b64decode(result.inline_data))
            self.assertEqual("image/png", result.mime_type)
            self.assertEqual(len(raw), result.bytes)
            self.assertFalse(result.cdn_url)
            self.assertEqual([], api.calls)

    def test_small_file_force_remote_uses_oss(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "pic.png"
            target.write_bytes(b"0" * 100)
            api = _FakeAPI()
            puts = []

            def handler(request: httpx.Request) -> httpx.Response:
                puts.append(request)
                return httpx.Response(200)

            media = _make_media(api, handler)
            result = asyncio.run(media.upload_file(target, force_remote_url=True))
            self.assertEqual(1, len(api.calls))
            self.assertEqual(1, len(puts))
            self.assertEqual("https://cdn.example.com/file.png", result.cdn_url)
            self.assertEqual("oss-key-1", result.oss_key)
            self.assertFalse(result.inline_data)

    def test_large_file_oss_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "big video.mp4"
            size = INLINE_LIMIT_BYTES + 1024
            target.write_bytes(b"x" * size)
            api = _FakeAPI()
            captured = {}

            def handler(request: httpx.Request) -> httpx.Response:
                captured["headers"] = request.headers
                captured["body_len"] = len(request.content)
                return httpx.Response(200)

            media = _make_media(api, handler)
            result = asyncio.run(media.upload_file(target))
            self.assertEqual("big_video.mp4", api.calls[0]["filename"])
            self.assertEqual("video/mp4", api.calls[0]["mime_type"])
            self.assertEqual(size, api.calls[0]["size_bytes"])
            self.assertEqual(str(size), captured["headers"]["content-length"])
            self.assertEqual("image/png", captured["headers"]["content-type"])
            self.assertEqual(size, captured["body_len"])
            self.assertEqual("https://cdn.example.com/file.png", result.cdn_url)

    def test_oss_http_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "big.png"
            target.write_bytes(b"x" * (INLINE_LIMIT_BYTES + 1))
            api = _FakeAPI()
            media = _make_media(api, lambda req: httpx.Response(403))
            with self.assertRaises(MediaTransferError) as ctx:
                asyncio.run(media.upload_file(target))
            self.assertEqual("upload_failed", ctx.exception.kind)

    def test_file_not_found_and_too_large(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = _make_media(_FakeAPI())
            with self.assertRaises(MediaTransferError) as ctx:
                asyncio.run(media.upload_file(Path(tmp) / "missing.png"))
            self.assertEqual("file_not_found", ctx.exception.kind)

            huge = Path(tmp) / "huge.bin"
            with huge.open("wb") as fp:
                fp.truncate(MAX_UPLOAD_BYTES + 1)
            with self.assertRaises(MediaTransferError) as ctx:
                asyncio.run(media.upload_file(huge))
            self.assertEqual("file_too_large", ctx.exception.kind)


class DownloadTests(unittest.TestCase):
    def test_streaming_download(self):
        payload = b"audio-bytes" * 10000

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=payload)

        with tempfile.TemporaryDirectory() as tmp:
            media = _make_media(_FakeAPI(), handler)
            dest = Path(tmp) / "sub" / "out.mp3"
            path = asyncio.run(
                media.download_to_file("https://cdn.example.com/a.mp3", dest, allowed_dir=tmp)
            )
            self.assertEqual(dest, path)
            self.assertEqual(payload, dest.read_bytes())

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = _make_media(_FakeAPI())
            with self.assertRaises(MediaTransferError) as ctx:
                asyncio.run(
                    media.download_to_file(
                        "https://x.example.com/a",
                        Path(tmp) / ".." / "evil.mp3",
                        allowed_dir=tmp,
                    )
                )
            self.assertEqual("path_not_allowed", ctx.exception.kind)

    def test_http_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = _make_media(_FakeAPI(), lambda req: httpx.Response(404))
            with self.assertRaises(MediaTransferError) as ctx:
                asyncio.run(
                    media.download_to_file(
                        "https://x.example.com/a", Path(tmp) / "a.mp3", allowed_dir=tmp
                    )
                )
            self.assertEqual("download_failed", ctx.exception.kind)


if __name__ == "__main__":
    unittest.main()
