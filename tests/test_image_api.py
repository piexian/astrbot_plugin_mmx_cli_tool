"""ImageAPI 双路由测试：Matrix 优先、开放平台回退。"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from mmx.apis.image import ImageAPI
from mmx.client import MiniMaxClient
from mmx.errors import MiniMaxError
from mmx.matrix import MatrixAPI


def _enabled(value: bool):
    async def predicate() -> bool:
        return value

    return predicate


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


def _matrix_payload(urls=None, failures=None) -> dict:
    return {
        "total_requested": len(urls or []) + len(failures or []),
        "total_success": len(urls or []),
        "success_items": [{"output_url": u} for u in urls or []],
        "failed_items": failures or [],
    }


class ImageRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        client = getattr(self, "_client", None)
        if client is not None:
            await client._client.aclose()

    async def test_matrix_route_normalizes_and_keeps_metadata(self):
        matrix = _FakeMatrix(
            _matrix_payload(
                ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"]
            )
        )
        self._client = _open_client(lambda req: httpx.Response(500))
        api = ImageAPI(self._client, matrix, _enabled(True), "image-01")
        result = await api.generate("cat", n=2, aspect_ratio="1:1", resolution="2K")
        self.assertEqual(
            ["https://cdn.example.com/a.png", "https://cdn.example.com/b.png"],
            result["data"]["image_urls"],
        )
        self.assertEqual("matrix", result["meta"]["route"])
        self.assertEqual(2, result["meta"]["total_success"])
        self.assertEqual([], result["meta"]["failed_items"])
        self.assertEqual([(["cat", "cat"], "1:1", "2K")], matrix.calls)

    async def test_matrix_partial_failure_reported_in_meta(self):
        matrix = _FakeMatrix(
            _matrix_payload(
                ["https://cdn.example.com/a.png"],
                [{"error_msg": "content blocked"}],
            )
        )
        self._client = _open_client(lambda req: httpx.Response(500))
        api = ImageAPI(self._client, matrix, _enabled(True))
        result = await api.generate("cat", n=2)
        self.assertEqual(["https://cdn.example.com/a.png"], result["data"]["image_urls"])
        self.assertEqual(1, result["meta"]["total_success"])
        self.assertEqual([{"error_msg": "content blocked"}], result["meta"]["failed_items"])

    async def test_explicit_model_uses_open_route(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.read().decode())
            return httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )

        matrix = _FakeMatrix()
        self._client = _open_client(handler)
        api = ImageAPI(self._client, matrix, _enabled(True), "image-01")
        result = await api.generate("cat", model="image-01-live")
        self.assertEqual("image-01-live", seen["body"]["model"])
        self.assertEqual("open", result["meta"]["route"])
        self.assertEqual(["https://example.com/x.png"], result["data"]["image_urls"])
        self.assertEqual([], matrix.calls)

    async def test_no_login_falls_back_to_default_model(self):
        seen = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(req.read().decode())
            return httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )

        matrix = _FakeMatrix()
        self._client = _open_client(handler)
        api = ImageAPI(self._client, matrix, _enabled(False), "image-01")
        await api.generate("cat")
        self.assertEqual("image-01", seen["body"]["model"])
        self.assertEqual([], matrix.calls)

    async def test_watermark_and_optimizer_off_force_open_route(self):
        matrix = _FakeMatrix()
        self._client = _open_client(
            lambda req: httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )
        )
        api = ImageAPI(self._client, matrix, _enabled(True), "image-01")
        await api.generate("cat", aigc_watermark=True)
        await api.generate("cat", prompt_optimizer=False)
        self.assertEqual([], matrix.calls)

    async def test_resolution_rejected_when_route_falls_back(self):
        matrix = _FakeMatrix()
        self._client = _open_client(
            lambda req: httpx.Response(
                200, json={"data": {"image_urls": ["https://example.com/x.png"]}}
            )
        )
        api = ImageAPI(self._client, matrix, _enabled(False), "image-01")
        with self.assertRaisesRegex(MiniMaxError, "resolution"):
            await api.generate("cat", resolution="2K")

    async def test_common_parameter_validation(self):
        matrix = _FakeMatrix(_matrix_payload(["https://cdn.example.com/a.png"]))
        self._client = _open_client(lambda req: httpx.Response(500))
        api = ImageAPI(self._client, matrix, _enabled(True))
        for kwargs, message in (
            ({"prompt": "  "}, "prompt"),
            ({"prompt": "cat", "n": 10}, "n"),
            ({"prompt": "cat", "resolution": "8K"}, "resolution"),
        ):
            with self.assertRaisesRegex(MiniMaxError, message):
                await api.generate(**kwargs)

    async def test_matrix_all_failed_raises(self):
        matrix = _FakeMatrix(_matrix_payload([], [{"error_msg": "content blocked"}]))
        self._client = _open_client(lambda req: httpx.Response(500))
        api = ImageAPI(self._client, matrix, _enabled(True))
        with self.assertRaisesRegex(MiniMaxError, "content blocked"):
            await api.generate("cat")


class MatrixContractTests(unittest.IsolatedAsyncioTestCase):
    """Matrix image_synthesize 真实 HTTP 请求体与超时断言。"""

    async def asyncTearDown(self):
        await self._client._client.aclose()

    async def test_request_body_uses_output_file_and_timeout_600(self):
        seen: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.read().decode())
            return httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0, "status_msg": "success"},
                    "total_requested": 1,
                    "total_success": 1,
                    "success_items": [
                        {"output_url": "https://cdn.example.com/a.png"}
                    ],
                    "failed_items": [],
                },
            )

        self._client = MiniMaxClient(region="cn")
        self._client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
        original = self._client.request
        calls: list[dict] = []

        async def spy(*args, **kwargs):
            calls.append(kwargs)
            return await original(*args, **kwargs)

        self._client.request = spy
        async def creds():
            return "cn", "token"

        matrix = MatrixAPI(self._client, creds)
        await matrix.image_synthesize(["cat"], aspect_ratio="1:1", resolution="2K")

        self.assertIn("/mavis/api/v1/mcp/image_synthesize", seen["url"])
        (item,) = seen["body"]["requests"]
        self.assertEqual("cat", item["prompt"])
        self.assertEqual("slot_0.png", item["output_file"])
        self.assertNotIn("output_file_path", item)
        self.assertEqual("1:1", item["aspect_ratio"])
        self.assertEqual("2K", item["resolution"])
        self.assertEqual(600, calls[0]["timeout"])



class OpenH3RouteTests(unittest.IsolatedAsyncioTestCase):
    """开放平台 H3 路由（/v1/images/generations）测试。"""

    async def asyncTearDown(self):
        await self._client._client.aclose()

    def _make(self, handler, default_model="MiniMax-H3-Image"):
        seen: dict = {}
        calls: list[dict] = []

        def wrapped(req: httpx.Request) -> httpx.Response:
            seen["url"] = str(req.url)
            seen["body"] = json.loads(req.read().decode())
            return handler(req)

        self._client = _open_client(wrapped)
        original = self._client.request

        async def spy(*args, **kwargs):
            calls.append(kwargs)
            return await original(*args, **kwargs)

        self._client.request = spy
        api = ImageAPI(self._client, _FakeMatrix(), _enabled(False), default_model)
        return api, seen, calls

    async def test_h3_body_maps_params_and_timeout(self):
        api, seen, calls = self._make(
            lambda req: httpx.Response(
                200,
                json={
                    "data": [{"url": "https://example.com/h3.png", "size": "1024x1024"}],
                    "usage": {"generated_images": 1},
                    "request_id": "req-1",
                },
            )
        )
        result = await api.generate(
            "cat",
            n=2,
            aspect_ratio="1:1",
            resolution="2K",
            aigc_watermark=True,
        )
        body = seen["body"]
        self.assertIn("/v1/images/generations", seen["url"])
        self.assertEqual("MiniMax-H3-Image", body["model"])
        self.assertEqual(2, body["n"])
        self.assertEqual("1:1", body["aspect_ratio"])
        self.assertEqual("2K", body["area"])
        self.assertTrue(body["aigc_watermark"])
        self.assertNotIn("size", body)
        self.assertEqual(600, calls[0]["timeout"])
        self.assertEqual(["https://example.com/h3.png"], result["data"]["image_urls"])
        self.assertEqual("open", result["meta"]["route"])
        self.assertEqual(1, result["meta"]["total_success"])
        self.assertEqual("req-1", result["meta"]["request_id"])

    async def test_h3_custom_size_and_disable_enhanced_and_base64(self):
        api, seen, _ = self._make(
            lambda req: httpx.Response(
                200, json={"data": [{"b64_json": "aGk="}]}
            )
        )
        result = await api.generate(
            "cat",
            width=1024,
            height=768,
            response_format="base64",
            prompt_optimizer=False,
        )
        body = seen["body"]
        self.assertEqual("1024x768", body["size"])
        self.assertNotIn("aspect_ratio", body)
        self.assertEqual("b64_json", body["response_format"])
        self.assertTrue(body["disable_prompt_enhanced"])
        self.assertEqual(["aGk="], result["data"]["image_base64"])

    async def test_h3_partial_failure_reported(self):
        api, _, _ = self._make(
            lambda req: httpx.Response(
                200,
                json={
                    "data": [
                        {"url": "https://example.com/a.png"},
                        {"error": {"code": "1027", "message": "content safety"}},
                    ],
                    "usage": {"generated_images": 1},
                },
            )
        )
        result = await api.generate("cat", n=2)
        self.assertEqual(["https://example.com/a.png"], result["data"]["image_urls"])
        self.assertEqual(
            [{"code": "1027", "error_msg": "content safety"}],
            result["meta"]["failed_items"],
        )

    async def test_h3_rejects_seed_and_subject_reference(self):
        api, _, _ = self._make(
            lambda req: httpx.Response(200, json={"data": []})
        )
        with self.assertRaisesRegex(MiniMaxError, "seed"):
            await api.generate("cat", seed=42)
        with self.assertRaisesRegex(MiniMaxError, "subject_reference"):
            await api.generate("cat", subject_reference=[{"type": "character"}])

    async def test_h3_all_failed_raises(self):
        api, _, _ = self._make(
            lambda req: httpx.Response(
                200,
                json={
                    "data": [{"error": {"code": "1027", "message": "content safety"}}],
                    "usage": {"generated_images": 0},
                },
            )
        )
        with self.assertRaisesRegex(MiniMaxError, "content safety"):
            await api.generate("cat")

    async def test_explicit_h3_model_overrides_image01_default(self):
        api, seen, _ = self._make(
            lambda req: httpx.Response(
                200, json={"data": [{"url": "https://example.com/h3.png"}]}
            ),
            default_model="image-01",
        )
        await api.generate("cat", model="MiniMax-H3-Image-Highspeed", resolution="1K")
        self.assertIn("/v1/images/generations", seen["url"])
        self.assertEqual("MiniMax-H3-Image-Highspeed", seen["body"]["model"])
        self.assertEqual("1K", seen["body"]["area"])

if __name__ == "__main__":
    unittest.main()
