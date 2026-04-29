import unittest
from unittest.mock import MagicMock, patch

from app.errors import LLMAllAttemptsFailedError, LLMRequestError
from app.llm.resilient import ResilientCaller, AttemptRecord


def _make_caller(client, *, max_retries=3, total_budget_s=120,
                 per_attempt_timeout_s=60):
    return ResilientCaller(
        client=client,
        max_retries=max_retries,
        total_budget_s=total_budget_s,
        per_attempt_timeout_s=per_attempt_timeout_s,
    )


def _ok_response(payload='{"ok": true}'):
    return (payload, {"choices": [{"message": {"content": payload}}]})


class ResilientCallerTest(unittest.TestCase):
    def setUp(self):
        # Force time.monotonic to advance deterministically: each call adds 0.001s
        self._t = [1000.0]

        def fake_monotonic():
            self._t[0] += 0.001
            return self._t[0]

        self._mono_patcher = patch("app.llm.resilient.time.monotonic", side_effect=fake_monotonic)
        self._mono = self._mono_patcher.start()
        self._sleep_patcher = patch("app.llm.resilient.time.sleep")
        self._sleep = self._sleep_patcher.start()

    def tearDown(self):
        self._mono_patcher.stop()
        self._sleep_patcher.stop()

    def test_primary_first_call_succeeds(self):
        client = MagicMock()
        client.chat.return_value = _ok_response()
        caller = _make_caller(client)

        parsed, raw, attempts = caller.call_and_parse(
            stage="vision",
            primary_model="m1",
            fallback_models=["fb1"],
            messages=[],
            temperature=0.1,
            max_tokens=10,
        )

        self.assertEqual(parsed, {"ok": True})
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].error_kind, "ok")
        self.assertEqual(attempts[0].model, "m1")
        client.chat.assert_called_once()

    def test_primary_retries_then_succeeds(self):
        client = MagicMock()
        client.chat.side_effect = [
            LLMRequestError("OpenRouter returned 503: x"),
            _ok_response(),
        ]
        caller = _make_caller(client)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=[],
            messages=[], temperature=0.1, max_tokens=10,
        )

        self.assertEqual([a.error_kind for a in attempts], ["request_failed", "ok"])
        self.assertEqual(attempts[0].status_code, 503)
        self.assertEqual(self._sleep.call_count, 1)

    def test_primary_exhausted_fallback_succeeds(self):
        client = MagicMock()
        client.chat.side_effect = [
            LLMRequestError("e1"),
            LLMRequestError("e2"),
            LLMRequestError("e3"),
            LLMRequestError("e4"),  # primary 4th attempt (initial + 3 retries)
            _ok_response(),         # fb1 succeeds
        ]
        caller = _make_caller(client, max_retries=3)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=["fb1", "fb2"],
            messages=[], temperature=0.1, max_tokens=10,
        )

        models = [a.model for a in attempts]
        self.assertEqual(models, ["m1", "m1", "m1", "m1", "fb1"])
        self.assertEqual(attempts[-1].error_kind, "ok")

    def test_all_fail_raises(self):
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("nope")
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1", fallback_models=["fb1"],
                messages=[], temperature=0.1, max_tokens=10,
            )

        self.assertEqual(ctx.exception.stage, "vision")
        # 4 primary + 1 fallback = 5
        self.assertEqual(len(ctx.exception.attempts), 5)
        self.assertTrue(all(a.error_kind == "request_failed" for a in ctx.exception.attempts))

    def test_parse_failed_triggers_retry_and_fallback(self):
        client = MagicMock()
        # Three malformed responses on m1, then valid on fb1
        client.chat.side_effect = [
            ("not json", {}),
            ("still not", {}),
            ("```\nnope\n```", {}),
            ("```\nnope\n```", {}),
            _ok_response(),
        ]
        caller = _make_caller(client, max_retries=3)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=["fb1"],
            messages=[], temperature=0.1, max_tokens=10,
        )

        kinds = [a.error_kind for a in attempts]
        self.assertEqual(kinds[:4], ["parse_failed"] * 4)
        self.assertEqual(kinds[-1], "ok")
        self.assertEqual(attempts[0].status_code, 200)

    def test_non_dict_json_treated_as_parse_failed(self):
        client = MagicMock()
        # Bare array is valid JSON but not a dict
        client.chat.side_effect = [
            ("[1, 2, 3]", {}),
            _ok_response(),
        ]
        caller = _make_caller(client, max_retries=3)

        _, _, attempts = caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=[],
            messages=[], temperature=0.1, max_tokens=10,
        )
        self.assertEqual(attempts[0].error_kind, "parse_failed")
        self.assertEqual(attempts[1].error_kind, "ok")

    def test_budget_exhausted_before_attempt(self):
        # Make the deadline expire after the first attempt
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("e1")
        caller = _make_caller(client, max_retries=3, total_budget_s=0.1)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1", fallback_models=["fb1"],
                messages=[], temperature=0.1, max_tokens=10,
            )
        kinds = [a.error_kind for a in ctx.exception.attempts]
        self.assertIn("budget_exhausted", kinds)

    def test_fallback_dedups_primary(self):
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("nope")
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1",
                fallback_models=["m1", "fb1"],  # m1 should be removed
                messages=[], temperature=0.1, max_tokens=10,
            )
        models = [a.model for a in ctx.exception.attempts]
        self.assertEqual(models.count("m1"), 4)  # primary's 4 attempts only
        self.assertEqual(models.count("fb1"), 1)

    def test_empty_fallback_list(self):
        client = MagicMock()
        client.chat.side_effect = LLMRequestError("e")
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="m1", fallback_models=[],
                messages=[], temperature=0.1, max_tokens=10,
            )
        self.assertEqual(len(ctx.exception.attempts), 4)

    def test_empty_primary_raises_immediately(self):
        client = MagicMock()
        caller = _make_caller(client, max_retries=3)

        with self.assertRaises(LLMAllAttemptsFailedError) as ctx:
            caller.call_and_parse(
                stage="vision", primary_model="", fallback_models=["fb1"],
                messages=[], temperature=0.1, max_tokens=10,
            )
        self.assertEqual(ctx.exception.attempts, [])
        client.chat.assert_not_called()

    def test_no_sleep_between_model_switch(self):
        client = MagicMock()
        client.chat.side_effect = [
            LLMRequestError("e1"),
            LLMRequestError("e2"),
            LLMRequestError("e3"),
            LLMRequestError("e4"),  # primary exhausted
            _ok_response(),         # fb1
        ]
        caller = _make_caller(client, max_retries=3)
        caller.call_and_parse(
            stage="vision", primary_model="m1", fallback_models=["fb1"],
            messages=[], temperature=0.1, max_tokens=10,
        )
        # 3 sleeps between primary's 4 attempts; none before the fallback
        self.assertEqual(self._sleep.call_count, 3)


if __name__ == "__main__":
    unittest.main()
