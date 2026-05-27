from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_request_id_var: ContextVar[Optional[str]] = ContextVar("observability_request_id", default=None)


def get_request_id() -> Optional[str]:
    return _request_id_var.get()


def set_request_id(request_id: str) -> Token:
    return _request_id_var.set(request_id)


def reset_request_id(token: Token) -> None:
    _request_id_var.reset(token)
