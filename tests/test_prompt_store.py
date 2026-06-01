import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.llm import prompt_store
from app.llm.prompts import TOP_LEVEL_CATEGORY_OPTIONS

GOLDEN = json.loads(
    (Path(__file__).parent / "data" / "prompt_category_golden.json").read_text(encoding="utf-8")
)


class PromptStoreReadTest(unittest.TestCase):
    def setUp(self):
        prompt_store._overrides = {}

    def tearDown(self):
        prompt_store._overrides = {}

    def test_get_returns_default_when_no_override(self):
        from app.llm.prompts import PRODUCT_DATA_SYSTEM_PROMPT
        self.assertEqual(
            prompt_store.get("PRODUCT_DATA_SYSTEM_PROMPT"), PRODUCT_DATA_SYSTEM_PROMPT
        )

    def test_get_returns_override_when_set(self):
        prompt_store._overrides["PRODUCT_DATA_SYSTEM_PROMPT"] = "custom text"
        self.assertEqual(prompt_store.get("PRODUCT_DATA_SYSTEM_PROMPT"), "custom text")

    def test_get_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            prompt_store.get("NOPE")

    def test_render_system_replaces_category_token(self):
        rendered = prompt_store.render_system("FAST_CLASSIFICATION_SYSTEM_PROMPT")
        self.assertNotIn("[[TOP_LEVEL_CATEGORY_OPTIONS]]", rendered)
        self.assertIn(TOP_LEVEL_CATEGORY_OPTIONS, rendered)

    def test_render_system_no_op_without_token(self):
        from app.llm.prompts import PRICE_ONLY_SYSTEM_PROMPT
        self.assertEqual(
            prompt_store.render_system("PRICE_ONLY_SYSTEM_PROMPT"), PRICE_ONLY_SYSTEM_PROMPT
        )

    def test_category_prompts_render_byte_identical_to_golden(self):
        for key in (
            "FAST_CLASSIFICATION_SYSTEM_PROMPT",
            "TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT",
            "PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT",
        ):
            self.assertEqual(prompt_store.render_system(key), GOLDEN[key])

    def test_list_prompts_has_sixteen_entries(self):
        prompts = prompt_store.list_prompts()
        self.assertEqual(len(prompts), 16)
        keys = {p["key"] for p in prompts}
        self.assertIn("CATEGORY_USER_PROMPT_TEMPLATE", keys)
        self.assertFalse(any(p["is_overridden"] for p in prompts))


class PromptStoreWriteTest(unittest.TestCase):
    def setUp(self):
        prompt_store._overrides = {}
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "prompt_overrides.json"
        self._patch = patch.object(prompt_store, "OVERRIDES_PATH", self._path)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()
        prompt_store._overrides = {}

    def test_update_persists_and_applies(self):
        result = prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "new system text"})
        self.assertEqual(prompt_store.get("PRODUCT_DATA_SYSTEM_PROMPT"), "new system text")
        saved = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(saved["PRODUCT_DATA_SYSTEM_PROMPT"], "new system text")
        entry = next(p for p in result if p["key"] == "PRODUCT_DATA_SYSTEM_PROMPT")
        self.assertTrue(entry["is_overridden"])

    def test_update_rejects_unknown_key(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"NOPE": "x"})

    def test_update_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "   "})

    def test_update_rejects_missing_category_token(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"FAST_CLASSIFICATION_SYSTEM_PROMPT": "no token here"})

    def test_update_rejects_user_prompt_missing_required_placeholder(self):
        with self.assertRaises(ValueError):
            prompt_store.update({"PRODUCT_DATA_USER_PROMPT": "no placeholder"})

    def test_update_rejects_user_prompt_extra_placeholder(self):
        with self.assertRaises(ValueError):
            prompt_store.update(
                {"PRODUCT_DATA_USER_PROMPT": "lang {language_label} extra {oops}"}
            )

    def test_update_rejects_anonymous_positional_field(self):
        with self.assertRaises(ValueError):
            prompt_store.update(
                {"PRODUCT_DATA_USER_PROMPT": "Lang {language_label} {}"}
            )

    def test_update_accepts_valid_user_prompt(self):
        prompt_store.update({"PRODUCT_DATA_USER_PROMPT": "Lang: {language_label}. Go."})
        self.assertEqual(
            prompt_store.get("PRODUCT_DATA_USER_PROMPT"), "Lang: {language_label}. Go."
        )

    def test_update_is_atomic_on_validation_failure(self):
        with self.assertRaises(ValueError):
            prompt_store.update(
                {"PRODUCT_DATA_SYSTEM_PROMPT": "valid", "NOPE": "bad"}
            )
        self.assertFalse(prompt_store.is_overridden("PRODUCT_DATA_SYSTEM_PROMPT"))

    def test_reset_rejects_non_list_keys(self):
        with self.assertRaises(ValueError):
            prompt_store.reset("PRODUCT_DATA_SYSTEM_PROMPT")

    def test_reset_specific_key(self):
        prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "x"})
        prompt_store.reset(["PRODUCT_DATA_SYSTEM_PROMPT"])
        self.assertFalse(prompt_store.is_overridden("PRODUCT_DATA_SYSTEM_PROMPT"))

    def test_reset_all(self):
        prompt_store.update({"PRODUCT_DATA_SYSTEM_PROMPT": "x"})
        prompt_store.reset(None)
        self.assertEqual(prompt_store._overrides, {})

    def test_render_system_replaces_token_in_override(self):
        prompt_store.update(
            {"CATEGORY_SYSTEM_PROMPT": "Custom rules.\n[[TOP_LEVEL_CATEGORY_OPTIONS]]\nEnd."}
        )
        rendered = prompt_store.render_system("CATEGORY_SYSTEM_PROMPT")
        self.assertNotIn("[[TOP_LEVEL_CATEGORY_OPTIONS]]", rendered)
        from app.llm.prompts import TOP_LEVEL_CATEGORY_OPTIONS
        self.assertIn(TOP_LEVEL_CATEGORY_OPTIONS, rendered)

    def test_load_overrides_tolerates_missing_file(self):
        prompt_store.load_overrides()
        self.assertEqual(prompt_store._overrides, {})

    def test_load_overrides_tolerates_corrupt_file(self):
        self._path.write_text("{not json", encoding="utf-8")
        prompt_store.load_overrides()
        self.assertEqual(prompt_store._overrides, {})


if __name__ == "__main__":
    unittest.main()
