from __future__ import annotations

import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_mmx_cli_tool.mmx.errors import (  # noqa: E402
    ErrorCategory, classify_error, friendly_message,
)
from astrbot_plugin_mmx_cli_tool.mmx.quota_usage import (  # noqa: E402
    format_quota_reset_time, format_quota_usage, merge_quota_models,
    normalize_model_quota, quota_window_label, summarize_keypool_quota,
)


def quota(**overrides):
    return {
        "model_name": "general", "current_interval_total_count": 100,
        "current_interval_usage_count": 80, "current_interval_remaining_percent": 80,
        "current_weekly_total_count": 1000, "current_weekly_usage_count": 800,
        "current_weekly_remaining_percent": 80, "start_time": 0, "end_time": 43200000,
        "remains_time": 59999, **overrides,
    }


class QuotaTests(unittest.TestCase):
    def test_legacy_and_new_usage_semantics(self):
        for reported in (80, 20):
            with self.subTest(reported=reported):
                value = normalize_model_quota(quota(current_interval_usage_count=reported))["current"]
                self.assertEqual((20, 80, 80), (value["used"], value["remaining"], value["remaining_percent"]))
                self.assertEqual("已用20%", format_quota_usage(value))

    def test_full_legacy_bucket_is_not_exhausted(self):
        self.assertEqual(100, summarize_keypool_quota(quota(
            current_interval_usage_count=100, current_interval_remaining_percent=100
        ))["remaining"])

    def test_explicit_remaining_and_percentage_are_preserved(self):
        result = normalize_model_quota(quota(current_interval_remaining_count=70))["current"]
        self.assertEqual(70, result["remaining"])
        self.assertEqual(80, result["remaining_percent"])
        self.assertEqual("已用20%", format_quota_usage(result))

    def test_ambiguous_counts_do_not_override_percent_or_disable_key(self):
        item = quota(current_interval_usage_count=40)
        window = normalize_model_quota(item)["current"]
        self.assertIsNone(window["remaining"])
        self.assertEqual("已用20%", format_quota_usage(window))
        self.assertEqual(-1, summarize_keypool_quota(item)["remaining"])

    def test_missing_percent_uses_legacy_counts(self):
        result = normalize_model_quota(quota(current_interval_remaining_percent=None))["current"]
        self.assertEqual(80, result["remaining"])
        self.assertEqual(80, result["remaining_percent"])

    def test_invalid_counts_remain_unknown(self):
        for bad in (-1, 101, float("inf"), float("nan"), True, "bad"):
            with self.subTest(bad=bad):
                value = normalize_model_quota(quota(current_interval_usage_count=bad))["current"]
                self.assertIsNone(value["remaining"])
                self.assertEqual(80, value["remaining_percent"])

    def test_unavailable_is_not_unlimited(self):
        value = normalize_model_quota(quota(
            current_interval_total_count=0, current_weekly_total_count=0,
            current_interval_status=3, current_weekly_status=3,
        ))
        for name in ("current", "weekly"):
            self.assertTrue(value[name]["unavailable"])
            self.assertFalse(value[name]["unlimited"])
            self.assertEqual("不在当前套餐中", format_quota_usage(value[name]))

    def test_unlimited_weekly_still_supported(self):
        value = normalize_model_quota(quota(current_weekly_status=3))["weekly"]
        self.assertEqual("∞", format_quota_usage(value))

    def test_seconds_and_window_labels(self):
        for value, expected in ((1, "1秒"), (59999, "59秒"), (60000, "1分钟"), (0, "即将"), (-1, "即将"), (None, None)):
            self.assertEqual(expected, format_quota_reset_time(value))
        self.assertEqual("12小时额度", quota_window_label(normalize_model_quota(quota())["current"]))
        self.assertEqual("当前周期额度", quota_window_label({}))
        self.assertEqual("1天额度", quota_window_label({"start_time": 0, "end_time": 86400000}))

    def test_boost_and_multi_key_weighting(self):
        a = normalize_model_quota(quota(weekly_boost_permille=1500))
        b = normalize_model_quota(quota(weekly_boost_permille=1000))
        self.assertEqual("已用20%（加成后剩余120%）", format_quota_usage(a["weekly"]))
        merged = merge_quota_models([a, b])["general"]
        self.assertEqual(160, merged["current"]["remaining"])
        self.assertEqual(100, merged["weekly"]["boosted_remaining_percent"])
        self.assertEqual("已用20%（加成后剩余100%）", format_quota_usage(merged["weekly"]))

    def test_unavailable_key_does_not_hide_available_key(self):
        missing = normalize_model_quota(quota(
            current_interval_total_count=0, current_weekly_total_count=0,
            current_interval_status=3, current_weekly_status=3,
        ))
        available = normalize_model_quota(quota())
        for items in ([missing, available], [available, missing]):
            merged = merge_quota_models(items)["general"]
            self.assertEqual("已用20%", format_quota_usage(merged["current"]))
            self.assertEqual(80, merged["current"]["remaining"])

    def test_merge_unknown_count_does_not_understate_total_usage(self):
        merged = merge_quota_models([
            normalize_model_quota(quota()),
            normalize_model_quota(quota(current_interval_usage_count=40)),
        ])["general"]["current"]
        self.assertIsNone(merged["remaining"])
        self.assertEqual(80, merged["remaining_percent"])


class ErrorTests(unittest.TestCase):
    def test_input_output_filter_mapping(self):
        for code, msg, word in ((1026, "input new_sensitive", "输入"), (1027, "输出涉敏", "输出"), (2001, "output new_sensitive", "输出")):
            error = classify_error(200, api_body={"base_resp": {"status_code": code, "status_msg": msg}})
            self.assertEqual(ErrorCategory.CONTENT_FILTER, error.category)
            self.assertFalse(error.retryable)
            self.assertIn(word, friendly_message(error))
            self.assertEqual(code, error.api_code)

    def test_sensitive_word_alone_does_not_classify_as_output_filter(self):
        error = classify_error(400, api_body={"base_resp": {"status_code": 2001, "status_msg": "sensitive database unavailable"}})
        self.assertEqual(ErrorCategory.GENERAL, error.category)

    def test_asr_error_mappings_are_endpoint_scoped(self):
        for status, category in ((413, ErrorCategory.USAGE), (422, ErrorCategory.CONTENT_FILTER)):
            body = {"error": {"message": "中文错误", "type": "unprocessable_entity_error"}}
            error = classify_error(status, path="/v1/speech_to_text", api_body=body)
            self.assertEqual(category, error.category)
            self.assertIn("中文错误", str(error))
            self.assertEqual(ErrorCategory.GENERAL, classify_error(status, path="/other", api_body=body).category)

    def test_v2_and_balance_errors(self):
        error = classify_error(422, api_body={"error": {"type": "unprocessable_entity_error", "message": "sensitive content 1026"}})
        self.assertEqual(ErrorCategory.CONTENT_FILTER, error.category)
        self.assertEqual(ErrorCategory.QUOTA, classify_error(402).category)

    def test_http_status_precedence_and_non_json(self):
        self.assertEqual(ErrorCategory.AUTH, classify_error(401, api_body={"base_resp": {"status_code": 1027}}).category)
        self.assertTrue(classify_error(429).retryable)
        self.assertTrue(classify_error(503, response=httpx.Response(503, text="unavailable")).retryable)
        self.assertEqual(ErrorCategory.GENERAL, classify_error(400, response=httpx.Response(400, json=[])).category)
