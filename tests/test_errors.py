import unittest

from app.errors import LLMAllAttemptsFailedError
from app.llm.resilient import AttemptRecord


class LLMAllAttemptsFailedErrorTest(unittest.TestCase):
    def test_summary_includes_stage_and_count(self):
        attempts = [
            AttemptRecord(model="m1", attempt=1, attempt_global=1,
                          error_kind="request_failed", message="boom",
                          latency_ms=12.0, status_code=503),
            AttemptRecord(model="m1", attempt=2, attempt_global=2,
                          error_kind="parse_failed", message="bad json",
                          latency_ms=8.0, status_code=200),
        ]
        exc = LLMAllAttemptsFailedError(stage="vision", attempts=attempts)
        self.assertEqual(exc.stage, "vision")
        self.assertEqual(exc.attempts, attempts)
        self.assertIn("vision", str(exc))
        self.assertIn("2", str(exc))

    def test_empty_attempts_still_summarised(self):
        exc = LLMAllAttemptsFailedError(stage="category", attempts=[])
        self.assertEqual(exc.attempts, [])
        self.assertIn("category", str(exc))


if __name__ == "__main__":
    unittest.main()
