import json
from typing import Any, Dict, List, Tuple

import requests

from ..errors import LLMRequestError


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: int,
        referer: str = "",
        app_name: str = "",
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout
        self.referer = referer
        self.app_name = app_name
        self.session = requests.Session()

    def chat(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        temperature: float = 0.2,
        max_tokens: int = 1024,
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

        try:
            response = self.session.post(
                self.base_url, headers=headers, data=json.dumps(payload), timeout=self.timeout
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
