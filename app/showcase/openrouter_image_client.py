import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

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
class ImageAttemptRecord:
    model: str
    attempt: int
    attempt_global: int
    error_kind: str  # "ok" | "request_failed" | "parse_failed"
    message: str
    latency_ms: float
    status_code: Optional[int] = None


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
    model: str = ""
    attempt_records: List[ImageAttemptRecord] = field(default_factory=list)


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
        fallback_models: Optional[Sequence[str]] = None,
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
        self.fallback_models: List[str] = [
            m for m in (fallback_models or []) if m
        ]
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
        fallback_models: Optional[Sequence[str]] = None,
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

        explicit_fallbacks = (
            list(fallback_models) if fallback_models is not None else list(self.fallback_models)
        )
        schedule: List[str] = [effective_model]
        for candidate in explicit_fallbacks:
            cleaned = (candidate or "").strip()
            if cleaned and cleaned not in schedule:
                schedule.append(cleaned)

        attempts_records: List[ImageAttemptRecord] = []
        global_attempt = 0
        last_error: Optional[OpenRouterImageClientError] = None

        for model_name in schedule:
            payload = self._build_payload(
                prompt=prompt,
                image_bytes=image_bytes,
                content_type=content_type,
                model=model_name,
            )

            for attempt in range(1, self.max_retries + 1):
                global_attempt += 1
                attempt_started = time.monotonic()
                try:
                    response = self._post(payload)
                except OpenRouterImageRetryableError as exc:
                    last_error = exc
                    attempts_records.append(
                        ImageAttemptRecord(
                            model=model_name,
                            attempt=attempt,
                            attempt_global=global_attempt,
                            error_kind="request_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - attempt_started) * 1000.0,
                            status_code=exc.status_code,
                        )
                    )
                    if attempt < self.max_retries:
                        self._sleep_backoff(attempt)
                        continue
                    break  # exhausted this model — try the next one
                except OpenRouterImageClientError as exc:
                    # Non-retryable upstream error (e.g. 401). Record and abort
                    # — switching models will not change auth/missing-API-key
                    # outcomes, so propagate immediately.
                    attempts_records.append(
                        ImageAttemptRecord(
                            model=model_name,
                            attempt=attempt,
                            attempt_global=global_attempt,
                            error_kind="request_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - attempt_started) * 1000.0,
                            status_code=exc.status_code,
                        )
                    )
                    raise

                try:
                    response_body = response.json()
                except ValueError as exc:
                    last_error = OpenRouterImageRetryableError(
                        f"Failed to decode OpenRouter response JSON: {exc}",
                        status_code=response.status_code,
                    )
                    attempts_records.append(
                        ImageAttemptRecord(
                            model=model_name,
                            attempt=attempt,
                            attempt_global=global_attempt,
                            error_kind="parse_failed",
                            message=str(last_error),
                            latency_ms=(time.monotonic() - attempt_started) * 1000.0,
                            status_code=response.status_code,
                        )
                    )
                    if attempt < self.max_retries:
                        self._sleep_backoff(attempt)
                        continue
                    break

                try:
                    image = extract_image_payload(response_body)
                except OpenRouterImageRetryableError as exc:
                    last_error = exc
                    attempts_records.append(
                        ImageAttemptRecord(
                            model=model_name,
                            attempt=attempt,
                            attempt_global=global_attempt,
                            error_kind="parse_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - attempt_started) * 1000.0,
                            status_code=response.status_code,
                        )
                    )
                    if attempt < self.max_retries:
                        self._sleep_backoff(attempt)
                        continue
                    break

                attempts_records.append(
                    ImageAttemptRecord(
                        model=model_name,
                        attempt=attempt,
                        attempt_global=global_attempt,
                        error_kind="ok",
                        message="",
                        latency_ms=(time.monotonic() - attempt_started) * 1000.0,
                        status_code=response.status_code,
                    )
                )
                return OpenRouterImageResult(
                    image=image,
                    upstream_status_code=response.status_code,
                    response_body=response_body,
                    attempts=attempt,
                    model=model_name,
                    attempt_records=attempts_records,
                )

        if last_error is not None:
            # Surface the most recent retryable error after exhausting every
            # configured model. Preserve the latest status_code/error_code.
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
