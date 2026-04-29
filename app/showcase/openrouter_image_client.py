import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import requests


_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class OpenRouterImageClientError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        error_code: str = "upstream_generation_failed",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class OpenRouterImageRetryableError(OpenRouterImageClientError):
    pass


class OpenRouterImageResponseError(OpenRouterImageRetryableError):
    """Raised when the response payload is structurally invalid (no usable image)."""


@dataclass
class ImagePayload:
    mime_type: str
    base64_data: str


@dataclass
class OpenRouterImageResult:
    image: ImagePayload
    upstream_status_code: int
    response_body: Dict[str, Any]
    attempts: int


def extract_image_payload(payload: Dict[str, Any]) -> ImagePayload:
    choices = payload.get("choices") or []
    if not choices:
        raise OpenRouterImageResponseError(
            "OpenRouter response contained no usable image payload.",
            status_code=200,
        )

    message = choices[0].get("message") or {}
    images = message.get("images") or []
    for image in images:
        image_url = image.get("image_url") or image.get("imageUrl") or {}
        url = image_url.get("url")
        if isinstance(url, str) and url.startswith("data:image/") and ";base64," in url:
            header, encoded = url.split(",", 1)
            mime_type = header[len("data:") : header.index(";base64")]
            return ImagePayload(mime_type=mime_type, base64_data=encoded)
        if isinstance(url, str) and url:
            return ImagePayload(mime_type="image/png", base64_data=url)

        raw_b64 = image.get("b64_json") or image.get("base64")
        if isinstance(raw_b64, str) and raw_b64:
            return ImagePayload(mime_type="image/png", base64_data=raw_b64)

    raise OpenRouterImageResponseError(
        "OpenRouter response contained no usable image payload.",
        status_code=200,
    )


class OpenRouterImageClient:
    """Synchronous OpenRouter image-generation client with capped retry/backoff."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: int,
        max_retries: int = 3,
        referer: str = "",
        app_name: str = "",
        backoff_initial_s: float = 1.0,
        backoff_cap_s: float = 8.0,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.referer = referer
        self.app_name = app_name
        self.backoff_initial_s = backoff_initial_s
        self.backoff_cap_s = backoff_cap_s
        self.session = requests.Session()
        # Hook used by tests to skip real sleeps without monkey-patching time.
        self._sleep = time.sleep

    def generate_image(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        content_type: str,
        request_id: str,
        model: Optional[str] = None,
    ) -> OpenRouterImageResult:
        if not self.api_key:
            raise OpenRouterImageClientError(
                "OPENROUTER_API_KEY is not configured.",
                error_code="missing_api_key",
            )
        effective_model = (model or self.model or "").strip()
        if not effective_model:
            raise OpenRouterImageClientError(
                "Showcase model is not configured.",
                error_code="missing_model",
            )

        payload = self._build_payload(
            prompt=prompt,
            image_bytes=image_bytes,
            content_type=content_type,
            model=effective_model,
        )
        last_error: Optional[OpenRouterImageClientError] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = self._post(payload)
            except OpenRouterImageRetryableError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise
            except OpenRouterImageClientError:
                raise

            try:
                response_body = response.json()
            except ValueError as exc:
                last_error = OpenRouterImageRetryableError(
                    f"Failed to decode OpenRouter response JSON: {exc}",
                    status_code=response.status_code,
                )
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise last_error

            try:
                image = extract_image_payload(response_body)
            except OpenRouterImageRetryableError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    self._sleep_backoff(attempt)
                    continue
                raise

            return OpenRouterImageResult(
                image=image,
                upstream_status_code=response.status_code,
                response_body=response_body,
                attempts=attempt,
            )

        if last_error is not None:
            raise last_error
        raise OpenRouterImageClientError(
            f"OpenRouter request failed for {request_id}.",
            error_code="upstream_generation_failed",
        )

    def _post(self, payload: Dict[str, Any]) -> requests.Response:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.referer:
            headers["HTTP-Referer"] = self.referer
        if self.app_name:
            headers["X-Title"] = self.app_name

        try:
            response = self.session.post(
                self.base_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise OpenRouterImageRetryableError(
                f"OpenRouter request transport error: {exc}",
            ) from exc

        if response.status_code in _RETRYABLE_STATUS_CODES:
            raise OpenRouterImageRetryableError(
                f"OpenRouter returned retryable status {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise OpenRouterImageClientError(
                f"OpenRouter returned status {response.status_code}: {response.text}",
                status_code=response.status_code,
            )
        return response

    def _sleep_backoff(self, attempt: int) -> None:
        delay = min(self.backoff_initial_s * (2 ** (attempt - 1)), self.backoff_cap_s)
        if delay > 0:
            self._sleep(delay)

    def _build_payload(
        self,
        *,
        prompt: str,
        image_bytes: bytes,
        content_type: str,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("utf-8")
        return {
            "model": model or self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{content_type};base64,{encoded}",
                            },
                        },
                    ],
                }
            ],
            "modalities": ["image", "text"],
        }
