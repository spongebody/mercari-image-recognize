from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Callable, Optional

from fastapi import Cookie, Header, HTTPException, Request

COOKIE_NAME = "console_session"
REMEMBER_TTL = 30 * 24 * 3600  # 30 days
SESSION_TTL = 12 * 3600        # 12 hours


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def make_session_token(password: str, ttl_seconds: int) -> str:
    """Return a signed `<payload>.<sig>` session token bound to `password`."""
    payload = _b64url(json.dumps({"exp": int(time.time()) + ttl_seconds}).encode())
    sig = _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_session_token(token: Optional[str], password: str) -> bool:
    if not password or not token or "." not in token:
        return False
    payload, _, sig = token.partition(".")
    expected = _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        data = json.loads(_b64url_decode(payload))
    except Exception:
        return False
    return int(data.get("exp", 0)) > int(time.time())


def require_logs_auth(expected_password: str) -> Callable:
    def _dep(authorization: Optional[str] = Header(default=None)) -> None:
        if not expected_password:
            raise HTTPException(status_code=503, detail="Logs viewer not configured (set LOGS_PASSWORD).")
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": 'Basic realm="logs"'},
            )
        scheme, _, value = authorization.partition(" ")
        provided: Optional[str] = None
        if scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(value).decode("utf-8", errors="replace")
                _, _, pw = decoded.partition(":")
                provided = pw
            except Exception:
                provided = None
        elif scheme.lower() == "bearer":
            provided = value
        if not provided or not hmac.compare_digest(provided, expected_password):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": 'Basic realm="logs"'},
            )

    return _dep
