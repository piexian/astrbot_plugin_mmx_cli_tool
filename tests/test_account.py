"""MiniMax Code 账户接口核心场景测试（MockTransport，无真实网络）。

只保留签名算法、信封兼容、401 刷新重试、身份/积分字段优先级等关键契约。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx

from astrbot_plugin_mmx_cli_tool.mmx.account import (
    AccountIdentity,
    MiniMaxCodeAccount,
    _js_encode_uri_component,
)
from astrbot_plugin_mmx_cli_tool.mmx.client import MiniMaxClient
from astrbot_plugin_mmx_cli_tool.mmx.errors import ErrorCategory, MiniMaxError


def make_account(handler, *, region="cn"):
    """构建走 MockTransport 的账户客户端；返回 (account, 状态盒)。"""
    state = {"region": region, "token": "tok-1", "refreshes": 0, "requests": []}

    async def creds():
        return state["region"], state["token"]

    async def refresher():
        state["refreshes"] += 1
        state["token"] = "tok-2"
        return state["region"], state["token"]

    client = MiniMaxClient(api_key="unused")
    original = client._client
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    account = MiniMaxCodeAccount(client, creds, refresher)
    account._test_state = state

    async def close():
        await client._client.aclose()
        await original.aclose()

    return account, close


def json_response(payload, status_code=200):
    return httpx.Response(status_code, json=payload)


OK = {"status_code": 0}


class SignatureTests(unittest.TestCase):
    def test_signed_headers_golden(self):
        import hashlib

        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()  # noqa: E731
        # new-api 实测实现保留 '/'，转义 ? = & 与空格等
        self.assertEqual(_js_encode_uri_component("/a b?c=d&e"), "/a%20b%3Fc%3Dd%26e")
        self.assertEqual(_js_encode_uri_component("-_.!~*'()"), "-_.!~*'()")

        pq = "/minimax-cloud/api/v1/signin/claim?app_id=3001&biz_id=3&unix=1764000000123&user_id=0"
        now_ms = 1764000000123
        # GET：yy 以 '{}' 参与拼接，x-signature 的 body 为空
        headers = MiniMaxCodeAccount._signed_headers(now_ms, pq, None)
        self.assertEqual(headers["yy"], "c4e419b5fb1ddb9a3a8218ff79d0856c")
        self.assertEqual(headers["x-signature"], "c5aede5565a165937aea99b561db3c1b")
        self.assertEqual(headers["x-timestamp"], "1764000000")
        # POST "{}"：yy 不变，x-signature 覆盖 body
        headers_post = MiniMaxCodeAccount._signed_headers(now_ms, pq, b"{}")
        self.assertEqual(headers_post["yy"], headers["yy"])
        self.assertEqual(
            headers_post["x-signature"], "ba50e479698bf5fb3a51410bd2ea9e47"
        )
        # 交叉验证公式（独立于被测函数拼接）
        self.assertEqual(
            headers["yy"],
            md5(_js_encode_uri_component(pq) + "_{}" + md5(str(now_ms)) + "ooui"),
        )


class AccountRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_signin_claim_request_shape(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return json_response({"day_no": 3, "points": 15, "base_resp": OK})

        account, close = make_account(handler)
        self.addAsyncCleanup(close)
        result = await account.signin_claim()

        self.assertEqual(result["day_no"], 3)  # 无 data 包装 → 回退整体 body
        self.assertEqual(len(captured), 1)
        req = captured[0]
        self.assertEqual(req.method, "POST")
        self.assertTrue(
            req.url.path.startswith("/minimax-cloud/api/v1/signin/claim"),
            req.url.path,
        )
        self.assertIn("app_id=3001", str(req.url.query))
        self.assertIn("is_desktop=1", str(req.url.query))
        self.assertIn("user_id=0", str(req.url.query))
        self.assertEqual(req.read(), b"{}")
        self.assertEqual(req.headers["authorization"], "Bearer tok-1")
        self.assertEqual(req.headers["user-agent"], "MiniMaxAgent")
        self.assertIn("yy", req.headers)
        self.assertIn("x-timestamp", req.headers)
        self.assertIn("x-signature", req.headers)

    async def test_signin_401_refreshes_then_retries(self):
        state = {"calls": 0, "refreshes": 0, "token": "tok-1"}

        async def creds():
            return "cn", state["token"]

        async def refresher():
            state["refreshes"] += 1
            state["token"] = "tok-2"
            return "cn", state["token"]

        def handler(request: httpx.Request) -> httpx.Response:
            state["calls"] += 1
            if state["token"] == "tok-1":
                return json_response({"message": "unauthorized"}, status_code=401)
            return json_response({"day_no": 1, "points": 5, "base_resp": OK})

        client = MiniMaxClient(api_key="unused")
        original = client._client
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.addAsyncCleanup(client._client.aclose)
        self.addAsyncCleanup(original.aclose)
        account = MiniMaxCodeAccount(client, creds, refresher)

        result = await account.signin_claim()
        self.assertEqual(result["day_no"], 1)
        self.assertEqual(state["refreshes"], 1)
        self.assertEqual(state["calls"], 2)

    async def test_remains_and_credit_pool_contracts(self):
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            if request.url.path.endswith("coding_plan/remains"):
                return json_response(
                    {
                        "model_remains": [{"model_name": "general", "usage_count": 48}],
                        "base_resp": OK,
                    }
                )
            return json_response(
                {"token_plan_credit_fallback_enabled": True, "base_resp": OK}
            )

        account, close = make_account(handler)
        self.addAsyncCleanup(close)
        remains = await account.remains("g-1")
        pool = await account.credit_pool()

        remains_req, pool_req = captured
        # remains：open 域、仅 X-Group-Id，无签名与公共 query
        self.assertEqual(
            str(remains_req.url),
            "https://www.minimaxi.com/v1/api/openplatform/coding_plan/remains",
        )
        self.assertEqual(remains_req.headers.get("x-group-id"), "g-1")
        self.assertNotIn("yy", remains_req.headers)
        self.assertNotIn("x-signature", remains_req.headers)
        self.assertEqual(remains["model_remains"][0]["usage_count"], 48)
        # credit_pool：仍需签名，但不拼公共 query
        self.assertEqual(
            str(pool_req.url),
            "https://www.minimaxi.com/backend/account/token_plan_credit",
        )
        self.assertIn("yy", pool_req.headers)
        self.assertTrue(pool["token_plan_credit_fallback_enabled"])

    async def test_error_envelope_raises_with_message(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(
                {
                    "statusInfo": {"code": 2013, "msg": "invalid params"},
                    "base_resp": OK,
                }
            )

        account, close = make_account(handler)
        self.addAsyncCleanup(close)
        with self.assertRaises(MiniMaxError) as ctx:
            await account.signin_claim()
        self.assertIn("invalid params", str(ctx.exception))
        self.assertIs(ctx.exception.category, ErrorCategory.GENERAL)


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, user_info, workspaces=None):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/api/user/info":
                # user/info 有 data 包装
                return json_response({"data": {"userInfo": user_info}, "base_resp": OK})
            if request.url.path == "/matrix/api/v1/user/get_user_extra_info":
                return json_response({"workspaces": workspaces or [], "base_resp": OK})
            return json_response({"base_resp": OK})

        return handler

    async def test_identity_workspace_balance_and_fallbacks(self):
        workspaces = [
            {"workspace_type": 1, "op_group_id": "team-g"},
            {
                "workspace_type": "0",
                "op_group_id": "personal-g",
                "op_credit_summary": {"total_remaining_amount": "42.5"},
            },
        ]
        captured = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            inner = self.make_handler(
                {
                    "realUserID": "rid-9",
                    "userID": 123,
                    "userName": "u",
                    "email": "e@x.com",
                },
                workspaces,
            )
            return inner(request)

        account, close = make_account(handler)
        self.addAsyncCleanup(close)
        identity = await account.identity()

        self.assertIsInstance(identity, AccountIdentity)
        self.assertEqual(identity.real_user_id, "rid-9")
        self.assertEqual(identity.group_id, "personal-g")  # 仅个人 workspace
        self.assertEqual(identity.credit_balance, "42.5")  # 优先级最高字段
        # 第二个请求（extra_info）已使用缓存 real_user_id
        self.assertIn("user_id=rid-9", str(captured[1].url.query))

        # 无 summary 时回退扁平字段 opcredit_balance > total_remains_credit
        flat = [
            {
                "workspace_type": 0,
                "op_group_id": "personal-g",
                "opcredit_balance": 30,
                "total_remains_credit": "20",
            }
        ]

        def flat_handler(request: httpx.Request) -> httpx.Response:
            return self.make_handler({"realUserID": "rid-1"}, flat)(request)

        account2, close2 = make_account(flat_handler)
        self.addAsyncCleanup(close2)
        self.assertEqual((await account2.identity()).credit_balance, "30")

        # 缺 real_user_id 必须报错（后续签名 user_id 依赖它）
        def missing_handler(request: httpx.Request) -> httpx.Response:
            return self.make_handler({})(request)

        account3, close3 = make_account(missing_handler)
        self.addAsyncCleanup(close3)
        with self.assertRaises(MiniMaxError):
            await account3.identity()

    async def test_auth_retry_recomputes_origin_after_region_change(self):
        """401 刷新导致区域切换时，重试与后续请求都必须使用新区域域名。"""
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(401, json={"error": "unauthorized"})
            return httpx.Response(
                200,
                json={
                    "base_resp": {"status_code": 0},
                    "data": {"userInfo": {"realUserID": "rid"}},
                },
            )

        state = {"region": "cn", "token": "t1"}

        async def creds():
            return state["region"], state["token"]

        async def refresher():
            state["region"] = "global"
            state["token"] = "t2"
            return state["region"], state["token"]

        client = MiniMaxClient(api_key="unused")
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        account = MiniMaxCodeAccount(client, creds, refresher)
        identity = await account.identity()
        await client._client.aclose()

        self.assertEqual(identity.real_user_id, "rid")
        self.assertEqual(requests[0].url.host, "agent.minimax.cn")
        self.assertEqual(requests[1].url.host, "agent.minimax.io")
        # extra_info 也必须跟随切换后的区域
        self.assertEqual(requests[2].url.host, "agent.minimax.io")


if __name__ == "__main__":
    unittest.main()
