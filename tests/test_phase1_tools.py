"""第一阶段业务工具测试：搜图、以图搜图、音乐音效生成（Mock 驱动，无真实网络）。"""

from __future__ import annotations

import json

import httpx
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from support import plugin_modules

from astrbot_plugin_mmx_cli_tool.mmx.media_transfer import (
    MediaTransferError,
    UploadResult,
)


class _FakeMatrixAPI:
    """MatrixAPI 桩：按测试预置返回值记录调用。"""

    def __init__(self):
        self.search_results: list[dict] = []
        self.reverse_items: list[dict] = []
        self.audio_item: dict = {"output_url": "https://cdn.example.com/out.mp3"}
        self.calls: list[tuple] = []
        self.error: Exception | None = None

    async def images_search_and_download(self, query, prompt=None, providers=None):
        self.calls.append(("search", query, prompt, providers))
        if self.error:
            raise self.error
        return self.search_results

    async def image_reverse_search(self, image_url):
        self.calls.append(("reverse", image_url))
        if self.error:
            raise self.error
        return self.reverse_items

    async def batch_text_to_music(self, prompt, lyrics=None, audio_format="mp3"):
        self.calls.append(("music", prompt, lyrics, audio_format))
        if self.error:
            raise self.error
        return self.audio_item

    async def batch_text_to_audio(self, prompt):
        self.calls.append(("sfx", prompt))
        if self.error:
            raise self.error
        return self.audio_item


class _FakeMedia:
    """MatrixMediaClient 桩：下载直接写文件，上传返回固定 cdn_url。"""

    def __init__(self, fail_download: bool = False):
        self.downloads: list[tuple] = []
        self.uploads: list[tuple] = []
        self.fail_download = fail_download

    async def download_to_file(self, url, dest, *, allowed_dir, timeout=300):
        self.downloads.append((url, str(dest), str(allowed_dir)))
        if self.fail_download:
            raise MediaTransferError("download_failed", "模拟下载失败")
        path = Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"media-bytes")
        return path

    async def upload_file(self, path, *, force_remote_url=False):
        self.uploads.append((str(path), force_remote_url))
        return UploadResult(
            bytes=100,
            mime_type="image/png",
            oss_key="k1",
            cdn_url="https://cdn.example.com/uploaded.png",
        )


def _images(n: int) -> list[dict]:
    return [
        {
            "images": [
                {
                    "image_url": f"https://img.example.com/{i}.jpg",
                    "title": f"图 {i}",
                    "source_page_url": f"https://site.example.com/{i}",
                }
                for i in range(n)
            ]
        }
    ]


def _reverse_items(n: int) -> list[dict]:
    return [
        {
            "title": f"出处 {i}",
            "source": f"站点{i}",
            "link": f"https://site.example.com/page/{i}",
            "image_url": f"https://img.example.com/thumb/{i}.jpg",
            "snippet": f"摘要 {i}",
        }
        for i in range(n)
    ]


class Phase1ToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.cache = self.root / "cache"
        self.cache.mkdir()
        self._ctx = plugin_modules(self.root)
        self.main, self.tools_pkg = self._ctx.__enter__()
        self.api = _FakeMatrixAPI()
        self.media = _FakeMedia()

    def tearDown(self):
        self._ctx.__exit__(None, None, None)
        self._tmp.cleanup()

    def _payload(self, result) -> dict:
        return json.loads(result)

    # ---------- 搜图并下载 ----------

    async def test_search_images_success(self):
        self.api.search_results = _images(3)
        tool = self.tools_pkg.SearchImagesTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, query="柴犬", count=2))
        self.assertTrue(resp["ok"])
        self.assertEqual(2, resp["downloaded"])
        self.assertEqual(0, resp["failed"])
        self.assertEqual(2, len(resp["file_paths"]))
        for p in resp["file_paths"]:
            self.assertTrue(Path(p).is_file())
            self.assertEqual(self.cache.resolve(), Path(p).resolve().parent)
        self.assertIn("file_paths", resp["hint"])
        self.assertEqual(("search", "柴犬", None, None), self.api.calls[0])

    async def test_search_images_count_clamped(self):
        self.api.search_results = _images(8)
        tool = self.tools_pkg.SearchImagesTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, query="猫", count=99))
        self.assertLessEqual(len(resp["file_paths"]), 5)

    async def test_search_images_missing_query(self):
        tool = self.tools_pkg.SearchImagesTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, query="  "))
        self.assertFalse(resp["ok"])
        self.assertIn("query", resp["error"])

    async def test_search_images_no_results(self):
        self.api.search_results = [{"error": "rate limited"}]
        tool = self.tools_pkg.SearchImagesTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, query="不存在的东西"))
        self.assertFalse(resp["ok"])
        self.assertIn("rate limited", resp["error"])

    async def test_search_images_download_failure_falls_back_to_urls(self):
        self.api.search_results = _images(2)
        self.media.fail_download = True
        tool = self.tools_pkg.SearchImagesTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, query="猫"))
        self.assertTrue(resp["ok"])
        self.assertEqual(0, resp["downloaded"])
        self.assertEqual(2, resp["failed"])
        self.assertEqual(2, len(resp["image_urls"]))

    # ---------- 以图搜图 ----------

    async def test_reverse_search_with_url(self):
        self.api.reverse_items = _reverse_items(4)
        tool = self.tools_pkg.ImageReverseSearchTool(
            self.api, self.media, str(self.root), []
        )
        resp = self._payload(await tool.call(None, image="https://img.example.com/a.jpg"))
        self.assertTrue(resp["ok"])
        self.assertEqual(4, resp["count"])
        self.assertEqual(("reverse", "https://img.example.com/a.jpg"), self.api.calls[0])
        self.assertEqual([], self.media.uploads)
        self.assertIn("以图搜图结果", resp["text"])
        self.assertIn("出处 0", resp["text"])
        self.assertIn("https://site.example.com/page/0", resp["text"])
        # 卡片只展示 Top 3
        self.assertNotIn("出处 3", resp["text"])

    async def test_reverse_search_with_local_file_uploads_first(self):
        data_dir = self.root / "plugin_data"
        data_dir.mkdir()
        local = data_dir / "photo.png"
        local.write_bytes(b"\x89PNG")
        self.api.reverse_items = _reverse_items(1)
        tool = self.tools_pkg.ImageReverseSearchTool(
            self.api, self.media, str(data_dir), []
        )
        resp = self._payload(await tool.call(None, image=str(local)))
        self.assertTrue(resp["ok"])
        self.assertEqual(1, len(self.media.uploads))
        self.assertTrue(self.media.uploads[0][1])  # force_remote_url
        self.assertEqual(
            ("reverse", "https://cdn.example.com/uploaded.png"), self.api.calls[0]
        )

    async def test_reverse_search_rejects_out_of_scope_path(self):
        tool = self.tools_pkg.ImageReverseSearchTool(
            self.api, self.media, str(self.root / "nope"), []
        )
        resp = self._payload(await tool.call(None, image="/etc/passwd"))
        self.assertFalse(resp["ok"])
        self.assertEqual([], self.api.calls)

    async def test_reverse_search_rejects_non_image_files(self):
        """auth.json 等敏感文件不得被上传换取公开链接（提示词注入防线）。"""
        data_dir = self.root / "plugin_data"
        data_dir.mkdir()
        secret = data_dir / "auth.json"
        secret.write_text('{"access_token": "t"}')
        tool = self.tools_pkg.ImageReverseSearchTool(
            self.api, self.media, str(data_dir), []
        )
        resp = self._payload(await tool.call(None, image=str(secret)))
        self.assertFalse(resp["ok"])
        self.assertEqual([], self.media.uploads)
        self.assertEqual([], self.api.calls)

    async def test_reverse_search_trusted_local_path(self):
        """聊天提取的落盘图片走 trusted_local，跳过沙箱目录校验。"""
        outside = self.root / "chat.png"
        outside.write_bytes(b"\x89PNG")
        self.api.reverse_items = _reverse_items(1)
        tool = self.tools_pkg.ImageReverseSearchTool(
            self.api, self.media, str(self.root / "data"), []
        )
        # trusted_local 只能由指令层直调 resolve_image_url，不对 LLM 开放
        url = await tool.resolve_image_url(str(outside), trusted_local=True)
        self.assertEqual("https://cdn.example.com/uploaded.png", url)
        self.assertEqual(1, len(self.media.uploads))

    async def test_reverse_search_empty(self):
        self.api.reverse_items = []
        tool = self.tools_pkg.ImageReverseSearchTool(
            self.api, self.media, str(self.root), []
        )
        resp = self._payload(await tool.call(None, image="https://img.example.com/x.jpg"))
        self.assertTrue(resp["ok"])
        self.assertEqual(0, resp["count"])

    def test_format_reverse_results_markdown(self):
        from astrbot_plugin_mmx_cli_tool.tools.image_reverse_tools import (
            format_reverse_results,
        )

        text = format_reverse_results(_reverse_items(5))
        self.assertIn("🔍 以图搜图结果:", text)
        self.assertIn("**出处 0**", text)
        self.assertIn("来源: 站点0", text)
        self.assertIn("链接: https://site.example.com/page/0", text)
        self.assertIn("缩略图: https://img.example.com/thumb/0.jpg", text)
        self.assertIn("摘要: 摘要 0", text)
        self.assertIn("**出处 2**", text)
        self.assertNotIn("出处 3", text)

    # ---------- 音乐与音效生成 ----------

    async def test_generate_music_success(self):
        tool = self.tools_pkg.GenerateMusicTool(self.api, self.media, str(self.cache))
        resp = self._payload(
            await tool.call(None, prompt="轻快的 lo-fi", lyrics="啦啦啦", format="wav")
        )
        self.assertTrue(resp["ok"])
        self.assertEqual(("music", "轻快的 lo-fi", "啦啦啦", "wav"), self.api.calls[0])
        self.assertTrue(resp["file_path"].endswith(".wav"))
        self.assertTrue(Path(resp["file_path"]).is_file())

    async def test_generate_music_missing_prompt(self):
        tool = self.tools_pkg.GenerateMusicTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, prompt=""))
        self.assertFalse(resp["ok"])

    async def test_generate_music_invalid_format_defaults_mp3(self):
        tool = self.tools_pkg.GenerateMusicTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, prompt="史诗", format="flac"))
        self.assertTrue(resp["ok"])
        self.assertEqual("mp3", self.api.calls[0][3])
        self.assertTrue(resp["file_path"].endswith(".mp3"))

    async def test_generate_music_download_failure_returns_url(self):
        self.media.fail_download = True
        tool = self.tools_pkg.GenerateMusicTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, prompt="雨声"))
        self.assertTrue(resp["ok"])
        self.assertNotIn("file_path", resp)
        self.assertEqual("https://cdn.example.com/out.mp3", resp["audio_url"])

    async def test_generate_sound_effect_success(self):
        tool = self.tools_pkg.GenerateSoundEffectTool(self.api, self.media, str(self.cache))
        resp = self._payload(await tool.call(None, prompt="玻璃碎裂"))
        self.assertTrue(resp["ok"])
        self.assertEqual(("sfx", "玻璃碎裂"), self.api.calls[0])
        self.assertTrue(resp["file_path"].endswith(".mp3"))

    async def test_tools_propagate_api_errors(self):
        self.api.error = RuntimeError("boom")
        for cls, kwargs in (
            ("SearchImagesTool", {"query": "x"}),
            ("GenerateMusicTool", {"prompt": "x"}),
            ("GenerateSoundEffectTool", {"prompt": "x"}),
        ):
            tool = getattr(self.tools_pkg, cls)(self.api, self.media, str(self.cache))
            resp = self._payload(await tool.call(None, **kwargs))
            self.assertFalse(resp["ok"], cls)
            self.assertIn("boom", resp["error"])

    # ---------- 指令解析 ----------

    def test_parse_music_command(self):
        from astrbot_plugin_mmx_cli_tool.mmx.direct_command_args import (
            DirectCommandError,
            parse_music_command,
        )

        args = parse_music_command("史诗 管弦乐 --lyrics 前进吧 --format wav")
        self.assertEqual("史诗 管弦乐", args.prompt)
        self.assertEqual("前进吧", args.lyrics)
        self.assertEqual("wav", args.audio_format)
        with self.assertRaises(DirectCommandError):
            parse_music_command("")
        with self.assertRaises(DirectCommandError):
            parse_music_command("x --format flac")
        with self.assertRaises(DirectCommandError):
            parse_music_command("x --format pcm")  # 裸 PCM 平台无法播放，已移除

    def test_parse_image_search_command(self):
        from astrbot_plugin_mmx_cli_tool.mmx.direct_command_args import (
            DirectCommandError,
            parse_image_search_command,
        )

        args = parse_image_search_command("可爱的猫 --n 5")
        self.assertEqual("可爱的猫", args.query)
        self.assertEqual(5, args.n)
        args = parse_image_search_command("狗")
        self.assertEqual(3, args.n)
        with self.assertRaises(DirectCommandError):
            parse_image_search_command("")
        with self.assertRaises(DirectCommandError):
            parse_image_search_command("x --n 9")


class MatrixAPITests(unittest.IsolatedAsyncioTestCase):
    """Matrix 网关协议层：Bearer 注入、错误展开、401 刷新重试。"""

    def _api(self, outcomes, creds=("cn", "tok1"), refresher=None):
        from astrbot_plugin_mmx_cli_tool.mmx.matrix import MatrixAPI

        class _FakeClient:
            def __init__(self):
                self.requests: list[dict] = []

            async def request(self, method, url, **kwargs):
                self.requests.append({"method": method, "url": url, **kwargs})
                outcome = outcomes.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        client = _FakeClient()
        api = MatrixAPI(
            client,
            lambda: _async_return(creds),
            refresher,
        )
        return api, client

    async def test_no_credential_gives_login_hint(self):
        from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError

        api, client = self._api([], creds=("cn", ""))
        with self.assertRaises(MiniMaxError) as ctx:
            await api.image_reverse_search("https://x.example.com/a.jpg")
        self.assertEqual(ErrorCategory.AUTH, ctx.exception.category)
        self.assertIn("/mmx login", str(ctx.exception))
        self.assertEqual([], client.requests)

    async def test_bearer_injection_and_payload(self):
        api, client = self._api([httpx.Response(200, json={"data": [{"title": "t"}]})])
        items = await api.image_reverse_search("https://x.example.com/a.jpg")
        self.assertEqual([{"title": "t"}], items)
        req = client.requests[0]
        self.assertEqual("tok1", req["api_key_override"])
        self.assertEqual(
            "https://agent.minimax.cn/mavis/api/v1/mcp/image_reverse_search", req["url"]
        )
        self.assertEqual({"image_url": "https://x.example.com/a.jpg"}, req["body"])

    async def test_401_refresh_and_retry_once(self):
        from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError

        refresh_calls = []

        async def refresher():
            refresh_calls.append(1)
            return "cn", "tok2"

        api, client = self._api(
            [
                MiniMaxError(ErrorCategory.AUTH, "401"),
                httpx.Response(200, json={"data": []}),
            ],
            refresher=refresher,
        )
        items = await api.image_reverse_search("https://x.example.com/a.jpg")
        self.assertEqual([], items)
        self.assertEqual(1, len(refresh_calls))
        self.assertEqual(2, len(client.requests))
        self.assertEqual("tok2", client.requests[1]["api_key_override"])

    async def test_401_refresh_fails_gives_login_hint(self):
        from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError

        async def refresher():
            return "cn", ""

        api, _ = self._api(
            [MiniMaxError(ErrorCategory.AUTH, "401")], refresher=refresher
        )
        with self.assertRaises(MiniMaxError) as ctx:
            await api.image_reverse_search("https://x.example.com/a.jpg")
        self.assertIn("/mmx login", str(ctx.exception))

    async def test_402_quota_and_business_code(self):
        from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError

        api, _ = self._api([httpx.Response(200, json={"code": 402, "message": "x"})])
        with self.assertRaises(MiniMaxError) as ctx:
            await api.image_reverse_search("https://x.example.com/a.jpg")
        self.assertEqual(ErrorCategory.QUOTA, ctx.exception.category)

        api2, _ = self._api([httpx.Response(200, json={"code": 5001, "message": "bad"})])
        with self.assertRaises(MiniMaxError) as ctx:
            await api2.image_reverse_search("https://x.example.com/a.jpg")
        self.assertEqual(ErrorCategory.GENERAL, ctx.exception.category)
        self.assertIn("5001", str(ctx.exception))


async def _async_return(value):
    return value


if __name__ == "__main__":
    unittest.main()
