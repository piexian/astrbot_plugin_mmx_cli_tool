"""ImageAPI 双路由测试：Matrix 优先、开放平台回退。"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mmx.apis.image import ImageAPI
from mmx.client import MiniMaxClient
from mmx.errors import MiniMaxError


class _FakeMatrix:
    """MatrixAPI 桩：记录调用并按预置负载返回。"""

    def __init__(self, data=None, error=None):
        self.data = data or {}
        self.error = error
        self.calls: list[tuple] = []

    async def image_synthesize(self, prompts, *, aspect_ratio=None, resolution=None):
        self.calls.append((prompts, aspect_ratio, resolution))
        if self.error:
            raise self.error
        return self.data


def _open_client(handler) -> MiniMaxClient:
    client = MiniMaxClient(api_key="test-key")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class ImageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        client = getattr(self, "_client", None)
        if client is not None:
            await client._client.aclose()

    async def test_matrix_route_normalizes_response(self):
        matrix = _FakeMatrix(
            {
                "total_requested": 2,
                "total_success": 2,
                "success_items": [
                    {"output_url": "https://cdn.example.com/a.png"},
                    {"output_url": "https://cdn.example.com/b.png"},
                ],
                "failed_items": [],
            }
        )
        self._client = _open_client(lambda req: httpx.Response(500))
        api = ImageAPI(self._client, matrix, lambda: True, "image-01")
        result = await api.generate("cat", n=2, aspect_ratio="1:1", resolution="2K")
        self.assertEqual(
            ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"],
            result["data"]["image_urls"],
        )
        self.assertEqual([(["cat", "cat"], "1:1", "2K")], matrix.calls)

    async def test_explicit_model_uses_open_route(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = req.read().decode()
            return httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )

        matrix = _FakeMatrix()
        self._client = _open_client(handler)
        api = ImageAPI(self._client, matrix, lambda: True, "image-01")
        result = await api.generate("cat", model="image-01-live")
        self.assertIn('"model":"image-01-live"', seen["body"])
        self.assertEqual(["https://example.com/x.png"], result["data"]["image_urls"])
        self.assertEqual([], matrix.calls)

    async def test_no_login_falls_back_to_default_model(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = req.read().decode()
            return httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )

        matrix = _FakeMatrix()
        self._client = _open_client(handler)
        api = ImageAPI(self._client, matrix, lambda: False, "image-01")
        await api.generate("cat")
        self.assertIn('"model":"image-01"', seen["body"])
        self.assertEqual([], matrix.calls)

    async def test_open_only_seed_skips_matrix(self):
        matrix = _FakeMatrix()
        self._client = _open_client(
            lambda req: httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )
        )
        api = ImageAPI(self._client, matrix, lambda: True, "image-01")
        await api.generate("cat", seed=42)
        self.assertEqual([], matrix.calls)

    async def test_matrix_all_failed_raises(self):
        matrix = _FakeMatrix(
            {
                "total_requested": 1,
                "total_success": 0,
                "success_items": [],
                "failed_items": [{"error_msg": "content blocked"}],
            }
        )
        self._client = _open_client(lambda req: httpx.Response(500))
        api = ImageAPI(self._client, matrix, lambda: True)
        with self.assertRaisesRegex(MiniMaxError, "content blocked"):
            await api.generate("cat")


if __name__ == "__main__":
    unittest.main()
