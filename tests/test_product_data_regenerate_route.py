import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from console_auth_helpers import auth_headers
import main


class ProductDataRegenerateRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    @patch.object(main, "analyzer")
    def test_regenerates_product_data_from_images_original_data_and_user_notes(self, analyzer):
        analyzer.regenerate_product_data.return_value = {
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
                "product_intro": "補足情報を反映した紹介文",
                "recommendation": "おすすめ文",
                "search_keywords": ["Nike", "Dri-FIT"],
            },
            "brand_name": "Nike",
            "brand_id_obj": {"rakuten_brand_id": "nike-r"},
            "timings": {"product_data_ms": 120.0},
        }

        response = self.client.post(
            "/api/v1/mercari/product-data/regenerate",
            headers=auth_headers(),
            files=[("image_list", ("front.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={
                "language": "ja",
                "original_product_data": json.dumps(
                    {"title": "古いタイトル", "brand_name": "Nike"},
                    ensure_ascii=False,
                ),
                "user_notes": "成色は目立つ傷なし。明らか同款。",
            },
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["brand_name"], "Nike")
        self.assertEqual(
            set(body["description"]["product_details"].keys()),
            {"brand", "product_name", "model_number", "color"},
        )
        self.assertEqual(body["description"]["product_details"]["color"], "ブラック")
        analyzer.regenerate_product_data.assert_called_once()
        call_kwargs = analyzer.regenerate_product_data.call_args.kwargs
        self.assertEqual(len(call_kwargs["images"]), 1)
        self.assertEqual(call_kwargs["language"], "ja")
        self.assertEqual(call_kwargs["original_product_data"]["title"], "古いタイトル")
        self.assertIn("明らか同款", call_kwargs["user_notes"])

    def test_rejects_invalid_original_product_data_json(self):
        response = self.client.post(
            "/api/v1/mercari/product-data/regenerate",
            headers=auth_headers(),
            files=[("image_list", ("front.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "ja", "original_product_data": "{not-json"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("original_product_data", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
