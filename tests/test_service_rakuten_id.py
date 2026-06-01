import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.llm import prompt_store
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
            "meru_path": "Mercari > Tops",
            "rakuma_path": "Rakuma>Tops",
            "zenplus_path": "ZenPlus>Tops",
        }
    }

    def get_categories_by_group(self, group_name):
        return list(self.categories.values())

    def find_category(self, group_name, category_name):
        return self.categories.get(category_name)


class RakutenIdResponseTest(unittest.TestCase):
    def test_old_sync_analyze_method_is_removed(self):
        self.assertFalse(hasattr(MercariAnalyzer, "analyze"))

    def test_title_analysis_image_fallback_uses_named_prompt(self):
        settings = SimpleNamespace(
            vision_model="vision-test",
            category_model="category-test",
            request_timeout=60,
            max_image_bytes=1024,
            allowed_mime_types={"image/png"},
            log_requests=False,
            vision_fallback_models=[],
            category_fallback_models=[],
            model_call_max_retries=0,
            model_call_total_budget_seconds=10,
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
        self.assertEqual(
            vision_client.calls[0]["messages"][0]["content"],
            prompt_store.render_system("TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT"),
        )
        user_content = vision_client.calls[0]["messages"][1]["content"][0]["text"]
        self.assertIn("top_level_category", user_content)
        self.assertIn("simple_description", user_content)
        self.assertNotIn("tax_excluded", user_content)
        self.assertNotIn("prices", user_content)

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
