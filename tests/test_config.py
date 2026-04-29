import importlib
import unittest
from unittest.mock import patch

import app.config as config_module


class SettingsConfigTest(unittest.TestCase):
    def test_image_compression_threshold_mb_defaults_to_one_mb(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()

        self.assertEqual(settings.image_compression_threshold_mb, 1)
        self.assertEqual(settings.image_compression_threshold_bytes, 1024 * 1024)

    def test_image_compression_threshold_mb_converts_to_bytes(self):
        with patch.dict("os.environ", {"IMAGE_COMPRESSION_THRESHOLD_MB": "3"}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()

        self.assertEqual(settings.image_compression_threshold_mb, 3)
        self.assertEqual(settings.image_compression_threshold_bytes, 3 * 1024 * 1024)

    def test_request_timeout_zero_falls_back_to_default(self):
        with patch.dict("os.environ", {"REQUEST_TIMEOUT": "0"}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()

        self.assertEqual(settings.request_timeout, 60)

    def test_default_fallback_models_when_env_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.vision_fallback_models[0], "google/gemini-3-flash-preview")
        self.assertEqual(len(settings.vision_fallback_models), 7)
        self.assertEqual(
            settings.vision_fallback_models,
            settings.category_fallback_models,
        )

    def test_fallback_models_from_env_csv(self):
        with patch.dict(
            "os.environ",
            {"VISION_FALLBACK_MODELS": "a/b , , c/d"},
            clear=True,
        ):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.vision_fallback_models, ["a/b", "c/d"])

    def test_model_call_budget_defaults(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.model_call_max_retries, 3)
        self.assertEqual(settings.model_call_total_budget_seconds, 120)

    def test_model_call_budget_from_env(self):
        with patch.dict(
            "os.environ",
            {"MODEL_CALL_MAX_RETRIES": "1", "MODEL_CALL_TOTAL_BUDGET_SECONDS": "60"},
            clear=True,
        ):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertEqual(settings.model_call_max_retries, 1)
        self.assertEqual(settings.model_call_total_budget_seconds, 60)

    def test_deprecated_category_retry_fields_are_removed(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()
        self.assertFalse(hasattr(settings, "category_llm_retry_enabled"))
        self.assertFalse(hasattr(settings, "category_llm_max_retries"))


if __name__ == "__main__":
    unittest.main()
