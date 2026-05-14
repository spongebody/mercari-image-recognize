import json
import unittest
from types import SimpleNamespace

from app.service import MercariAnalyzer


class RecordingChatClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return json.dumps(self.payload), {"choices": []}


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
        if brand_name == "Nike":
            return {
                "brand_name": "Nike",
                "brand_id_obj": {
                    "rakuten_brand_id": "nike-r",
                    "yshop_brand_id": "",
                    "yauc_brand_id": "",
                    "meru_brand_id": "nike-m",
                    "ebay_brand_id": "",
                    "rakuma_brand_id": "",
                    "amazon_brand_id": "",
                    "qoo10_brand_id": "",
                },
            }
        return None


class FakeCategoryStore:
    categories = {
        "メンズファッション/トップス": {
            "id": "123",
            "name": "メンズファッション/トップス",
            "meru_id": "m-123",
            "rakuma_id": "ra-123",
            "zenplus_id": "z-123",
        },
        "メンズファッション/アウター": {
            "id": "456",
            "name": "メンズファッション/アウター",
            "meru_id": "m-456",
            "rakuma_id": "ra-456",
            "zenplus_id": "z-456",
        },
    }

    def get_categories_by_group(self, group_name):
        return list(self.categories.values())

    def find_category(self, group_name, category_name):
        return self.categories.get(category_name)


def _settings():
    return SimpleNamespace(
        vision_model="vision-test",
        category_model="category-test",
        product_data_model="product-data-test",
        log_llm_raw=False,
        vision_fallback_models=[],
        category_fallback_models=[],
        model_call_max_retries=0,
        model_call_total_budget_seconds=10,
        request_timeout=10,
    )


class ParallelFlowServiceTest(unittest.TestCase):
    def test_classify_first_image_categories_uses_first_image_and_returns_confidence(self):
        vision_client = RecordingChatClient(
            {
                "title": "Nike シャツ",
                "simple_description": "Nikeのメンズシャツ",
                "top_level_category": "メンズファッション",
            }
        )
        category_client = RecordingChatClient(
            {
                "best_target_path": "メンズファッション/トップス",
                "confidence": 0.91,
                "alternatives": [
                    {
                        "target_path": "メンズファッション/アウター",
                        "confidence": 0.53,
                    }
                ],
            }
        )
        analyzer = MercariAnalyzer(
            settings=_settings(),
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=category_client,
        )

        result = analyzer.classify_first_image_categories(
            images=[
                (b"front-image", "image/png"),
                (b"back-image", "image/png"),
            ],
            language="ja",
        )

        vision_content = vision_client.calls[0]["messages"][1]["content"]
        image_parts = [part for part in vision_content if part["type"] == "image_url"]
        prompt_text = vision_content[0]["text"]
        category_prompt = category_client.calls[0]["messages"][1]["content"]
        self.assertEqual(len(image_parts), 1)
        self.assertNotIn("product_intro", prompt_text)
        self.assertNotIn("tax_excluded", prompt_text)
        self.assertNotIn("brand_name", prompt_text)
        self.assertIn("- Brand (may be empty): \n", category_prompt)
        self.assertEqual(result["status"], "product_pending")
        self.assertNotIn("brand_name", result)
        self.assertEqual(result["categories"][0]["confidence"], 0.91)
        self.assertEqual(result["categories"][1]["confidence"], 0.53)
        self.assertEqual(set(result["timings"].keys()), {"total_ms", "classification_ms"})
        self.assertEqual(result["timings"]["total_ms"], result["timings"]["classification_ms"])

    def test_generate_product_data_uses_product_data_model_and_returns_price_fields(self):
        vision_client = RecordingChatClient(
            {
                "title": "Nike シャツ",
                "description": {
                    "product_details": {
                        "brand": "Nike",
                        "product_name": "シャツ",
                        "model_number": "",
                        "target": "メンズ",
                        "color": "黒",
                        "size": "M",
                        "weight": "",
                        "condition": "良好",
                    },
                    "product_intro": "商品紹介",
                    "recommendation": "おすすめ",
                    "search_keywords": ["Nike", "シャツ"],
                },
                "brand_name": "Nike",
                "tax_excluded": "¥980",
                "tax_included": "税込 1,078円",
                "prices": [1000, 1500, 2000],
            }
        )
        analyzer = MercariAnalyzer(
            settings=_settings(),
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=SequenceChatClient([]),
        )

        result = analyzer.generate_product_data(
            images=[
                (b"front-image", "image/png"),
                (b"back-image", "image/png"),
            ],
            language="ja",
        )

        call = vision_client.calls[0]
        prompt_text = call["messages"][1]["content"][0]["text"]
        image_parts = [part for part in call["messages"][1]["content"] if part["type"] == "image_url"]
        self.assertEqual(call["model"], "product-data-test")
        self.assertEqual(len(image_parts), 2)
        self.assertIn("tax_excluded", prompt_text)
        self.assertIn("prices", prompt_text)
        self.assertEqual(result["tax_excluded"], 980)
        self.assertEqual(result["tax_included"], 1078)
        self.assertEqual(result["prices"], [])
        self.assertEqual(result["brand_name"], "Nike")
        self.assertEqual(result["brand_id_obj"]["rakuten_brand_id"], "nike-r")
        self.assertEqual(set(result["timings"].keys()), {"product_data_ms"})

    def test_generate_product_data_preserves_inferred_prices_when_no_direct_price(self):
        vision_client = RecordingChatClient(
            {
                "title": "Nike シャツ",
                "description": {
                    "product_details": {
                        "brand": "Nike",
                        "product_name": "シャツ",
                        "model_number": "",
                        "target": "メンズ",
                        "color": "黒",
                        "size": "M",
                        "weight": "",
                        "condition": "良好",
                    },
                    "product_intro": "商品紹介",
                    "recommendation": "おすすめ",
                    "search_keywords": ["Nike", "シャツ"],
                },
                "brand_name": "Nike",
                "tax_excluded": None,
                "tax_included": None,
                "prices": [1000, 1500, 2000],
            }
        )
        analyzer = MercariAnalyzer(
            settings=_settings(),
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=SequenceChatClient([]),
        )

        result = analyzer.generate_product_data(
            images=[(b"front-image", "image/png")],
            language="ja",
        )

        self.assertIsNone(result["tax_excluded"])
        self.assertIsNone(result["tax_included"])
        self.assertEqual(result["prices"], [1000, 1500, 2000])

    def test_generate_product_data_expands_short_title_to_at_least_80_chars(self):
        vision_client = RecordingChatClient(
            {
                "title": "Nike シャツ",
                "description": {
                    "product_details": {
                        "brand": "Nike",
                        "product_name": "Dri-FIT トレーニングシャツ",
                        "model_number": "DV1234-010",
                        "target": "メンズ",
                        "color": "ブラック",
                        "size": "M",
                        "weight": "",
                        "condition": "良好",
                    },
                    "product_intro": "速乾素材のスポーツウェアです。",
                    "recommendation": "普段使いにもトレーニングにもおすすめです。",
                    "search_keywords": ["Nike", "Dri-FIT", "トレーニングシャツ"],
                },
                "brand_name": "Nike",
            }
        )
        analyzer = MercariAnalyzer(
            settings=_settings(),
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=SequenceChatClient([]),
        )

        result = analyzer.generate_product_data(
            images=[(b"front-image", "image/png")],
            language="ja",
        )

        self.assertGreaterEqual(len(result["title"]), 80)
        self.assertTrue(result["title"].startswith("Nike シャツ"))
        self.assertIn("DV1234-010", result["title"])
        self.assertIn("ブラック", result["title"])

    def test_generate_product_data_supports_model_override_for_fallback(self):
        vision_client = RecordingChatClient(
            {
                "title": "Nike シャツ",
                "description": {
                    "product_details": {
                        "brand": "Nike",
                        "product_name": "シャツ",
                        "model_number": "",
                        "target": "",
                        "color": "",
                        "size": "",
                        "weight": "",
                        "condition": "",
                    },
                    "product_intro": "",
                    "recommendation": "",
                    "search_keywords": [],
                },
                "brand_name": "Nike",
            }
        )
        analyzer = MercariAnalyzer(
            settings=_settings(),
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=SequenceChatClient([]),
        )

        result = analyzer.generate_product_data(
            images=[(b"front-image", "image/png")],
            language="ja",
            model_override="openai/gpt-4o-mini",
        )

        self.assertEqual(vision_client.calls[0]["model"], "openai/gpt-4o-mini")
        self.assertEqual(result["brand_name"], "Nike")

    def test_regenerate_product_data_prioritizes_user_notes_and_original_data(self):
        vision_client = RecordingChatClient(
            {
                "title": "Nike Dri-FIT ブラック M 良好 トレーニング向け 速乾 スポーツウェア",
                "description": {
                    "product_details": {
                        "brand": "Nike",
                        "product_name": "Dri-FIT トレーニングシャツ",
                        "model_number": "DV1234-010",
                        "target": "メンズ",
                        "color": "ブラック",
                        "size": "M",
                        "weight": "",
                        "condition": "目立つ傷なし",
                    },
                    "product_intro": "ユーザー補足を反映した商品紹介です。",
                    "recommendation": "同款を探している方におすすめです。",
                    "search_keywords": ["Nike", "Dri-FIT", "明らか同款", "イタリア真皮"],
                },
                "brand_name": "Nike",
            }
        )
        analyzer = MercariAnalyzer(
            settings=_settings(),
            brand_store=FakeBrandStore(),
            category_store=FakeCategoryStore(),
            vision_client=vision_client,
            category_client=SequenceChatClient([]),
        )

        result = analyzer.regenerate_product_data(
            images=[(b"front-image", "image/png")],
            language="ja",
            original_product_data={
                "title": "古いタイトル",
                "description": {"product_details": {"condition": "傷あり"}},
                "brand_name": "Nike",
            },
            user_notes="成色は目立つ傷なし。明らか同款。イタリア真皮という説明を優先。",
        )

        prompt_text = vision_client.calls[0]["messages"][1]["content"][0]["text"]
        self.assertIn("User supplemental information", prompt_text)
        self.assertIn("成色は目立つ傷なし", prompt_text)
        self.assertIn("Original product data", prompt_text)
        self.assertIn("古いタイトル", prompt_text)
        self.assertEqual(vision_client.calls[0]["model"], "product-data-test")
        self.assertGreaterEqual(len(result["title"]), 80)
        self.assertEqual(result["brand_name"], "Nike")


if __name__ == "__main__":
    unittest.main()
