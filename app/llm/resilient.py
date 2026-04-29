from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..errors import LLMAllAttemptsFailedError, LLMParseError, LLMRequestError
from .client import OpenRouterClient
from .json_parser import parse_llm_json


_BACKOFF_S: Tuple[float, ...] = (0.2, 0.4, 0.8)
_BACKOFF_CAP_S: float = 1.5
_MIN_USEFUL_BUDGET_S: float = 1.0
_BACKOFF_HEADROOM_S: float = 0.1
_STATUS_RE = re.compile(r"OpenRouter returned (\d{3})")


@dataclass
class AttemptRecord:
    model: str
    attempt: int
    attempt_global: int
    error_kind: str
    message: str
    latency_ms: float
    status_code: Optional[int] = None


def _extract_status_code(exc: Exception) -> Optional[int]:
    m = _STATUS_RE.search(str(exc))
    return int(m.group(1)) if m else None


class ResilientCaller:
    """Wraps OpenRouterClient.chat with retry + fallback + JSON parsing."""

    def __init__(
        self,
        *,
        client: OpenRouterClient,
        max_retries: int,
        total_budget_s: float,
        per_attempt_timeout_s: float,
    ) -> None:
        self.client = client
        self.max_retries = max(0, int(max_retries))
        self.total_budget_s = float(total_budget_s)
        self.per_attempt_timeout_s = float(per_attempt_timeout_s)

    def call_and_parse(
        self,
        *,
        stage: str,
        primary_model: str,
        fallback_models: Sequence[str],
        messages: List[Dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        attempts: List[AttemptRecord] = []
        if not primary_model:
            raise LLMAllAttemptsFailedError(stage=stage, attempts=attempts)

        deadline = time.monotonic() + self.total_budget_s
        global_idx = 0

        schedule: List[Tuple[str, int]] = [(primary_model, self.max_retries + 1)]
        for m in fallback_models:
            if m and m != primary_model:
                schedule.append((m, 1))

        for model, n_attempts in schedule:
            for attempt in range(1, n_attempts + 1):
                global_idx += 1
                remaining = deadline - time.monotonic()
                if remaining <= _MIN_USEFUL_BUDGET_S:
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            attempt=attempt,
                            attempt_global=global_idx,
                            error_kind="budget_exhausted",
                            message=f"Stage budget exhausted before attempt (remaining {remaining:.2f}s).",
                            latency_ms=0.0,
                            status_code=None,
                        )
                    )
                    raise LLMAllAttemptsFailedError(stage=stage, attempts=attempts)

                effective_timeout = min(self.per_attempt_timeout_s, remaining)
                t0 = time.monotonic()
                try:
                    content, raw_response = self.client.chat(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        timeout=effective_timeout,
                    )
                except LLMRequestError as exc:
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            attempt=attempt,
                            attempt_global=global_idx,
                            error_kind="request_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - t0) * 1000.0,
                            status_code=_extract_status_code(exc),
                        )
                    )
                    if attempt < n_attempts:
                        self._sleep_capped(attempt - 1, deadline)
                    continue

                try:
                    parsed = parse_llm_json(content)
                    if not isinstance(parsed, dict):
                        raise LLMParseError("LLM did not return a JSON object.")
                except LLMParseError as exc:
                    attempts.append(
                        AttemptRecord(
                            model=model,
                            attempt=attempt,
                            attempt_global=global_idx,
                            error_kind="parse_failed",
                            message=str(exc),
                            latency_ms=(time.monotonic() - t0) * 1000.0,
                            status_code=200,
                        )
                    )
                    if attempt < n_attempts:
                        self._sleep_capped(attempt - 1, deadline)
                    continue

                attempts.append(
                    AttemptRecord(
                        model=model,
                        attempt=attempt,
                        attempt_global=global_idx,
                        error_kind="ok",
                        message="",
                        latency_ms=(time.monotonic() - t0) * 1000.0,
                        status_code=200,
                    )
                )
                return parsed, raw_response, attempts

        raise LLMAllAttemptsFailedError(stage=stage, attempts=attempts)

    @staticmethod
    def _sleep_capped(idx: int, deadline: float) -> None:
        base = _BACKOFF_S[min(idx, len(_BACKOFF_S) - 1)]
        delay = min(base, _BACKOFF_CAP_S)
        budget_room = max(0.0, deadline - time.monotonic() - _BACKOFF_HEADROOM_S)
        delay = min(delay, budget_room)
        if delay > 0:
            time.sleep(delay)
