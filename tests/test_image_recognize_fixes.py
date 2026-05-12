"""Regression tests for the three image-recognition pipeline fixes:

1. Fallback timer starts AFTER classification finishes (not at request entry).
2. Product-data fallback path uses the dedicated fallback prompt.
3. Product-data fallback model gets its own retry + multi-model fallback chain.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import main
from app.errors import LLMRequestError
from app.llm.prompts import (
    PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT,
    PRODUCT_DATA_SYSTEM_PROMPT,
)
from app.service import MercariAnalyzer


class _SequenceClient:
    """Minimal LLM client double that returns canned responses or raises."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return json.dumps(outcome), {"choices": [{"message": {"content": json.dumps(outcome)}}]}


class _FakeBrandStore:
    def match(self, brand_name):  # noqa: D401 - test stub
        return None


class _FakeCategoryStore:
    def get_categories_by_group(self, group_name):  # noqa: D401 - test stub
        return []

    def find_category(self, group_name, category_name):  # noqa: D401 - test stub
        return None


def _build_analyzer(
    *,
    vision_outcomes,
    fallback_models=(),
    primary_fallback_models=(),
):
    settings = SimpleNamespace(
        vision_model="vision-test",
        category_model="category-test",
        product_data_model="primary-product-data",
        product_data_fallback_model="explicit-fallback-model",
        product_data_fallback_models=list(fallback_models),
        log_llm_raw=False,
        vision_fallback_models=list(primary_fallback_models),
        category_fallback_models=[],
        model_call_max_retries=0,  # no retries inside ResilientCaller, just go to next model
        model_call_total_budget_seconds=10,
        request_timeout=10,
    )
    vision_client = _SequenceClient(vision_outcomes)
    category_client = _SequenceClient([])
    analyzer = MercariAnalyzer(
        settings=settings,
        brand_store=_FakeBrandStore(),
        category_store=_FakeCategoryStore(),
        vision_client=vision_client,
        category_client=category_client,
    )
    return analyzer, vision_client


def _good_payload(title="X"):
    return {
        "title": title,
        "description": {
            "product_details": {
                "brand": "",
                "product_name": "",
                "model_number": "",
                "target": "",
                "color": "",
                "size": "",
                "weight": "",
                "condition": "",
            },
            "product_intro": "",
            "recommendation": "",
            "search_keywords": [],
        },
        "brand_name": "",
    }


class FallbackTimerStartsAtPrimarySubmitTest(unittest.TestCase):
    """started_at must reflect the primary future's submit moment.

    This is the threshold baseline for fallback selection. Aligning it with
    submit time means the configured PRODUCT_DATA_FALLBACK_TIMEOUT_SECONDS maps
    to the actual wall time the primary model spent generating, so the user-
    observable product_data_ms can be compared against the threshold directly.
    """

    def setUp(self):
        self.client = TestClient(main.app)
        self._original_fallback_model = main.settings.product_data_fallback_model
        self._original_fallback_timeout = main.settings.product_data_fallback_timeout_seconds
        main.settings.product_data_fallback_model = "openai/gpt-4o-mini"
        main.settings.product_data_fallback_timeout_seconds = 5.0

    def tearDown(self):
        main.settings.product_data_fallback_model = self._original_fallback_model
        main.settings.product_data_fallback_timeout_seconds = self._original_fallback_timeout

    @patch.object(main, "product_data_executor")
    @patch.object(main, "analyzer")
    def test_started_at_is_captured_before_classification(self, analyzer, executor):
        primary_future = concurrent.futures.Future()
        fallback_future = concurrent.futures.Future()
        executor.submit.side_effect = [primary_future, fallback_future]

        classify_payload = {
            "status": "product_pending",
            "categories": [],
            "timings": {"total_ms": 0.0, "classification_ms": 0.0},
        }

        def fake_classify(**_kwargs):
            time.sleep(0.05)
            return classify_payload

        analyzer.classify_first_image_categories.side_effect = fake_classify

        with patch.object(main.analysis_job_store, "put", wraps=main.analysis_job_store.put) as put_spy:
            pre_request_time = time.monotonic()
            response = self.client.post(
                "/api/v1/mercari/image/analyze",
                files=[("image_list", ("front.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
                data={"language": "ja"},
            )
            self.assertEqual(response.status_code, 200)

            self.assertEqual(put_spy.call_count, 1)
            started_at = put_spy.call_args.kwargs["started_at"]
            # started_at must be captured BEFORE classify slept for 50ms.
            # Anything well under the 50ms classify duration confirms it sits
            # at submit time rather than after classification finishes.
            self.assertLess(started_at - pre_request_time, 0.04)


class FallbackPromptIsUsedTest(unittest.TestCase):
    """Issue #2: when use_fallback_prompt=True the fallback system prompt is sent."""

    def test_fallback_prompt_used_when_flag_set(self):
        analyzer, vision_client = _build_analyzer(
            vision_outcomes=[_good_payload("primary-result")]
        )

        analyzer.generate_product_data(
            images=[(b"front", "image/png")],
            language="ja",
            model_override="explicit-fallback-model",
            use_fallback_prompt=True,
        )

        sent_system = vision_client.calls[0]["messages"][0]["content"]
        self.assertEqual(sent_system, PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT)
        self.assertNotEqual(sent_system, PRODUCT_DATA_SYSTEM_PROMPT)
        # The fallback user prompt should explicitly require multi-paragraph
        # product_intro and search_keywords without leading "#".
        sent_user = vision_client.calls[0]["messages"][1]["content"][0]["text"]
        self.assertIn("3–5 paragraph", sent_user)
        self.assertIn("search_keywords", sent_user)

    def test_primary_prompt_used_by_default(self):
        analyzer, vision_client = _build_analyzer(
            vision_outcomes=[_good_payload("primary-result")]
        )

        analyzer.generate_product_data(
            images=[(b"front", "image/png")],
            language="ja",
        )

        sent_system = vision_client.calls[0]["messages"][0]["content"]
        self.assertEqual(sent_system, PRODUCT_DATA_SYSTEM_PROMPT)


class ProductDataFallbackChainTest(unittest.TestCase):
    """Issue #3: explicit fallback model gets its own retry/fallback chain."""

    def test_fallback_model_falls_through_to_chain_on_failure(self):
        """The explicit fallback model also has further fallback models."""
        analyzer, vision_client = _build_analyzer(
            vision_outcomes=[
                LLMRequestError("OpenRouter returned 503: explicit fallback down"),
                _good_payload("ok-from-chain"),
            ],
            fallback_models=("chain-model-a", "chain-model-b"),
        )

        result = analyzer.generate_product_data(
            images=[(b"front", "image/png")],
            language="ja",
            model_override="explicit-fallback-model",
            use_fallback_prompt=True,
        )

        models_called = [call["model"] for call in vision_client.calls]
        self.assertEqual(models_called[0], "explicit-fallback-model")
        self.assertEqual(models_called[1], "chain-model-a")
        self.assertTrue(result["title"].startswith("ok-from-chain"))
        self.assertGreaterEqual(len(result["title"]), 80)

    def test_primary_chain_includes_explicit_fallback_first(self):
        """Primary product-data path should fall back to the explicit fallback model first.

        Even though primary and fallback futures are dispatched in parallel
        from main.py, the in-process chain inside _call_product_data_llm
        should still exhaust the configured fallback list before failing.
        """
        analyzer, vision_client = _build_analyzer(
            vision_outcomes=[
                LLMRequestError("primary down"),
                _good_payload("ok-from-explicit-fb"),
            ],
            fallback_models=("chain-only",),
        )

        result = analyzer.generate_product_data(
            images=[(b"front", "image/png")],
            language="ja",
        )

        models_called = [call["model"] for call in vision_client.calls]
        self.assertEqual(models_called[0], "primary-product-data")
        self.assertEqual(models_called[1], "explicit-fallback-model")
        self.assertTrue(result["title"].startswith("ok-from-explicit-fb"))
        self.assertGreaterEqual(len(result["title"]), 80)


if __name__ == "__main__":
    unittest.main()
