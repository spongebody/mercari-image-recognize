import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from console_auth_helpers import auth_headers
import main as main_module


_SUCCESS_PAYLOAD = {
    "request_id": "req-success",
    "status": "succeeded",
    "model": "google/gemini-3.1-flash-image-preview",
    "model_override": None,
    "prompt_hint": "studio",
    "final_prompt": "demo prompt",
    "image_base64": "aW1hZ2U=",
    "image_mime_type": "image/png",
    "input_path": None,
    "output_path": None,
    "latency_ms": 1234,
    "created_at": "2026-04-28T20:30:00+08:00",
}

_FAILURE_PAYLOAD = {
    "request_id": "req-failure",
    "status": "failed",
    "model": "google/gemini-3.1-flash-image-preview",
    "model_override": None,
    "error_code": "upstream_generation_failed",
    "error_message": "OpenRouter returned no usable image payload.",
    "latency_ms": 1234,
    "created_at": "2026-04-28T20:30:00+08:00",
}


class GenerateShowcaseRouteTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main_module.app)

    def test_rejects_missing_file(self):
        response = self.client.post(
            "/api/v1/showcase/generate",
            headers=auth_headers(),
            data={"prompt_hint": "studio"},
        )
        self.assertEqual(response.status_code, 422)

    def test_rejects_non_image_content_type(self):
        response = self.client.post(
            "/api/v1/showcase/generate",
            headers=auth_headers(),
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("image", response.json()["detail"].lower())

    def test_rejects_empty_image(self):
        response = self.client.post(
            "/api/v1/showcase/generate",
            headers=auth_headers(),
            files={"file": ("bag.jpg", b"", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Uploaded file is empty.")

    @patch.object(main_module, "showcase_service")
    def test_returns_200_and_payload_on_success(self, service):
        service.generate_showcase.return_value = _SUCCESS_PAYLOAD

        response = self.client.post(
            "/api/v1/showcase/generate",
            headers=auth_headers(),
            files={"file": ("bag.jpg", b"fake-image", "image/jpeg")},
            data={"prompt_hint": "studio"},
        )

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["image_base64"], "aW1hZ2U=")

        service.generate_showcase.assert_called_once()
        kwargs = service.generate_showcase.call_args.kwargs
        self.assertEqual(kwargs["upload_filename"], "bag.jpg")
        self.assertEqual(kwargs["content_type"], "image/jpeg")
        self.assertEqual(kwargs["image_bytes"], b"fake-image")
        self.assertEqual(kwargs["prompt_hint"], "studio")
        self.assertIsNone(kwargs["model_override"])

    @patch.object(main_module, "showcase_service")
    def test_forwards_model_form_field_as_override(self, service):
        service.generate_showcase.return_value = _SUCCESS_PAYLOAD

        response = self.client.post(
            "/api/v1/showcase/generate",
            headers=auth_headers(),
            files={"file": ("bag.jpg", b"fake-image", "image/jpeg")},
            data={"prompt_hint": "studio", "model": "openai/gpt-image-1"},
        )

        self.assertEqual(response.status_code, 200)
        kwargs = service.generate_showcase.call_args.kwargs
        self.assertEqual(kwargs["model_override"], "openai/gpt-image-1")

    @patch.object(main_module, "showcase_service")
    def test_returns_502_on_service_failure_payload(self, service):
        service.generate_showcase.return_value = _FAILURE_PAYLOAD

        response = self.client.post(
            "/api/v1/showcase/generate",
            headers=auth_headers(),
            files={"file": ("chair.jpg", b"fake-image", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 502)
        body = response.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error_code"], "upstream_generation_failed")


class HealthRouteShowcaseModelTest(unittest.TestCase):
    def test_health_includes_showcase_model(self):
        client = TestClient(main_module.app)
        response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertIn("showcase_model", response.json()["models"])


if __name__ == "__main__":
    unittest.main()
