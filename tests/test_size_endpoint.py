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


class ExtractSizeTest(unittest.TestCase):
    def test_uses_vision_model_and_inspects_all_images(self):
        analyzer, vision_client = _analyzer({"product_size": " 40 to 45 "})

        result = analyzer.extract_size(
            images=[(b"front", "image/png"), (b"tag", "image/png")],
        )

        call = vision_client.calls[0]
        image_parts = [p for p in call["messages"][1]["content"] if p["type"] == "image_url"]
        self.assertEqual(call["model"], "vision-test")
        self.assertEqual(len(image_parts), 2)
        # Cleaned and trimmed.
        self.assertEqual(result["product_size"], "40 to 45")
        self.assertEqual(set(result["timings"].keys()), {"size_ms"})

    def test_returns_null_when_no_visible_size(self):
        analyzer, _ = _analyzer({"product_size": None})
        result = analyzer.extract_size(images=[(b"front", "image/png")])
        self.assertIsNone(result["product_size"])

    def test_empty_string_size_becomes_null(self):
        analyzer, _ = _analyzer({"product_size": "   "})
        result = analyzer.extract_size(images=[(b"front", "image/png")])
        self.assertIsNone(result["product_size"])

    def test_missing_size_key_becomes_null(self):
        analyzer, _ = _analyzer({})
        result = analyzer.extract_size(images=[(b"front", "image/png")])
        self.assertIsNone(result["product_size"])

    def test_debug_includes_raw_and_attempts(self):
        analyzer, _ = _analyzer({"product_size": "M"})
        result = analyzer.extract_size(images=[(b"front", "image/png")], debug=True)
        self.assertEqual(result["product_size"], "M")
        self.assertIn("size_ai_raw", result["_debug"])
        self.assertIn("size_only", result["_debug"]["attempts"])


class SizeEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    @patch.object(main, "analyzer")
    def test_size_endpoint_returns_size_only_by_default(self, analyzer):
        analyzer.extract_size.return_value = {
            "product_size": "40 to 45",
            "timings": {"size_ms": 42.0},
        }

        resp = self.client.post(
            "/api/v1/mercari/image/size",
            headers=auth_headers(),
            files=[
                ("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                ("image_list", ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")),
            ],
        )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["product_size"], "40 to 45")
        self.assertNotIn("timings", body)
        self.assertNotIn("image_processing", body)
        analyzer.extract_size.assert_called_once()

    @patch.object(main, "analyzer")
    def test_size_endpoint_returns_null_when_no_size(self, analyzer):
        analyzer.extract_size.return_value = {
            "product_size": None,
            "timings": {"size_ms": 12.0},
        }

        resp = self.client.post(
            "/api/v1/mercari/image/size",
            headers=auth_headers(),
            files=[("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
        )

        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["product_size"])

    @patch.object(main, "analyzer")
    def test_size_endpoint_returns_debug_fields_when_debug_enabled(self, analyzer):
        analyzer.extract_size.return_value = {
            "product_size": "27cm",
            "timings": {"size_ms": 42.0},
            "_debug": {"size_ai_raw": {"product_size": "27cm"}},
        }

        with patch.object(main.settings, "enable_debug_param", True):
            resp = self.client.post(
                "/api/v1/mercari/image/size",
                headers=auth_headers(),
                files=[
                    ("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                    ("image_list", ("b.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                ],
                data={"debug": "true"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["product_size"], "27cm")
        self.assertEqual(body["timings"]["size_ms"], 42.0)
        self.assertEqual(len(body["image_processing"]), 2)
        self.assertEqual(body["_debug"]["size_ai_raw"]["product_size"], "27cm")
        self.assertTrue(analyzer.extract_size.call_args.kwargs["debug"])


if __name__ == "__main__":
    unittest.main()
