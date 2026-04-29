import unittest

from app.showcase.prompt import build_showcase_prompt


class ShowcasePromptTest(unittest.TestCase):
    def test_includes_core_constraints(self):
        prompt = build_showcase_prompt(None).lower()
        self.assertIn("do not restyle", prompt)
        self.assertIn("candid lifestyle moment", prompt)
        self.assertIn("moment, not a pose", prompt)
        self.assertIn("natural interaction with the product", prompt)
        self.assertIn("single commercial photograph", prompt)

    def test_appends_prompt_hint(self):
        prompt = build_showcase_prompt("luxury studio handbag")
        self.assertIn("luxury studio handbag", prompt)


if __name__ == "__main__":
    unittest.main()
