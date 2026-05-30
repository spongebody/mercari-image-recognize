import json
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..errors import LLMRequestError


# Sentinel: when passed as `reasoning`, fall back to the client's configured
# reasoning. Lets callers override reasoning per call (e.g. disable it for the
# fast classification stages) without affecting other callers.
USE_CLIENT_REASONING = object()


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int,
        referer: str = "",
        app_name: str = "",
        reasoning: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.referer = referer
        self.app_name = app_name
        self.reasoning = dict(reasoning) if reasoning else None
        self.session = requests.Session()

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 1024,
        timeout: Optional[float] = None,
        reasoning: Any = USE_CLIENT_REASONING,
    ) -> Tuple[str, Dict[str, Any]]:
        if not self.api_key:
            raise LLMRequestError("OPENROUTER_API_KEY is not configured.")
        if not model:
            raise LLMRequestError("Model name is empty.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.referer:
            headers["HTTP-Referer"] = self.referer
        if self.app_name:
            headers["X-Title"] = self.app_name

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        effective_reasoning = (
            self.reasoning if reasoning is USE_CLIENT_REASONING else reasoning
        )
        if effective_reasoning is not None:
            payload["reasoning"] = dict(effective_reasoning)

        effective_timeout = timeout if timeout is not None else self.timeout
        try:
            response = self.session.post(
                self.base_url,
                headers=headers,
                data=json.dumps(payload),
                timeout=effective_timeout,
            )
        except requests.RequestException as exc:
            raise LLMRequestError(f"OpenRouter request failed: {exc}") from exc

        if response.status_code >= 400:
            raise LLMRequestError(
                f"OpenRouter returned {response.status_code}: {response.text}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise LLMRequestError(f"Failed to parse OpenRouter response: {exc}") from exc

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMRequestError("OpenRouter response is missing content.") from exc

        return content, data
