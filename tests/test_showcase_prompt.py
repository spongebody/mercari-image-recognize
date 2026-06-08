import unittest

from app.llm import prompt_store
from app.showcase.prompt import build_showcase_prompt


class ShowcasePromptTest(unittest.TestCase):
    def setUp(self):
        prompt_store._overrides = {}

    def tearDown(self):
        prompt_store._overrides = {}

    def test_includes_core_constraints(self):
        prompt = build_showcase_prompt(None).lower()
        self.assertIn("do not restyle", prompt)
        self.assertIn("candid lifestyle moment", prompt)
        self.assertIn("moment, not a pose", prompt)
        self.assertIn("natural interaction with the product", prompt)
        self.assertIn("single commercial photograph", prompt)

    def test_requires_full_figure_and_square_framing(self):
        prompt = build_showcase_prompt(None).lower()
        self.assertIn("never crop", prompt)
        self.assertIn("1:1", prompt)

    def test_appends_prompt_hint(self):
        prompt = build_showcase_prompt("luxury studio handbag")
        self.assertIn("luxury studio handbag", prompt)

    def test_uses_prompt_store_override(self):
        prompt_store._overrides["SHOWCASE_PROMPT"] = "CUSTOM SHOWCASE INSTRUCTION"
        self.assertEqual(
            build_showcase_prompt(None), "CUSTOM SHOWCASE INSTRUCTION"
        )

    def test_override_still_appends_hint(self):
        prompt_store._overrides["SHOWCASE_PROMPT"] = "BASE"
        prompt = build_showcase_prompt("extra note")
        self.assertIn("BASE", prompt)
        self.assertIn("extra note", prompt)


if __name__ == "__main__":
    unittest.main()
