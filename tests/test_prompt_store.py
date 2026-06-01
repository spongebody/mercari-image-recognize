import json
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
