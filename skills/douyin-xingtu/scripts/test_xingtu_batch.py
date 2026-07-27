#!/usr/bin/env python3

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).with_name("xingtu_batch.py")
SPEC = importlib.util.spec_from_file_location("xingtu_batch", MODULE_PATH)
assert SPEC and SPEC.loader
xingtu = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = xingtu
SPEC.loader.exec_module(xingtu)


def candidate(
    *,
    name: str = "测试达人",
    core_user_id: str = "author-1",
    star_id: str = "star-1",
    recent=None,
    representative=None,
):
    return {
        "display_name": name,
        "core_user_id": core_user_id,
        "star_id": star_id,
        "last_10_items": recent or [],
        "representative_items": representative or [],
        "metric_conflicts": [],
    }


class FakeClient:
    def fetch_json(self, path):
        return {
            "items": [
                {
                    "id": "7665370585678447348",
                    "author_id": "author-1",
                    "create_time": 1784800000,
                    "stats": {
                        "watch_cnt": 1200,
                        "like_cnt": 30,
                        "comment_cnt": 4,
                        "share_cnt": 2,
                    },
                }
            ]
        }


class FakeMarketClient:
    def __init__(self, tabs, states):
        self.tabs = tabs
        self.states = iter(states)
        self.calls = []
        self.evaluated_code = []

    def call(self, action, args=None):
        self.calls.append((action, args or {}))
        if action == "list_tabs":
            return {"tabs": self.tabs}
        return {}

    def evaluate_json(self, code):
        self.evaluated_code.append(code)
        return next(self.states)


class MarketReadinessTests(unittest.TestCase):
    def test_old_market_route_is_replaced_before_ready_check(self):
        client = FakeMarketClient(
            [{"url": "https://www.xingtu.cn/ad/creator/market"}],
            [{"on_creator_index": True, "login_required": False, "search_ready": True}],
        )
        state = xingtu.WebBridge.ensure_market(client)
        self.assertTrue(state["search_ready"])
        self.assertIn(
            ("navigate", {"url": xingtu.MARKET_URL, "newTab": False}),
            client.calls,
        )

    def test_no_existing_tab_opens_creator_index_in_new_tab(self):
        client = FakeMarketClient(
            [],
            [{"on_creator_index": True, "login_required": False, "search_ready": True}],
        )
        xingtu.WebBridge.ensure_market(client)
        navigate = [call for call in client.calls if call[0] == "navigate"][0]
        self.assertEqual(navigate[1]["url"], xingtu.MARKET_URL)
        self.assertTrue(navigate[1]["newTab"])

    def test_delayed_search_control_becomes_ready(self):
        client = FakeMarketClient(
            [{"url": xingtu.MARKET_URL}],
            [
                {"on_creator_index": True, "login_required": False, "search_ready": False},
                {"on_creator_index": True, "login_required": False, "search_ready": True},
            ],
        )
        with patch.object(xingtu.time, "sleep"):
            state = xingtu.WebBridge.ensure_market(client)
        self.assertTrue(state["search_ready"])

    def test_missing_visible_control_is_page_not_ready_not_auth_error(self):
        client = FakeMarketClient(
            [{"url": xingtu.MARKET_URL}],
            [
                {"on_creator_index": True, "login_required": False, "search_ready": False}
            ] * 12,
        )
        with patch.object(xingtu.time, "sleep"):
            with self.assertRaisesRegex(xingtu.XingtuError, "^page_not_ready:"):
                xingtu.WebBridge.ensure_market(client)
        self.assertIn("getBoundingClientRect", client.evaluated_code[0])
        self.assertIn("!e.disabled", client.evaluated_code[0])

    def test_login_redirect_is_auth_required(self):
        client = FakeMarketClient(
            [{"url": xingtu.MARKET_URL}],
            [
                {"on_creator_index": False, "login_required": True, "search_ready": False}
            ] * 12,
        )
        with patch.object(xingtu.time, "sleep"):
            with self.assertRaisesRegex(xingtu.XingtuError, "^auth_required:"):
                xingtu.WebBridge.ensure_market(client)

    def test_logged_in_homepage_is_page_not_ready_not_auth_error(self):
        client = FakeMarketClient(
            [{"url": xingtu.MARKET_URL}],
            [
                {"on_creator_index": False, "login_required": False, "search_ready": False}
            ] * 12,
        )
        with patch.object(xingtu.time, "sleep"):
            with self.assertRaisesRegex(xingtu.XingtuError, "^page_not_ready:"):
                xingtu.WebBridge.ensure_market(client)


class FetchAuthenticationTests(unittest.TestCase):
    def test_http_401_is_auth_required(self):
        class Client:
            def evaluate_json(self, _code):
                return {"ok": False, "status": 401, "body_text": ""}

        with self.assertRaisesRegex(xingtu.XingtuError, "^auth_required:"):
            xingtu.WebBridge.fetch_json(Client(), "/test")


class CandidateSelectionTests(unittest.TestCase):
    def test_item_in_representative_items_disambiguates_creator(self):
        records = [
            candidate(name="同名达人", core_user_id="wrong"),
            candidate(
                name="昵称已变化",
                representative=[
                    {"item_id": "7665370585678447348", "vv": 1200}
                ],
            ),
        ]
        selected, method, warnings = xingtu.select_author_candidate(
            records,
            {
                "display_name": "同名达人",
                "item_id": "7665370585678447348",
                "expected_author_id": "author-1",
            },
        )
        self.assertEqual(selected["star_id"], "star-1")
        self.assertEqual(method, "item_id_in_candidate_items")
        self.assertEqual(warnings, [])

    def test_unrequested_item_conflict_is_warning_for_name_query(self):
        record = candidate()
        record["metric_conflicts"] = [{"item_id": "other"}]
        selected, method, warnings = xingtu.select_author_candidate(
            [record], {"display_name": "测试达人"}
        )
        self.assertIsNotNone(selected)
        self.assertEqual(method, "exact_display_name")
        self.assertIn("candidate_has_unrequested_item_metric_conflicts", warnings)


class PublishedItemTests(unittest.TestCase):
    def setUp(self):
        self.input_records = [
            {
                "source_key": "sheet-row-7",
                "display_name": "测试达人",
                "item_id": "7665370585678447348",
            }
        ]

    def author_result(self, play_count=1200):
        selected = candidate(
            recent=[
                {
                    "item_id": "7665370585678447348",
                    "vv": play_count,
                }
            ]
        )
        return {
            "identity": {
                "star_id": "star-1",
                "core_user_id": "author-1",
                "match_method": "item_id_in_candidate_items",
                "confidence": "exact",
            },
            "candidates": [selected],
            "status": "ready",
            "errors": [],
            "warnings": [],
            "evidence": [],
        }

    def test_published_item_merges_verified_creator_identity(self):
        with patch.object(xingtu, "search_author", return_value=self.author_result()):
            result = xingtu.published_items(FakeClient(), self.input_records)[0]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["identity"]["star_id"], "star-1")
        self.assertEqual(result["identity"]["core_user_id"], "author-1")
        self.assertEqual(
            result["identity"]["match_method"],
            "item_id+item_id_in_candidate_items",
        )

    def test_play_difference_prefers_current_item_detail_by_default(self):
        with patch.object(
            xingtu, "search_author", return_value=self.author_result(play_count=999)
        ):
            result = xingtu.published_items(FakeClient(), self.input_records)[0]
        self.assertEqual(result["status"], "ready")
        self.assertEqual(
            result["metric_resolution"]["selected_surface"],
            "item_detail.stats.watch_cnt",
        )
        self.assertIn(
            "cached_summary_play_count_differs_from_item_detail",
            result["warnings"],
        )

    def test_strict_mode_blocks_play_difference(self):
        with patch.object(
            xingtu, "search_author", return_value=self.author_result(play_count=999)
        ):
            result = xingtu.published_items(
                FakeClient(), self.input_records, strict_conflicts=True
            )[0]
        self.assertEqual(result["status"], "metric_conflict")
        self.assertIn(
            "play_count_differs_across_xingtu_surfaces", result["errors"]
        )


if __name__ == "__main__":
    unittest.main()
