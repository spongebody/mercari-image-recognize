import concurrent.futures
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.errors import LLMAllAttemptsFailedError
from app.llm.resilient import AttemptRecord
import main


class ImageAnalyzeJobsTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_initial_response_omits_brand_until_polling_completes(self, analyzer, executor):
        product_future = concurrent.futures.Future()
        executor.submit.return_value = product_future
        analyzer.classify_first_image_categories.return_value = {
            "status": "product_pending",
            "categories": [
                {
                    "id": "123",
                    "rakuten_id": "123",
                    "name": "メンズファッション/トップス",
                    "confidence": 0.92,
                }
            ],
            "best_target_path": "メンズファッション/トップス",
            "best_category_id": "123",
            "rakuten_id": "123",
            "timings": {"total_ms": 100.0, "classification_ms": 100.0},
            "image_processing": [
                {
                    "index": 1,
                    "filename": "front.png",
                    "compressed": True,
                    "original_bytes": 2_000_000,
                    "processed_bytes": 500_000,
                }
            ],
        }

        response = self.client.post(
            "/api/v1/mercari/image/analyze",
            files=[
                ("image_list", ("front.png", b"\x89PNG\r\n\x1a\n", "image/png")),
                ("image_list", ("back.png", b"\x89PNG\r\n\x1a\n", "image/png")),
            ],
            data={"language": "ja"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "product_pending")
        self.assertIn("job_id", body)
        self.assertNotIn("brand_name", body)
        self.assertNotIn("brand_id_obj", body)
        self.assertEqual(body["image_processing"][0]["compressed"], True)
        analyzer.classify_first_image_categories.assert_called_once()
        self.assertEqual(analyzer.classify_first_image_categories.call_args.kwargs["category_limit"], 3)
        self.assertEqual(body["timings"], {"total_ms": 100.0, "classification_ms": 100.0})

        pending_response = self.client.get(f"/api/v1/mercari/image/analyze/{body['job_id']}")
        self.assertEqual(pending_response.status_code, 200)
        self.assertEqual(pending_response.json()["status"], "product_pending")
        self.assertNotIn("brand_name", pending_response.json())
        self.assertEqual(pending_response.json()["image_processing"][0]["compressed"], True)

        product_future.set_result(
            {
                "title": "シャツ",
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
                    "product_intro": "紹介文",
                    "recommendation": "おすすめ",
                    "search_keywords": ["Nike", "シャツ"],
                },
                "brand_name": "Nike",
                "brand_id_obj": {"rakuten_brand_id": "nike-r"},
                "timings": {"product_data_ms": 250.0},
            }
        )

        completed_response = self.client.get(f"/api/v1/mercari/image/analyze/{body['job_id']}")
        self.assertEqual(completed_response.status_code, 200)
        completed = completed_response.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["brand_name"], "Nike")
        self.assertEqual(completed["brand_id_obj"]["rakuten_brand_id"], "nike-r")
        self.assertEqual(completed["categories"][0]["confidence"], 0.92)
        self.assertEqual(completed["image_processing"][0]["compressed"], True)
        self.assertEqual(
            completed["timings"],
            {
                "total_ms": 250.0,
                "classification_ms": 100.0,
                "product_data_ms": 250.0,
                "product_data_primary_ms": 250.0,
                "product_data_fallback_ms": 250.0,
            },
        )

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_product_data_failure_does_not_block_initial_category_response(self, analyzer, executor):
        attempts = [
            AttemptRecord(
                model="product-model",
                attempt=1,
                attempt_global=1,
                error_kind="request_failed",
                message="OpenRouter returned 503: x",
                latency_ms=10.0,
                status_code=503,
            )
        ]
        product_future = concurrent.futures.Future()
        product_future.set_exception(LLMAllAttemptsFailedError("product_data", attempts))
        executor.submit.return_value = product_future
        analyzer.classify_first_image_categories.return_value = {
            "status": "product_pending",
            "categories": [
                {
                    "id": "123",
                    "rakuten_id": "123",
                    "name": "メンズファッション/トップス",
                    "confidence": 0.92,
                }
            ],
            "timings": {"total_ms": 100.0, "classification_ms": 100.0},
        }

        response = self.client.post(
            "/api/v1/mercari/image/analyze",
            files=[("image_list", ("front.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "ja", "category_count": "3"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "product_pending")
        self.assertEqual(body["categories"][0]["confidence"], 0.92)

        poll_response = self.client.get(f"/api/v1/mercari/image/analyze/{body['job_id']}")
        self.assertEqual(poll_response.status_code, 502)
        self.assertEqual(poll_response.json()["detail"]["stage"], "product_data")

    @patch.object(main, "analyzer")
    def test_polling_unknown_job_returns_404(self, analyzer):
        response = self.client.get("/api/v1/mercari/image/analyze/not-found")

        self.assertEqual(response.status_code, 404)

    def test_merge_analysis_payload_preserves_debug_attempts_from_both_paths(self):
        merged = main._merge_analysis_payload(
            {
                "status": "product_pending",
                "categories": [],
                "_debug": {
                    "fast_ai_raw": {"title": "x"},
                    "attempts": {
                        "fast_vision": [{"model": "vision"}],
                        "category": [{"model": "category"}],
                    },
                },
            },
            {
                "title": "x",
                "_debug": {
                    "product_data_ai_raw": {"title": "x"},
                    "attempts": {
                        "product_data": [{"model": "product"}],
                    },
                },
            },
        )

        self.assertIn("fast_vision", merged["_debug"]["attempts"])
        self.assertIn("category", merged["_debug"]["attempts"])
        self.assertIn("product_data", merged["_debug"]["attempts"])

    def test_merge_analysis_payload_total_ms_uses_max_of_parallel_paths(self):
        merged = main._merge_analysis_payload(
            {
                "status": "product_pending",
                "categories": [],
                "timings": {"total_ms": 500.0, "classification_ms": 500.0},
            },
            {
                "title": "x",
                "timings": {"product_data_ms": 1500.0},
            },
        )

        self.assertEqual(merged["timings"]["classification_ms"], 500.0)
        self.assertEqual(merged["timings"]["product_data_ms"], 1500.0)
        self.assertEqual(merged["timings"]["total_ms"], 1500.0)


if __name__ == "__main__":
    unittest.main()
