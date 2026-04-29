from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .llm.resilient import AttemptRecord  # pragma: no cover


class BadRequestError(Exception):
    pass


class LLMRequestError(Exception):
    pass


class LLMParseError(Exception):
    """Raised by parse_llm_json when LLM output cannot be coerced into JSON."""
