from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class AttemptRecord:
    model: str
    attempt: int            # per-model attempt index, starting at 1
    attempt_global: int     # global attempt index across all models, starting at 1
    error_kind: str         # "request_failed" | "parse_failed" | "budget_exhausted" | "ok"
    message: str
    latency_ms: float
    status_code: Optional[int] = None
