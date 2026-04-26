import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.service import MercariAnalyzer, _paths_from_categories


class FakeChatClient:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, **kwargs):
        return json.dumps(self.payload), {"choices": []}


class RecordingChatClient(FakeChatClient):
    def __init__(self, payload):
        super().__init__(payload)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return super().chat(**kwargs)


class SequenceChatClient:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        payload = self.payloads.pop(0)
        return json.dumps(payload), {"choices": []}


class FakeBrandStore:
    def match(self, brand_name):
        return None


class FakeCategoryStore:
    categories = {
        "メンズファッション/トップス": {
            "id": "123",
            "name": "メンズファッション/トップス",
            "meru_id": "m-123",
            "rakuma_id": "ra-123",
            "zenplus_id": "z-123",
        }
    }

    def get_categories_by_group(self, group_name):
        return list(self.categories.values())

    def find_category(self, group_name, category_name):
        return self.categories.get(category_name)


class RakutenIdResponseTest(unittest.TestCase):
    def test_analyze_includes_rakuten_id_aliases_for_image_category_response(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=FakeChatClient(
                {
                    "title": "シャツ",
                    "description": "メンズシャツ",
                    "prices": [1000, 1500, 2000],
                    "top_level_category": "メンズファッション",
                    "brand_name": "",
                }
            ),
            category_client=FakeChatClient(
                {"best_target_path": "メンズファッション/トップス"}
            ),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
        )

        self.assertEqual(result["categories"][0]["id"], "123")
        self.assertEqual(result["categories"][0]["rakuten_id"], "123")
        self.assertEqual(result["best_category_id"], "123")
        self.assertEqual(result["rakuten_id"], "123")

    def test_analyze_uses_single_vision_call_for_prices(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        vision_client = RecordingChatClient(
            {
                "title": "シャツ",
                "description": "メンズシャツ",
                "prices": [1000, 1500, 2000],
                "top_level_category": "メンズファッション",
                "brand_name": "",
            }
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=FakeChatClient(
                {"best_target_path": "メンズファッション/トップス"}
            ),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
        )

        self.assertEqual(result["prices"], [1000, 1500, 2000])
        self.assertEqual(len(vision_client.calls), 1)

    def test_analyze_direct_tax_excluded_clears_inferred_prices(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=FakeChatClient(
                {
                    "title": "シャツ",
                    "description": "メンズシャツ",
                    "tax_excluded": "¥980",
                    "tax_included": "税込 1,078円",
                    "prices": [1000, 1500, 2000],
                    "top_level_category": "メンズファッション",
                    "brand_name": "",
                }
            ),
            category_client=FakeChatClient(
                {"best_target_path": "メンズファッション/トップス"}
            ),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
        )

        self.assertEqual(result["tax_excluded"], 980)
        self.assertEqual(result["tax_included"], 1078)
        self.assertEqual(result["prices"], [])

    def test_analyze_preserves_inferred_prices_when_no_direct_tax_excluded(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=FakeChatClient(
                {
                    "title": "シャツ",
                    "description": "メンズシャツ",
                    "tax_excluded": None,
                    "tax_included": None,
                    "prices": [1000, 1500, 2000],
                    "top_level_category": "メンズファッション",
                    "brand_name": "",
                }
            ),
            category_client=FakeChatClient(
                {"best_target_path": "メンズファッション/トップス"}
            ),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
        )

        self.assertIsNone(result["tax_excluded"])
        self.assertIsNone(result["tax_included"])
        self.assertEqual(result["prices"], [1000, 1500, 2000])

    def test_analyze_ignores_tax_included_price_without_direct_tax_excluded(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=FakeChatClient(
                {
                    "title": "シャツ",
                    "description": "メンズシャツ",
                    "tax_excluded": None,
                    "tax_included": "税込 1,078円",
                    "prices": [1000, 1500, 2000],
                    "top_level_category": "メンズファッション",
                    "brand_name": "",
                }
            ),
            category_client=FakeChatClient(
                {"best_target_path": "メンズファッション/トップス"}
            ),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
        )

        self.assertIsNone(result["tax_excluded"])
        self.assertIsNone(result["tax_included"])
        self.assertEqual(result["prices"], [1000, 1500, 2000])

    def test_analyze_direct_price_uses_first_number_from_combined_price_text(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=FakeChatClient(
                {
                    "title": "シャツ",
                    "description": "メンズシャツ",
                    "tax_excluded": "¥980（税込¥1,078）",
                    "tax_included": "税込¥1,078",
                    "prices": [1000, 1500, 2000],
                    "top_level_category": "メンズファッション",
                    "brand_name": "",
                }
            ),
            category_client=FakeChatClient(
                {"best_target_path": "メンズファッション/トップス"}
            ),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
        )

        self.assertEqual(result["tax_excluded"], 980)
        self.assertEqual(result["tax_included"], 1078)
        self.assertEqual(result["prices"], [])

    def test_title_analysis_image_fallback_reuses_priced_vision_prompt(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            request_timeout=60,
            max_image_bytes=1024,
            allowed_mime_types={"image/png"},
            log_llm_raw=False,
            category_llm_retry_enabled=False,
            category_llm_max_retries=0,
        )
        vision_client = RecordingChatClient(
            {
                "title": "シャツ",
                "description": "メンズシャツ",
                "prices": [1000, 1500, 2000],
                "top_level_category": "メンズファッション",
                "brand_name": "",
            }
        )
        category_client = SequenceChatClient(
            [
                {"top_level_category": ""},
                {"best_target_path": "メンズファッション/トップス"},
            ]
        )
        analyzer = MercariAnalyzer(
            settings=settings,
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=category_client,
        )

        with patch(
            "app.service.fetch_image_from_url",
            return_value=(b"image-bytes", "image/png"),
        ):
            result = analyzer.analyze_title(
                title="unknown shirt",
                image_url="https://example.com/item.png",
                language="ja",
            )

        self.assertEqual(result["best_target_path"], "メンズファッション/トップス")
        self.assertEqual(len(vision_client.calls), 1)
        user_content = vision_client.calls[0]["messages"][1]["content"][0]["text"]
        self.assertIn("tax_excluded", user_content)
        self.assertIn("If tax_excluded is visible, set prices to []", user_content)

    def test_paths_from_categories_includes_rakuten_id_for_best_and_alternatives(self):
        result = _paths_from_categories(
            [
                {
                    "id": "123",
                    "name": "メンズファッション/トップス",
                    "meru_id": "m-123",
                    "rakuma_id": "ra-123",
                    "zenplus_id": "z-123",
                },
                {
                    "id": "456",
                    "name": "メンズファッション/パンツ",
                    "meru_id": "m-456",
                    "rakuma_id": "ra-456",
                    "zenplus_id": "z-456",
                },
            ]
        )

        self.assertEqual(result["best_category_id"], "123")
        self.assertEqual(result["rakuten_id"], "123")
        self.assertEqual(result["alternatives"][0]["category_id"], "456")
        self.assertEqual(result["alternatives"][0]["rakuten_id"], "456")


if __name__ == "__main__":
    unittest.main()
