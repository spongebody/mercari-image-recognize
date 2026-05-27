import concurrent.futures
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app.errors import LLMAllAttemptsFailedError
from app.llm.resilient import AttemptRecord
import main


class _BaseFallbackTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)
        self._original_fallback_model = main.settings.product_data_fallback_model
        self._original_fallback_timeout = main.settings.product_data_fallback_timeout_seconds
        main.settings.product_data_fallback_model = "openai/gpt-4o-mini"
        main.settings.product_data_fallback_timeout_seconds = 10.0

    def tearDown(self):
        main.settings.product_data_fallback_model = self._original_fallback_model
        main.settings.product_data_fallback_timeout_seconds = self._original_fallback_timeout

    def _classification_payload(self):
        return {
            "status": "product_pending",
            "categories": [
                {
                    "id": "123",
                    "rakuten_id": "123",
                    "name": "メンズ/トップス",
                    "confidence": 0.9,
                }
            ],
            "timings": {"total_ms": 80.0, "classification_ms": 80.0},
            "image_processing": [],
        }

    def _product_payload(self, brand_name="Nike", brand_rakuten="nike-r", product_data_ms=900.0):
        return {
            "title": f"{brand_name} シャツ",
            "description": {
                "product_details": {
                    "brand": brand_name,
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
                "search_keywords": [brand_name],
            },
            "brand_name": brand_name,
            "brand_id_obj": {"rakuten_brand_id": brand_rakuten},
            "timings": {"product_data_ms": product_data_ms},
        }

    def _post_analyze(self):
        return self.client.post(
            "/api/v1/mercari/image/analyze",
            files=[("image_list", ("front.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "ja"},
        )


class ProductDataFallbackTest(_BaseFallbackTest):
    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_dispatches_primary_and_fallback_calls_in_parallel(self, analyzer, executor):
        primary_future = concurrent.futures.Future()
        fallback_future = concurrent.futures.Future()
        # _submit_with_request_id delegates to product_data_executor.submit(_runner)
        # so the mock receives a single positional callable; return the right futures.
        executor.submit.side_effect = [primary_future, fallback_future]
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        # Capture _submit_with_request_id calls to inspect original fn kwargs.
        original_submit = main._submit_with_request_id
        captured_calls = []

        def recording_submit(fn, /, *args, **kwargs):
            captured_calls.append({"fn": fn, "args": args, "kwargs": kwargs})
            return original_submit(fn, *args, **kwargs)

        with patch.object(main, "_submit_with_request_id", side_effect=recording_submit):
            response = self._post_analyze()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(captured_calls), 2)

        primary_call_kwargs = captured_calls[0]["kwargs"]
        fallback_call_kwargs = captured_calls[1]["kwargs"]
        self.assertNotIn("model_override", primary_call_kwargs)
        self.assertEqual(
            fallback_call_kwargs.get("model_override"),
            "openai/gpt-4o-mini",
        )

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_uses_fallback_when_primary_pending_after_timeout(
        self, analyzer, executor
    ):
        # Force the fallback timeout to elapse instantly so any poll after
        # initial dispatch is considered "after the configured timeout".
        main.settings.product_data_fallback_timeout_seconds = 0.0
        primary_future = concurrent.futures.Future()
        fallback_future = concurrent.futures.Future()
        executor.submit.side_effect = [primary_future, fallback_future]
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        response = self._post_analyze()
        self.assertEqual(response.status_code, 200)
        body = response.json()

        early_poll = self.client.get(f"/api/v1/mercari/image/analyze/{body['job_id']}")
        self.assertEqual(early_poll.status_code, 200)
        self.assertEqual(early_poll.json()["status"], "product_pending")

        fallback_future.set_result(self._product_payload(product_data_ms=300.0))

        late_poll = self.client.get(f"/api/v1/mercari/image/analyze/{body['job_id']}")
        self.assertEqual(late_poll.status_code, 200)
        completed = late_poll.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["product_data_source"], "fallback")
        self.assertEqual(completed["brand_name"], "Nike")
        self.assertEqual(completed["timings"]["product_data_ms"], 300.0)

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_keeps_pending_when_within_timeout_even_if_fallback_completed(
        self, analyzer, executor
    ):
        # An effectively infinite timeout means the elapsed wall-clock cannot
        # exceed it inside a unit test.
        main.settings.product_data_fallback_timeout_seconds = 3600.0
        primary_future = concurrent.futures.Future()
        fallback_future = concurrent.futures.Future()
        executor.submit.side_effect = [primary_future, fallback_future]
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        response = self._post_analyze()
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["job_id"]

        fallback_future.set_result(self._product_payload(product_data_ms=400.0))

        poll = self.client.get(f"/api/v1/mercari/image/analyze/{job_id}")
        self.assertEqual(poll.status_code, 200)
        self.assertEqual(poll.json()["status"], "product_pending")
        self.assertNotIn("product_data_source", poll.json())

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_prefers_primary_when_primary_finishes_first(self, analyzer, executor):
        # Even with a 0 timeout (fallback is allowed to win), primary success
        # always wins when it is available.
        main.settings.product_data_fallback_timeout_seconds = 0.0
        primary_future = concurrent.futures.Future()
        fallback_future = concurrent.futures.Future()
        executor.submit.side_effect = [primary_future, fallback_future]
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        response = self._post_analyze()
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["job_id"]

        primary_future.set_result(
            self._product_payload(brand_name="Adidas", brand_rakuten="adi-r", product_data_ms=500.0)
        )

        poll = self.client.get(f"/api/v1/mercari/image/analyze/{job_id}")
        self.assertEqual(poll.status_code, 200)
        completed = poll.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["product_data_source"], "primary")
        self.assertEqual(completed["brand_name"], "Adidas")
        self.assertEqual(completed["timings"]["product_data_ms"], 500.0)

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_uses_fallback_when_primary_fails(self, analyzer, executor):
        attempts = [
            AttemptRecord(
                model="primary-model",
                attempt=1,
                attempt_global=1,
                error_kind="request_failed",
                message="primary went boom",
                latency_ms=5.0,
                status_code=500,
            )
        ]
        primary_future = concurrent.futures.Future()
        primary_future.set_exception(LLMAllAttemptsFailedError("product_data", attempts))
        fallback_future = concurrent.futures.Future()
        fallback_future.set_result(self._product_payload(brand_name="Puma", brand_rakuten="puma-r", product_data_ms=420.0))
        executor.submit.side_effect = [primary_future, fallback_future]
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        response = self._post_analyze()
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["job_id"]

        poll = self.client.get(f"/api/v1/mercari/image/analyze/{job_id}")
        self.assertEqual(poll.status_code, 200)
        completed = poll.json()
        self.assertEqual(completed["status"], "completed")
        self.assertEqual(completed["product_data_source"], "fallback")
        self.assertEqual(completed["brand_name"], "Puma")

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_returns_502_when_both_primary_and_fallback_fail(self, analyzer, executor):
        attempts = [
            AttemptRecord(
                model="primary-model",
                attempt=1,
                attempt_global=1,
                error_kind="request_failed",
                message="primary went boom",
                latency_ms=5.0,
                status_code=500,
            )
        ]
        primary_future = concurrent.futures.Future()
        primary_future.set_exception(LLMAllAttemptsFailedError("product_data", attempts))
        fallback_future = concurrent.futures.Future()
        fallback_future.set_exception(LLMAllAttemptsFailedError("product_data", attempts))
        executor.submit.side_effect = [primary_future, fallback_future]
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        response = self._post_analyze()
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["job_id"]

        poll = self.client.get(f"/api/v1/mercari/image/analyze/{job_id}")
        self.assertEqual(poll.status_code, 502)


class ProductDataFallbackDisabledTest(_BaseFallbackTest):
    def setUp(self):
        super().setUp()
        main.settings.product_data_fallback_model = ""

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_only_primary_dispatched_when_fallback_disabled(self, analyzer, executor):
        primary_future = concurrent.futures.Future()
        executor.submit.return_value = primary_future
        analyzer.classify_first_image_categories.return_value = self._classification_payload()

        response = self._post_analyze()
        self.assertEqual(response.status_code, 200)

        self.assertEqual(executor.submit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
