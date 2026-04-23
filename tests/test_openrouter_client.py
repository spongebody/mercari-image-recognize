import json
import os
import unittest
from unittest.mock import patch

from app.config import Settings
from app.llm.client import OpenRouterClient


class _FakeResponse:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class OpenRouterClientReasoningTest(unittest.TestCase):
    def test_settings_builds_reasoning_config_from_env(self):
        with patch.dict(
            os.environ,
            {
                "REASONING_ENABLED": "true",
                "REASONING_EFFORT": "high",
                "REASONING_MAX_TOKENS": "2048",
                "REASONING_SUMMARY": "detailed",
            },
            clear=False,
        ):
            settings = Settings()

        self.assertEqual(
            settings.reasoning,
            {
                "enabled": True,
                "effort": "high",
                "max_tokens": 2048,
                "summary": "detailed",
            },
        )

    def test_settings_omits_invalid_or_empty_reasoning_values(self):
        with patch.dict(
            os.environ,
            {
                "REASONING_ENABLED": "",
                "REASONING_EFFORT": "extreme",
                "REASONING_MAX_TOKENS": "abc",
                "REASONING_SUMMARY": "",
            },
            clear=False,
        ):
            settings = Settings()

        self.assertIsNone(settings.reasoning)

    def test_chat_includes_reasoning_payload_when_configured(self):
        client = OpenRouterClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            timeout=30,
            reasoning={"enabled": True, "effort": "medium", "summary": "auto"},
        )
        captured = {}

        def fake_post(url, headers, data, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = json.loads(data)
            captured["timeout"] = timeout
            return _FakeResponse(
                {"choices": [{"message": {"content": "ok", "reasoning_details": []}}]}
            )

        client.session.post = fake_post

        content, raw = client.chat(
            model="google/gemini-3.1-pro-preview",
            messages=[{"role": "user", "content": "hello"}],
            max_tokens=1234,
        )

        self.assertEqual(content, "ok")
        self.assertIn("choices", raw)
        self.assertEqual(
            captured["payload"]["reasoning"],
            {"enabled": True, "effort": "medium", "summary": "auto"},
        )

    def test_chat_omits_reasoning_when_not_configured(self):
        client = OpenRouterClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            timeout=30,
        )
        captured = {}

        def fake_post(url, headers, data, timeout):
            captured["payload"] = json.loads(data)
            return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        client.session.post = fake_post
        client.chat(
            model="google/gemini-3.1-pro-preview",
            messages=[{"role": "user", "content": "hello"}],
        )

        self.assertNotIn("reasoning", captured["payload"])


if __name__ == "__main__":
    unittest.main()
