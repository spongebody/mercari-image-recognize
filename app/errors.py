from __future__ import annotations

from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from .llm.resilient import AttemptRecord  # pragma: no cover


class BadRequestError(Exception):
    pass


class LLMRequestError(Exception):
    pass


class LLMParseError(Exception):
    """Raised by parse_llm_json when LLM output cannot be coerced into JSON."""


class LLMAllAttemptsFailedError(Exception):
    """Raised when every retry + fallback attempt for a stage has failed."""

    def __init__(self, stage: str, attempts: "List[AttemptRecord]") -> None:
        self.stage = stage
        self.attempts = list(attempts)
        super().__init__(f"{stage}: {len(self.attempts)} attempts failed.")
