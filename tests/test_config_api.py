import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


class ConfigApiTest(unittest.TestCase):
    def setUp(self):
        self.original_values = {
            "vision_model": main.settings.vision_model,
            "category_model": main.settings.category_model,
            "showcase_model": main.settings.showcase_model,
            "log_requests": main.settings.log_requests,
            "request_timeout": main.settings.request_timeout,
            "vision_client_timeout": main.vision_client.timeout,
            "category_client_timeout": main.category_client.timeout,
            "showcase_client_model": main.showcase_image_client.model,
            "showcase_service_model": main.showcase_service.model,
        }

    def tearDown(self):
        main.settings.vision_model = self.original_values["vision_model"]
        main.settings.category_model = self.original_values["category_model"]
        main.settings.showcase_model = self.original_values["showcase_model"]
        main.settings.log_requests = self.original_values["log_requests"]
        main.settings.request_timeout = self.original_values["request_timeout"]
        main.vision_client.timeout = self.original_values["vision_client_timeout"]
        main.category_client.timeout = self.original_values["category_client_timeout"]
        main.showcase_image_client.model = self.original_values["showcase_client_model"]
        main.showcase_service.model = self.original_values["showcase_service_model"]

    def test_config_api_updates_runtime_settings_and_env_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("VISION_MODEL=old-vision\n", encoding="utf-8")

            with patch("main.CONFIG_ENV_PATH", env_path):
                client = TestClient(main.app)
                response = client.put(
                    "/api/v1/config",
                    json={
                        "VISION_MODEL": "api-vision",
                        "CATEGORY_MODEL": "api-category",
                        "LOG_REQUESTS": False,
                        "REQUEST_TIMEOUT": 45,
                    },
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["VISION_MODEL"], "api-vision")
            self.assertEqual(main.settings.vision_model, "api-vision")
            self.assertEqual(main.settings.category_model, "api-category")
            self.assertFalse(main.settings.log_requests)
            self.assertEqual(main.vision_client.timeout, 45)
            self.assertEqual(main.category_client.timeout, 45)
            self.assertIn("VISION_MODEL=api-vision", env_path.read_text(encoding="utf-8"))

    def test_config_api_updates_showcase_model_and_syncs_clients(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("SHOWCASE_MODEL=old-showcase\n", encoding="utf-8")

            with patch("main.CONFIG_ENV_PATH", env_path):
                client = TestClient(main.app)
                response = client.put(
                    "/api/v1/config",
                    json={"SHOWCASE_MODEL": "openai/gpt-image-1"},
                )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["SHOWCASE_MODEL"], "openai/gpt-image-1")
            self.assertEqual(main.settings.showcase_model, "openai/gpt-image-1")
            self.assertEqual(main.showcase_image_client.model, "openai/gpt-image-1")
            self.assertEqual(main.showcase_service.model, "openai/gpt-image-1")
            self.assertIn(
                "SHOWCASE_MODEL=openai/gpt-image-1",
                env_path.read_text(encoding="utf-8"),
            )

    def test_config_api_rejects_cross_origin_write(self):
        client = TestClient(main.app)

        response = client.put(
            "/api/v1/config",
            headers={"Origin": "https://evil.example", "Host": "api.example"},
            json={"VISION_MODEL": "api-vision"},
        )

        self.assertEqual(response.status_code, 403)

    def test_config_page_served_by_api_app(self):
        client = TestClient(main.app)

        response = client.get("/config")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn("API 配置", response.text)


if __name__ == "__main__":
    unittest.main()
