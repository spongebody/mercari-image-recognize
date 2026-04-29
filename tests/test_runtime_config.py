import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from app.runtime_config import get_public_config, update_runtime_config


def _fake_settings(**overrides):
    base = dict(
        vision_model="old-vision",
        category_model="old-category",
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

    def test_multiline_str_value_must_be_list_or_string(self):
        with self.assertRaises(ValueError):
            update_runtime_config(
                _fake_settings(),
                {"VISION_FALLBACK_MODELS": 123},
            )


if __name__ == "__main__":
    unittest.main()
