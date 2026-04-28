import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.runtime_config import get_public_config, update_runtime_config


class RuntimeConfigTest(unittest.TestCase):
    def test_update_runtime_config_writes_env_and_updates_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "\n".join(
                    [
                        "OPENROUTER_API_KEY=secret-key",
                        "VISION_MODEL=old-vision",
                        "LOG_REQUESTS=false",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            settings = SimpleNamespace(
                vision_model="old-vision",
                category_model="old-category",
                log_llm_raw=False,
                log_requests=False,
                enable_debug_param=True,
                category_llm_retry_enabled=False,
                category_llm_max_retries=1,
                image_compression_threshold_mb=1,
                request_timeout=60,
            )

            result = update_runtime_config(
                settings,
                {
                    "VISION_MODEL": "new-vision",
                    "CATEGORY_MODEL": "new-category",
                    "LOG_REQUESTS": True,
                    "CATEGORY_LLM_MAX_RETRIES": 3,
                },
                env_path=env_path,
            )

            self.assertEqual(result["VISION_MODEL"], "new-vision")
            self.assertEqual(settings.vision_model, "new-vision")
            self.assertEqual(settings.category_model, "new-category")
            self.assertTrue(settings.log_requests)
            self.assertEqual(settings.category_llm_max_retries, 3)

            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("OPENROUTER_API_KEY=secret-key", env_text)
            self.assertIn("VISION_MODEL=new-vision", env_text)
            self.assertIn("CATEGORY_MODEL=new-category", env_text)
            self.assertIn("LOG_REQUESTS=true", env_text)
            self.assertIn("CATEGORY_LLM_MAX_RETRIES=3", env_text)

    def test_get_public_config_omits_api_key(self):
        settings = SimpleNamespace(
            vision_model="vision",
            category_model="category",
            log_llm_raw=True,
            log_requests=True,
            enable_debug_param=False,
            category_llm_retry_enabled=True,
            category_llm_max_retries=2,
            image_compression_threshold_mb=1,
            request_timeout=30,
            openrouter_api_key="secret-key",
        )

        result = get_public_config(settings)

        self.assertEqual(result["VISION_MODEL"], "vision")
        self.assertNotIn("OPENROUTER_API_KEY", result)

    def test_update_rejects_multiline_string_values(self):
        settings = SimpleNamespace(
            vision_model="vision",
            category_model="category",
            log_llm_raw=False,
            log_requests=True,
            enable_debug_param=True,
            category_llm_retry_enabled=False,
            category_llm_max_retries=1,
            image_compression_threshold_mb=1,
            request_timeout=60,
        )

        with self.assertRaises(ValueError):
            update_runtime_config(settings, {"VISION_MODEL": "model\nLOG_REQUESTS=false"})

    def test_update_rejects_zero_request_timeout(self):
        settings = SimpleNamespace(
            vision_model="vision",
            category_model="category",
            log_llm_raw=False,
            log_requests=True,
            enable_debug_param=True,
            category_llm_retry_enabled=False,
            category_llm_max_retries=1,
            image_compression_threshold_mb=1,
            request_timeout=60,
        )

        with self.assertRaises(ValueError):
            update_runtime_config(settings, {"REQUEST_TIMEOUT": 0})


if __name__ == "__main__":
    unittest.main()
