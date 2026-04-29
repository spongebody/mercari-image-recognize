import unittest

from app.errors import LLMParseError
from app.llm.json_parser import parse_llm_json


class ParseLLMJsonTest(unittest.TestCase):
    def test_plain_json_object(self):
        self.assertEqual(parse_llm_json('{"a": 1}'), {"a": 1})

    def test_json_object_in_json_fence(self):
        raw = "Here you go:\n```json\n{\"a\": 1, \"b\": [2, 3]}\n```"
        self.assertEqual(parse_llm_json(raw), {"a": 1, "b": [2, 3]})

    def test_json_object_in_unlabeled_fence(self):
        raw = "```\n{\"x\": \"y\"}\n```"
        self.assertEqual(parse_llm_json(raw), {"x": "y"})

    def test_json_object_with_surrounding_prose(self):
        raw = "Sure! The answer is { \"answer\": 42 } - hope it helps."
        self.assertEqual(parse_llm_json(raw), {"answer": 42})

    def test_bare_array(self):
        self.assertEqual(parse_llm_json("[1, 2, 3]"), [1, 2, 3])

    def test_unicode_preserved(self):
        self.assertEqual(parse_llm_json('{"name": "東京"}'), {"name": "東京"})

    def test_empty_string_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json("")

    def test_whitespace_only_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json("   \n\t  ")

    def test_pure_prose_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json("I'm sorry, I can't help with that.")

    def test_trailing_comma_raises(self):
        with self.assertRaises(LLMParseError):
            parse_llm_json('{"a": 1,}')

    def test_excerpt_in_message_capped_at_200_chars(self):
        long_blob = "noise " * 200
        with self.assertRaises(LLMParseError) as ctx:
            parse_llm_json(long_blob)
        msg = str(ctx.exception)
        self.assertIn("excerpt=", msg)
        # 200 chars + ellipsis somewhere in the message
        self.assertIn("…", msg)


if __name__ == "__main__":
    unittest.main()
