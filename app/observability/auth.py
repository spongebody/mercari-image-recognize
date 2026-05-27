from __future__ import annotations

import base64
import hmac
from typing import Callable, Optional

from fastapi import Header, HTTPException


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
