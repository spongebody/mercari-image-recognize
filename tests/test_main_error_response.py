import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.errors import BadRequestError, LLMAllAttemptsFailedError
from app.llm.resilient import AttemptRecord
from console_auth_helpers import auth_headers
import main as main_module


class MainErrorResponseTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)

    @patch.object(main_module, "analyzer")
    def test_llm_all_attempts_failed_returns_structured_502(self, analyzer):
        attempts = [
            AttemptRecord(
                model="m1", attempt=1, attempt_global=1,
                error_kind="request_failed", message="OpenRouter returned 503: x",
                latency_ms=1234.5, status_code=503,
            ),
            AttemptRecord(
                model="m1", attempt=2, attempt_global=2,
                error_kind="parse_failed", message="JSON decode failed",
                latency_ms=42.0, status_code=200,
            ),
        ]
        analyzer.classify_first_image_categories.side_effect = LLMAllAttemptsFailedError("fast_vision", attempts)

        resp = self.client.post(
            "/api/v1/mercari/image/analyze",
            headers=auth_headers(),
            files=[("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "en"},
        )

        self.assertEqual(resp.status_code, 502)
        body = resp.json()
        detail = body["detail"]
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["stage"], "fast_vision")
        self.assertEqual(detail["kind"], "all_attempts_failed")
        self.assertEqual(len(detail["attempts"]), 2)
        self.assertEqual(detail["attempts"][0]["error_kind"], "request_failed")
        self.assertEqual(detail["attempts"][0]["status_code"], 503)
        self.assertEqual(detail["attempts"][1]["error_kind"], "parse_failed")

    @patch.object(main_module, "analyzer")
    def test_bad_request_returns_string_400(self, analyzer):
        analyzer.classify_first_image_categories.side_effect = BadRequestError("Image list is required.")

        resp = self.client.post(
            "/api/v1/mercari/image/analyze",
            headers=auth_headers(),
            files=[("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
            data={"language": "en"},
        )

        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertIsInstance(body["detail"], str)
        self.assertEqual(body["detail"], "Image list is required.")


if __name__ == "__main__":
    unittest.main()
