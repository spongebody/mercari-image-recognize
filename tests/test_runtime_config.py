import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.runtime_config import get_public_config, update_runtime_config


def _fake_settings(**overrides):
    base = dict(
        vision_model="old-vision",
        category_model="old-category",
        product_data_model="old-product-data",
        product_data_fallback_model="openai/gpt-4o-mini",
        product_data_fallback_timeout_seconds=10.0,
        showcase_model="old-showcase",
        log_llm_raw=False,
        log_requests=False,
        enable_debug_param=True,
        image_compression_threshold_mb=1,
        request_timeout=60,
        vision_fallback_models=["a/b"],
        category_fallback_models=["a/b"],
        model_call_max_retries=3,
        model_call_total_budget_seconds=120,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class RuntimeConfigTest(unittest.TestCase):
    def test_update_writes_env_and_updates_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "OPENROUTER_API_KEY=secret-key\nVISION_MODEL=old-vision\n",
                encoding="utf-8",
            )
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {
                    "VISION_MODEL": "new-vision",
                    "CATEGORY_MODEL": "new-category",
                    "LOG_REQUESTS": True,
                    "MODEL_CALL_MAX_RETRIES": 1,
                },
                env_path=env_path,
            )

            self.assertEqual(result["VISION_MODEL"], "new-vision")
            self.assertEqual(settings.category_model, "new-category")
            self.assertTrue(settings.log_requests)
            self.assertEqual(settings.model_call_max_retries, 1)

            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("OPENROUTER_API_KEY=secret-key", env_text)
            self.assertIn("VISION_MODEL=new-vision", env_text)
            self.assertIn("CATEGORY_MODEL=new-category", env_text)
            self.assertIn("LOG_REQUESTS=true", env_text)
            self.assertIn("MODEL_CALL_MAX_RETRIES=1", env_text)

    def test_get_public_config_returns_lists_for_fallbacks(self):
        settings = _fake_settings(
            vision_fallback_models=["m1", "m2"],
            category_fallback_models=["m3"],
            openrouter_api_key="secret",
        )

        result = get_public_config(settings)

        self.assertEqual(result["VISION_FALLBACK_MODELS"], ["m1", "m2"])
        self.assertEqual(result["CATEGORY_FALLBACK_MODELS"], ["m3"])
        self.assertNotIn("OPENROUTER_API_KEY", result)
        self.assertNotIn("CATEGORY_LLM_RETRY_ENABLED", result)
        self.assertNotIn("CATEGORY_LLM_MAX_RETRIES", result)

    def test_multiline_str_round_trip_from_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("VISION_FALLBACK_MODELS=z/z\n", encoding="utf-8")
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {"VISION_FALLBACK_MODELS": ["a/b", "c/d"]},
                env_path=env_path,
            )

            self.assertEqual(result["VISION_FALLBACK_MODELS"], ["a/b", "c/d"])
            self.assertEqual(settings.vision_fallback_models, ["a/b", "c/d"])
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("VISION_FALLBACK_MODELS=a/b,c/d", env_text)

    def test_multiline_str_round_trip_from_newline_string(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("", encoding="utf-8")
            settings = _fake_settings()

            update_runtime_config(
                settings,
                {"CATEGORY_FALLBACK_MODELS": "a/b\n\nc/d\n"},
                env_path=env_path,
            )

            self.assertEqual(settings.category_fallback_models, ["a/b", "c/d"])

    def test_deprecated_field_is_rejected(self):
        with self.assertRaises(ValueError):
            update_runtime_config(
                _fake_settings(),
                {"CATEGORY_LLM_RETRY_ENABLED": True},
            )

    def test_update_rejects_zero_request_timeout(self):
        with self.assertRaises(ValueError):
            update_runtime_config(_fake_settings(), {"REQUEST_TIMEOUT": 0})

    def test_showcase_model_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "OPENROUTER_API_KEY=secret\nSHOWCASE_MODEL=old-showcase\n",
                encoding="utf-8",
            )
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {"SHOWCASE_MODEL": "openai/gpt-image-1"},
                env_path=env_path,
            )

            self.assertEqual(result["SHOWCASE_MODEL"], "openai/gpt-image-1")
            self.assertEqual(settings.showcase_model, "openai/gpt-image-1")
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("SHOWCASE_MODEL=openai/gpt-image-1", env_text)
            self.assertIn("OPENROUTER_API_KEY=secret", env_text)

    def test_product_data_model_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text(
                "OPENROUTER_API_KEY=secret\nPRODUCT_DATA_MODEL=old-product\n",
                encoding="utf-8",
            )
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {"PRODUCT_DATA_MODEL": "google/gemini-2.5-flash"},
                env_path=env_path,
            )

            self.assertEqual(result["PRODUCT_DATA_MODEL"], "google/gemini-2.5-flash")
            self.assertEqual(settings.product_data_model, "google/gemini-2.5-flash")
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("PRODUCT_DATA_MODEL=google/gemini-2.5-flash", env_text)
            self.assertIn("OPENROUTER_API_KEY=secret", env_text)

    def test_get_public_config_includes_showcase_model(self):
        settings = _fake_settings(showcase_model="custom/showcase")
        result = get_public_config(settings)
        self.assertEqual(result["SHOWCASE_MODEL"], "custom/showcase")

    def test_get_public_config_includes_product_data_model(self):
        settings = _fake_settings(product_data_model="custom/product-data")
        result = get_public_config(settings)
        self.assertEqual(result["PRODUCT_DATA_MODEL"], "custom/product-data")

    def test_product_data_fallback_round_trip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env_path = Path(tmpdir) / ".env"
            env_path.write_text("", encoding="utf-8")
            settings = _fake_settings()

            result = update_runtime_config(
                settings,
                {
                    "PRODUCT_DATA_FALLBACK_MODEL": "openai/gpt-4o-mini",
                    "PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS": 7.5,
                },
                env_path=env_path,
            )

            self.assertEqual(result["PRODUCT_DATA_FALLBACK_MODEL"], "openai/gpt-4o-mini")
            self.assertEqual(result["PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS"], 7.5)
            self.assertEqual(settings.product_data_fallback_model, "openai/gpt-4o-mini")
            self.assertEqual(settings.product_data_fallback_timeout_seconds, 7.5)
            env_text = env_path.read_text(encoding="utf-8")
            self.assertIn("PRODUCT_DATA_FALLBACK_MODEL=openai/gpt-4o-mini", env_text)
            self.assertIn("PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS=7.5", env_text)

    def test_product_data_fallback_timeout_must_be_positive(self):
        with self.assertRaises(ValueError):
            update_runtime_config(
                _fake_settings(),
                {"PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS": 0},
            )

    def test_get_public_config_includes_fallback_settings(self):
        settings = _fake_settings(
            product_data_fallback_model="custom/mini",
            product_data_fallback_timeout_seconds=15.0,
        )
        result = get_public_config(settings)
        self.assertEqual(result["PRODUCT_DATA_FALLBACK_MODEL"], "custom/mini")
        self.assertEqual(result["PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS"], 15.0)

    def test_multiline_str_value_must_be_list_or_string(self):
        with self.assertRaises(ValueError):
            update_runtime_config(
                _fake_settings(),
                {"VISION_FALLBACK_MODELS": 123},
            )


if __name__ == "__main__":
    unittest.main()
