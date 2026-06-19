import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from console_auth_helpers import auth_headers
from app.service import MercariAnalyzer
import main


class RecordingChatClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.payload), {"choices": []}


class FakeBrandStore:
    def match(self, brand_name):
        return None


class FakeCategoryStore:
    def get_categories_by_group(self, group_name):
        return []

    def find_category(self, group_name, category_name):
        return None


def _settings():
    return SimpleNamespace(
        vision_model="vision-test",
        price_model="price-model-test",
        category_model="category-test",
        product_data_model="product-data-test",
        log_requests=False,
        vision_fallback_models=[],
        category_fallback_models=[],
        model_call_max_retries=0,
        model_call_total_budget_seconds=10,
        request_timeout=10,
    )


def _analyzer(payload):
    vision_client = RecordingChatClient(payload)
    return MercariAnalyzer(
        settings=_settings(),
        brand_store=FakeBrandStore(),
        category_store=FakeCategoryStore(),
        vision_client=vision_client,
        category_client=vision_client,
    ), vision_client


class ExtractPricesTest(unittest.TestCase):
    def test_uses_price_model_and_returns_visible_prices(self):
        analyzer, vision_client = _analyzer(
            {"tax_excluded": "¥980", "tax_included": "税込 1,078円"}
        )

        result = analyzer.extract_prices(
            images=[(b"front", "image/png"), (b"back", "image/png")],
        )

        call = vision_client.calls[0]
        image_parts = [p for p in call["messages"][1]["content"] if p["type"] == "image_url"]
        self.assertEqual(call["model"], "price-model-test")
        self.assertEqual(len(image_parts), 2)
        self.assertEqual(result["tax_excluded"], 980)
        self.assertEqual(result["tax_included"], 1078)
        self.assertEqual(set(result["timings"].keys()), {"price_ms"})

    def test_single_visible_price_is_tax_included(self):
        analyzer, _ = _analyzer({"tax_excluded": "¥980", "tax_included": None})
        result = analyzer.extract_prices(images=[(b"front", "image/png")])
        self.assertIsNone(result["tax_excluded"])
        self.assertEqual(result["tax_included"], 980)

    def test_returns_ai_reference_price_range_when_no_visible_price(self):
        analyzer, _ = _analyzer(
            {"tax_excluded": None, "tax_included": None, "prices": [1200, 2400]}
        )

        result = analyzer.extract_prices(images=[(b"front", "image/png")])

        self.assertIsNone(result["tax_excluded"])
        self.assertIsNone(result["tax_included"])
        self.assertEqual(result["prices"], [1200, 2400])

    def test_reference_price_range_covers_visible_price_without_changing_it(self):
        analyzer, _ = _analyzer(
            {"tax_excluded": None, "tax_included": "税込 10,780円", "prices": [1200, 2400]}
        )

        result = analyzer.extract_prices(images=[(b"front", "image/png")])

        self.assertIsNone(result["tax_excluded"])
        self.assertEqual(result["tax_included"], 10780)
        self.assertEqual(result["prices"], [1200, 10780])

    def test_returns_null_when_no_visible_price(self):
        analyzer, _ = _analyzer({"tax_excluded": None, "tax_included": None})
        result = analyzer.extract_prices(images=[(b"front", "image/png")])
        self.assertIsNone(result["tax_excluded"])
        self.assertIsNone(result["tax_included"])


class PriceEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    @patch.object(main, "analyzer")
    def test_price_endpoint_returns_price_only_by_default(self, analyzer):
        analyzer.extract_prices.return_value = {
            "tax_excluded": None,
            "tax_included": 1078,
            "prices": [900, 1300],
            "timings": {"price_ms": 42.0},
        }

        resp = self.client.post(
            "/api/v1/mercari/image/price",
            headers=auth_headers(),
            files=[
                ("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                ("image_list", ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")),
            ],
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["tax_excluded"])
        self.assertEqual(body["tax_included"], 1078)
        self.assertEqual(body["prices"], [900, 1300])
        self.assertNotIn("timings", body)
        self.assertNotIn("image_processing", body)
        analyzer.extract_prices.assert_called_once()

    @patch.object(main, "analyzer")
    def test_price_endpoint_returns_debug_fields_when_debug_enabled(self, analyzer):
        analyzer.extract_prices.return_value = {
            "tax_excluded": None,
            "tax_included": 1078,
            "prices": [],
            "timings": {"price_ms": 42.0},
            "_debug": {"price_ai_raw": {"tax_included": "1078"}},
        }

        with patch.object(main.settings, "enable_debug_param", True):
            resp = self.client.post(
                "/api/v1/mercari/image/price",
                headers=auth_headers(),
                files=[
                    ("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                    ("image_list", ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                ],
                data={"debug": "true"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIsNone(body["tax_excluded"])
        self.assertEqual(body["tax_included"], 1078)
        self.assertEqual(body["timings"]["price_ms"], 42.0)
        self.assertEqual(len(body["image_processing"]), 2)
        self.assertEqual(body["_debug"]["price_ai_raw"]["tax_included"], "1078")
        analyzer.extract_prices.assert_called_once()
        self.assertTrue(analyzer.extract_prices.call_args.kwargs["debug"])


if __name__ == "__main__":
    unittest.main()
