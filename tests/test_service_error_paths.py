import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.errors import LLMAllAttemptsFailedError, LLMRequestError
from app.service import MercariAnalyzer


class FakeBrandStore:
    def match(self, brand_name):
        return None


def _build_analyzer(vision_client, category_client, *, category_candidates=None):
    settings = SimpleNamespace(
        vision_model="m1",
        category_model="m1",
        log_llm_raw=False,
        vision_fallback_models=["fb1"],
        category_fallback_models=["fb1"],
        model_call_max_retries=0,
        model_call_total_budget_seconds=10,
        request_timeout=10,
    )
    category_store = MagicMock()
    category_store.get_categories_by_group.return_value = category_candidates or []
    return MercariAnalyzer(
        settings=settings,
        brand_store=FakeBrandStore(),
        category_store=category_store,
        vision_client=vision_client,
        category_client=category_client,
    )


class ServiceErrorPathsTest(unittest.TestCase):
    @patch("app.llm.resilient.time.sleep")
    def test_vision_full_failure_raises(self, _sleep):
        vision_client = MagicMock()
        vision_client.chat.side_effect = LLMRequestError("OpenRouter returned 503: x")
        category_client = MagicMock()
        analyzer = _build_analyzer(vision_client, category_client)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            analyzer.analyze(
                images=[(b"\x89PNG\r\n\x1a\n", "image/png")],
                language="en",
            )
        self.assertEqual(ctx.exception.stage, "vision")
        # primary (max_retries=0 → 1 attempt) + 1 fallback = 2 attempts
        self.assertEqual(len(ctx.exception.attempts), 2)

    @patch("app.llm.resilient.time.sleep")
    def test_vision_ok_category_full_failure_raises(self, _sleep):
        ai_raw_payload = (
            '{"title":"x","description":"d","top_level_category":"花・ガーデン・DIY",'
            '"brand_name":"","tax_excluded":null,"tax_included":null,"prices":[]}'
        )
        vision_client = MagicMock()
        vision_client.chat.return_value = (
            ai_raw_payload,
            {"choices": [{"message": {"content": ai_raw_payload}}]},
        )
        category_client = MagicMock()
        category_client.chat.side_effect = LLMRequestError("OpenRouter returned 503: x")
        analyzer = _build_analyzer(
            vision_client,
            category_client,
            category_candidates=[{"name": "花・ガーデン・DIY/foo", "id": "1"}],
        )

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            analyzer.analyze(
                images=[(b"\x89PNG\r\n\x1a\n", "image/png")],
                language="en",
            )
        self.assertEqual(ctx.exception.stage, "category")


if __name__ == "__main__":
    unittest.main()
