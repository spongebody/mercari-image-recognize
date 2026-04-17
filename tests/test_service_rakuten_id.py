import json
import unittest
from types import SimpleNamespace

from app.service import MercariAnalyzer, _paths_from_categories


class FakeChatClient:
    def __init__(self, payload):
        self.payload = payload

    def chat(self, **kwargs):
        return json.dumps(self.payload), {"choices": []}


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
            price_client=FakeChatClient({}),
        )

        result = analyzer.analyze(
            images=[(b"image-bytes", "image/png")],
            language="ja",
            category_limit=1,
            price_strategy="vision",
        )

        self.assertEqual(result["categories"][0]["id"], "123")
        self.assertEqual(result["categories"][0]["rakuten_id"], "123")
        self.assertEqual(result["best_category_id"], "123")
        self.assertEqual(result["rakuten_id"], "123")

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
