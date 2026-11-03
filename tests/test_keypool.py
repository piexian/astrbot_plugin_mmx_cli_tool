"""KeyPool 额度感知选择、池同步与客户端接线测试（无真实网络）。"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from astrbot_plugin_mmx_cli_tool.mmx.client import MiniMaxClient
from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError
from astrbot_plugin_mmx_cli_tool.mmx.keypool import KeyPool, unique_keys
from astrbot_plugin_mmx_cli_tool.mmx.quota_usage import resolve_quota_model


def quota_item(name: str, remaining: int) -> dict:
    """额度接口返回的单模型条目（字段对齐 Token Plan）。"""
    return {
        "model_name": name,
        "current_interval_total_count": 100,
        "current_interval_usage_count": 100 - remaining,
        "current_interval_remaining_count": remaining,
        "current_interval_remaining_percent": remaining,
        "current_interval_status": 1,
        "remains_time": 3_600_000,
        "start_time": 0,
        "end_time": 43_200_000,
    }


def quota_fetcher(quotas: dict[str, dict[str, int]], calls: list[str] | None = None):
    """按 Key 返回额度数据的 fetcher；calls 记录被查询的 Key。"""

    async def fetch(key: str) -> dict:
        if calls is not None:
            calls.append(key)
        return {
            "model_remains": [
                quota_item(name, remaining)
                for name, remaining in quotas[key].items()
            ]
        }

    return fetch


async def swap_transport(client: MiniMaxClient, response: httpx.Response) -> None:
    """换掉客户端底层传输层，避免测试发真实请求。"""
    await client._client.aclose()
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: response)
    )


class ResolveQuotaModelTests(unittest.TestCase):
    def test_maps_request_models_to_quota_names(self):
        cases = [
            ("MiniMax-H3", ["video"], "video"),
            ("MiniMax-Hailuo-2.3", ["MiniMax-Hailuo-2.3", "general"], "MiniMax-Hailuo-2.3"),
            ("image-01", {"image"}, "image"),
            ("speech-2.8-hd", ["audio"], "audio"),
            ("MiniMax-Text-01", ["general"], "general"),
            ("MiniMax-M3", ["general"], ""),  # 无关键字线索时不做猜测
        ]
        for model, known, expected in cases:
            with self.subTest(model=model, known=known):
                self.assertEqual(expected, resolve_quota_model(model, known))

    def test_unmatched_model_degrades_to_unknown(self):
        self.assertEqual("", resolve_quota_model("mystery-model", ["general"]))
        self.assertEqual("", resolve_quota_model("MiniMax-H3", ["general"]))
        self.assertEqual("", resolve_quota_model("", ["general"]))

    def test_exact_name_wins_over_family(self):
        self.assertEqual(
            "text-extra",
            resolve_quota_model("text-extra", ["general", "text-extra"]),
        )


class SelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_selects_key_with_most_model_quota(self):
        pool = KeyPool(["key-a", "key-b"])
        fetcher = quota_fetcher(
            {
                "key-a": {"video": 30},
                "key-b": {"video": 75},
            }
        )
        await pool.refresh_if_stale(fetcher, force=True)

        key, index = await pool.get_key("MiniMax-H3")

        self.assertEqual(("key-b", 1), (key, index))

    async def test_unknown_quota_keeps_round_robin(self):
        pool = KeyPool(["key-a", "key-b"])

        picked = {(await pool.get_key("MiniMax-H3"))[0] for _ in range(2)}

        self.assertEqual({"key-a", "key-b"}, picked)

    async def test_all_models_exhausted_disables_key_and_reports(self):
        pool = KeyPool(["key-a", "key-b"])
        await pool.refresh_if_stale(
            quota_fetcher({"key-a": {"video": 0}, "key-b": {"video": 0}}), force=True
        )

        self.assertEqual(0, pool.enabled_count())
        summary = pool.get_summary()["keys"]
        self.assertFalse(summary[0]["enabled"])
        self.assertIn("额度", summary[0]["disabled_reason"])
        with self.assertRaises(RuntimeError) as ctx:
            await pool.get_key("MiniMax-H3")
        self.assertIn("额度已用尽", str(ctx.exception))

    async def test_disabled_key_recovers_after_cooldown(self):
        pool = KeyPool(["key-a"])
        await pool.refresh_if_stale(quota_fetcher({"key-a": {"video": 0}}), force=True)
        self.assertFalse(pool.get_summary()["keys"][0]["enabled"])

        pool._states[0].disabled_time = time.time() - KeyPool.DISABLED_COOLDOWN - 1
        await pool.refresh_if_stale(quota_fetcher({"key-a": {"video": 20}}), force=True)

        self.assertTrue(pool.get_summary()["keys"][0]["enabled"])
        self.assertEqual(("key-a", 0), await pool.get_key("MiniMax-H3"))

    async def test_refresh_if_stale_short_circuits_within_ttl(self):
        calls: list[str] = []
        pool = KeyPool(["key-a"])
        fetcher = quota_fetcher({"key-a": {"video": 10}}, calls)

        await pool.refresh_if_stale(fetcher)
        await pool.refresh_if_stale(fetcher)
        self.assertEqual(1, len(calls))

        pool._quotas_refreshed_at = 0.0
        await pool.refresh_if_stale(fetcher)
        self.assertEqual(2, len(calls))


class MarkingTests(unittest.IsolatedAsyncioTestCase):
    async def test_mark_model_exhausted_only_zeroes_target_model(self):
        pool = KeyPool(["key-a"])
        await pool.refresh_if_stale(
            quota_fetcher({"key-a": {"video": 30, "general": 40}}), force=True
        )

        await pool.mark_model_exhausted(0, "MiniMax-H3")

        quotas = pool.get_summary()["keys"][0]["model_quotas"]
        self.assertEqual(0, quotas["video"]["remaining"])
        self.assertEqual(40, quotas["general"]["remaining"])
        self.assertTrue(pool.get_summary()["keys"][0]["enabled"])

    async def test_mark_model_exhausted_ignores_unknown_model(self):
        pool = KeyPool(["key-a"])
        await pool.refresh_if_stale(
            quota_fetcher({"key-a": {"general": 40}}), force=True
        )

        await pool.mark_model_exhausted(0, "mystery-model")

        quotas = pool.get_summary()["keys"][0]["model_quotas"]
        self.assertEqual(40, quotas["general"]["remaining"])
        self.assertTrue(pool.get_summary()["keys"][0]["enabled"])

    async def test_mark_key_invalid_disables_key(self):
        pool = KeyPool(["key-a", "key-b"])

        await pool.mark_key_invalid(1)

        summary = pool.get_summary()["keys"][1]
        self.assertFalse(summary["enabled"])
        self.assertEqual("API Key 无效或已过期", summary["disabled_reason"])
        self.assertEqual(1, summary["failed_requests"])


class PoolSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_unique_keys_trims_and_dedupes(self):
        self.assertEqual(["a", "b"], unique_keys([" a ", "a", "", "b", " a"]))

    def test_set_keys_keeps_existing_state_and_indexes(self):
        pool = KeyPool(["key-a", "key-b"])
        pool._states[1].total_requests = 7

        self.assertTrue(pool.set_keys(["key-b", "key-c"]))

        self.assertEqual(["key-b", "key-c"], [s.key for s in pool._states])
        self.assertEqual([1, 0], [s.index for s in pool._states])
        self.assertEqual(7, pool._states[0].total_requests)

    def test_set_keys_returns_false_when_empty(self):
        pool = KeyPool(["key-a"])

        self.assertFalse(pool.set_keys([]))
        self.assertEqual(0, pool.key_count)


class ClientWiringTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.pool = KeyPool(["key-a", "key-b"])
        self.seen: list[tuple[int, str, ErrorCategory]] = []
        self.models: list[str] = []
        self.resolved: tuple[str, int] = ("", -1)

        seen, models = self.seen, self.models

        async def getter(model: str) -> tuple[str, int]:
            models.append(model)
            self.resolved = await self.pool.get_key(model)
            return self.resolved
        async def on_error(index: int, model: str, error: MiniMaxError) -> None:
            seen.append((index, model, error.category))

        self.client = MiniMaxClient(key_getter=getter, pool_error_handler=on_error)
        self.addAsyncCleanup(self.client.close)

    async def install(self, response: httpx.Response) -> None:
        """换掉底层传输层，避免测试发真实请求。"""
        await swap_transport(self.client, response)

    async def test_request_passes_model_to_key_getter(self):
        client = self.client
        await self.install(httpx.Response(200, json={"ok": True}))

        await client.request_json("POST", "/v1/image", body={}, model="MiniMax-H3")

        self.assertEqual(["MiniMax-H3"], self.models)

    async def test_quota_error_reports_key_index_and_model(self):
        client = self.client
        await self.install(
            httpx.Response(429, json={"base_resp": {"status_code": 1002}})
        )

        with self.assertRaises(MiniMaxError) as ctx:
            await client.request("POST", "/v1/image", body={}, model="MiniMax-H3")

        self.assertEqual(ErrorCategory.QUOTA, ctx.exception.category)
        self.assertEqual(1, len(self.seen))
        index, model, category = self.seen[0]
        self.assertEqual("MiniMax-H3", model)
        self.assertEqual(ErrorCategory.QUOTA, category)
        self.assertEqual(self.resolved[1], index)

    async def test_auth_error_reports_key_index(self):
        client = self.client
        await self.install(httpx.Response(401, json={}))

        with self.assertRaises(MiniMaxError) as ctx:
            await client.request("GET", "/v1/quota")

        self.assertEqual(ErrorCategory.AUTH, ctx.exception.category)
        self.assertEqual(ErrorCategory.AUTH, self.seen[0][2])

    async def test_handler_failure_is_attached_as_note(self):
        async def boom(index: int, model: str, error: MiniMaxError) -> None:
            raise RuntimeError("池回写失败")

        client = MiniMaxClient(
            key_getter=self.pool.get_key, pool_error_handler=boom
        )
        self.addAsyncCleanup(client.close)
        await swap_transport(client, httpx.Response(429, json={}))

        with self.assertRaises(MiniMaxError) as ctx:
            await client.request("POST", "/v1/image", body={})

        self.assertIn("池回写失败", str(ctx.exception.__notes__[0]))

    async def test_fixed_key_skips_pool_error_handler(self):
        client = MiniMaxClient(api_key="sk-fixed")
        self.addAsyncCleanup(client.close)
        await swap_transport(client, httpx.Response(429, json={}))

        with self.assertRaises(MiniMaxError):
            await client.request("POST", "/v1/image", body={})

        self.assertEqual([], self.seen)
