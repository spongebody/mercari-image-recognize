import base64
import json
import unittest

import requests

from app.showcase.openrouter_image_client import (
    OpenRouterImageClient,
    OpenRouterImageClientError,
    OpenRouterImageResponseError,
    extract_image_payload,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _success_payload(b64: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "images": [
                        {"image_url": {"url": f"data:image/png;base64,{b64}"}}
                    ]
                }
            }
        ]
    }


class ExtractImagePayloadTest(unittest.TestCase):
    def test_reads_data_url(self):
        encoded = base64.b64encode(b"fake-png-bytes").decode("utf-8")
        parsed = extract_image_payload(_success_payload(encoded))
        self.assertEqual(parsed.mime_type, "image/png")
        self.assertEqual(parsed.base64_data, encoded)

    def test_falls_back_to_b64_json_field(self):
        encoded = base64.b64encode(b"abc").decode("utf-8")
        payload = {
            "choices": [
                {
                    "message": {
                        "images": [{"b64_json": encoded}]
                    }
                }
            ]
        }
        parsed = extract_image_payload(payload)
        self.assertEqual(parsed.mime_type, "image/png")
        self.assertEqual(parsed.base64_data, encoded)

    def test_raises_when_no_image_present(self):
        payload = {"choices": [{"message": {"content": "no image here"}}]}
        with self.assertRaises(OpenRouterImageResponseError):
            extract_image_payload(payload)


class OpenRouterImageClientTest(unittest.TestCase):
    def _build(self, *, max_retries: int = 3) -> OpenRouterImageClient:
        client = OpenRouterImageClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            model="google/gemini-3.1-flash-image-preview",
            timeout=10,
            max_retries=max_retries,
        )
        # Avoid real sleeps during tests.
        client._sleep = lambda _delay: None
        return client

    def test_returns_payload_on_success(self):
        encoded = base64.b64encode(b"ok").decode("utf-8")
        client = self._build()
        calls: list[dict] = []

        def fake_post(url, headers, data, timeout):
            calls.append({"url": url, "headers": headers, "payload": json.loads(data)})
            return _FakeResponse(200, _success_payload(encoded))

        client.session.post = fake_post

        result = client.generate_image(
            prompt="hello",
            image_bytes=b"binary",
            content_type="image/jpeg",
            request_id="req-1",
        )

        self.assertEqual(result.image.base64_data, encoded)
        self.assertEqual(result.upstream_status_code, 200)
        self.assertEqual(result.attempts, 1)
        self.assertEqual(len(calls), 1)
        sent = calls[0]["payload"]
        self.assertEqual(sent["model"], "google/gemini-3.1-flash-image-preview")
        self.assertEqual(sent["modalities"], ["image", "text"])
        self.assertEqual(sent["messages"][0]["content"][0]["text"], "hello")
        self.assertTrue(
            sent["messages"][0]["content"][1]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"
            )
        )

    def test_per_call_model_overrides_default(self):
        encoded = base64.b64encode(b"ok").decode("utf-8")
        client = self._build()
        calls: list[dict] = []

        def fake_post(url, headers, data, timeout):
            calls.append({"payload": json.loads(data)})
            return _FakeResponse(200, _success_payload(encoded))

        client.session.post = fake_post
        client.generate_image(
            prompt="hi",
            image_bytes=b"x",
            content_type="image/png",
            request_id="req-1",
            model="openai/gpt-image-1",
        )
        self.assertEqual(calls[0]["payload"]["model"], "openai/gpt-image-1")

    def test_per_call_model_can_recover_from_unconfigured_default(self):
        encoded = base64.b64encode(b"ok").decode("utf-8")
        client = OpenRouterImageClient(
            api_key="key",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            model="",
            timeout=10,
            max_retries=1,
        )
        client._sleep = lambda _delay: None
        calls: list[dict] = []

        def fake_post(url, headers, data, timeout):
            calls.append({"payload": json.loads(data)})
            return _FakeResponse(200, _success_payload(encoded))

        client.session.post = fake_post
        client.generate_image(
            prompt="hi",
            image_bytes=b"x",
            content_type="image/png",
            request_id="req-1",
            model="openai/gpt-image-1",
        )
        self.assertEqual(calls[0]["payload"]["model"], "openai/gpt-image-1")

    def test_retries_then_succeeds_after_503(self):
        encoded = base64.b64encode(b"ok").decode("utf-8")
        client = self._build(max_retries=3)
        responses = [
            _FakeResponse(503, {}, text="upstream"),
            _FakeResponse(200, _success_payload(encoded)),
        ]
        idx = {"i": 0}

        def fake_post(url, headers, data, timeout):
            response = responses[idx["i"]]
            idx["i"] += 1
            return response

        client.session.post = fake_post

        result = client.generate_image(
            prompt="hello",
            image_bytes=b"x",
            content_type="image/png",
            request_id="req-1",
        )
        self.assertEqual(result.attempts, 2)

    def test_raises_after_max_retries_on_5xx(self):
        client = self._build(max_retries=2)

        def fake_post(url, headers, data, timeout):
            return _FakeResponse(503, {}, text="busy")

        client.session.post = fake_post

        with self.assertRaises(OpenRouterImageClientError) as ctx:
            client.generate_image(
                prompt="hello",
                image_bytes=b"x",
                content_type="image/png",
                request_id="req-1",
            )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_raises_immediately_on_401(self):
        client = self._build(max_retries=3)
        call_count = {"n": 0}

        def fake_post(url, headers, data, timeout):
            call_count["n"] += 1
            return _FakeResponse(401, {}, text="bad key")

        client.session.post = fake_post

        with self.assertRaises(OpenRouterImageClientError) as ctx:
            client.generate_image(
                prompt="hello",
                image_bytes=b"x",
                content_type="image/png",
                request_id="req-1",
            )
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(call_count["n"], 1)

    def test_treats_transport_error_as_retryable(self):
        encoded = base64.b64encode(b"ok").decode("utf-8")
        client = self._build(max_retries=3)
        results = [
            requests.ConnectionError("boom"),
            _FakeResponse(200, _success_payload(encoded)),
        ]
        idx = {"i": 0}

        def fake_post(url, headers, data, timeout):
            outcome = results[idx["i"]]
            idx["i"] += 1
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        client.session.post = fake_post
        result = client.generate_image(
            prompt="hi",
            image_bytes=b"x",
            content_type="image/png",
            request_id="req-1",
        )
        self.assertEqual(result.attempts, 2)

    def test_missing_api_key_raises_before_request(self):
        client = OpenRouterImageClient(
            api_key="",
            base_url="https://openrouter.ai/api/v1/chat/completions",
            model="m",
            timeout=10,
        )
        called = {"n": 0}

        def fake_post(*args, **kwargs):
            called["n"] += 1

        client.session.post = fake_post
        with self.assertRaises(OpenRouterImageClientError) as ctx:
            client.generate_image(
                prompt="hi",
                image_bytes=b"x",
                content_type="image/png",
                request_id="req-1",
            )
        self.assertEqual(ctx.exception.error_code, "missing_api_key")
        self.assertEqual(called["n"], 0)


if __name__ == "__main__":
    unittest.main()
