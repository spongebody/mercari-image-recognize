from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import time
from typing import Any, Callable, Optional

from fastapi import Cookie, Header, HTTPException, Request

try:
    from app.console_accounts import ALL_MENUS, SUBACCOUNT_ROLE, SUPERADMIN_ROLE
except ImportError:  # pragma: no cover - defensive fallback for partial imports
    ALL_MENUS = ("test", "config", "evaluations", "logs", "accounts")
    SUPERADMIN_ROLE = "superadmin"
    SUBACCOUNT_ROLE = "subaccount"

COOKIE_NAME = "console_session"
REMEMBER_TTL = 30 * 24 * 3600  # 30 days
SESSION_TTL = 12 * 3600        # 12 hours
_DEFAULT_SUPERADMIN_USERNAME = "admin"
_SUBACCOUNT_MENUS = tuple(menu for menu in ALL_MENUS if menu != "accounts")


@dataclass(frozen=True)
class ConsoleIdentity:
    username: str
    role: str
    menus: tuple[str, ...]

    @property
    def is_superadmin(self) -> bool:
        return self.role == SUPERADMIN_ROLE

    def has_menu(self, menu_id: str) -> bool:
        return menu_id in self.menus


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def _encode_payload(data: dict[str, Any], password: str) -> str:
    payload = _b64url(json.dumps(data, separators=(",", ":")).encode())
    sig = _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _safe_compare_digest(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left, right)
    except TypeError:
        return False


def make_session_token(password: str, ttl_seconds: int) -> str:
    """Return a signed `<payload>.<sig>` session token bound to `password`."""
    return _encode_payload({"exp": int(time.time()) + ttl_seconds}, password)


def make_identity_session_token(
    password: str,
    ttl_seconds: int,
    *,
    username: str,
    role: str,
    menus: list[str] | tuple[str, ...],
) -> str:
    """Return a signed session token carrying console identity claims."""
    return _encode_payload(
        {
            "exp": int(time.time()) + ttl_seconds,
            "username": username,
            "role": role,
            "menus": list(menus),
        },
        password,
    )


def _decode_session_payload(token: Optional[str], password: str) -> Optional[dict[str, Any]]:
    if not password or not token or "." not in token:
        return None
    payload, _, sig = token.partition(".")
    expected = _b64url(hmac.new(password.encode(), payload.encode(), hashlib.sha256).digest())
    if not _safe_compare_digest(sig, expected):
        return None
    try:
        data = json.loads(_b64url_decode(payload))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        exp = int(data.get("exp", 0))
    except (TypeError, ValueError):
        return None
    if exp <= int(time.time()):
        return None
    return data


def _superadmin_identity(username: str = _DEFAULT_SUPERADMIN_USERNAME) -> ConsoleIdentity:
    return ConsoleIdentity(username=username, role=SUPERADMIN_ROLE, menus=tuple(ALL_MENUS))


def _identity_from_payload(data: dict[str, Any]) -> Optional[ConsoleIdentity]:
    if "username" not in data and "role" not in data and "menus" not in data:
        return _superadmin_identity()

    username = str(data.get("username", "")).strip()
    role = str(data.get("role", "")).strip()
    if not username or role not in {SUPERADMIN_ROLE, SUBACCOUNT_ROLE}:
        return None

    if role == SUPERADMIN_ROLE:
        return _superadmin_identity(username)

    raw_menus = data.get("menus", [])
    if not isinstance(raw_menus, (list, tuple)):
        return None
    allowed = set(_SUBACCOUNT_MENUS)
    requested_menus = {menu for menu in raw_menus if isinstance(menu, str)}
    menus = tuple(menu for menu in ALL_MENUS if menu in allowed and menu in requested_menus)
    if not menus:
        return None
    return ConsoleIdentity(username=username, role=SUBACCOUNT_ROLE, menus=menus)


def verify_session_identity(token: Optional[str], password: str) -> Optional[ConsoleIdentity]:
    data = _decode_session_payload(token, password)
    if data is None:
        return None
    return _identity_from_payload(data)


def verify_session_token(token: Optional[str], password: str) -> bool:
    return verify_session_identity(token, password) is not None


def _password_from_header(authorization: str) -> Optional[str]:
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(value).decode("utf-8", errors="replace")
            _, _, pw = decoded.partition(":")
            return pw
        except Exception:
            return None
    if scheme.lower() == "bearer":
        return value
    return None


def _identity_from_credentials(
    authorization: Optional[str],
    cookie: Optional[str],
    password: str,
) -> Optional[ConsoleIdentity]:
    if not password:
        return None
    if cookie:
        identity = verify_session_identity(cookie, password)
        if identity is not None:
            return identity
    if authorization:
        provided = _password_from_header(authorization)
        if provided and _safe_compare_digest(provided, password):
            return _superadmin_identity()
    return None


def _is_authed(authorization: Optional[str], cookie: Optional[str], password: str) -> bool:
    return _identity_from_credentials(authorization, cookie, password) is not None


def identity_from_request(request: Request, password: str) -> Optional[ConsoleIdentity]:
    """Return console identity from a valid session cookie or auth header."""
    return _identity_from_credentials(
        request.headers.get("authorization"),
        request.cookies.get(COOKIE_NAME),
        password,
    )


def is_console_authed(request: Request, password: str) -> bool:
    """True if the request carries a valid session cookie or auth header."""
    return identity_from_request(request, password) is not None


def require_logs_auth(expected_password: str) -> Callable:
    def _dep(
        authorization: Optional[str] = Header(default=None),
        console_session: Optional[str] = Cookie(default=None),
    ) -> None:
        if not expected_password:
            raise HTTPException(status_code=503, detail="Logs viewer not configured (set LOGS_PASSWORD).")
        if _is_authed(authorization, console_session, expected_password):
            return
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": 'Basic realm="logs"'},
        )

    return _dep


def require_menu_auth(expected_password: str, menu_id: str) -> Callable:
    def _dep(
        authorization: Optional[str] = Header(default=None),
        console_session: Optional[str] = Cookie(default=None),
    ) -> ConsoleIdentity:
        if not expected_password:
            raise HTTPException(status_code=503, detail="Console auth not configured (set LOGS_PASSWORD).")
        identity = _identity_from_credentials(authorization, console_session, expected_password)
        if identity is None:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": 'Basic realm="logs"'},
            )
        if not identity.has_menu(menu_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return identity

    return _dep


def require_superadmin_auth(expected_password: str) -> Callable:
    menu_dep = require_menu_auth(expected_password, "accounts")

    def _dep(
        authorization: Optional[str] = Header(default=None),
        console_session: Optional[str] = Cookie(default=None),
    ) -> ConsoleIdentity:
        identity = menu_dep(authorization=authorization, console_session=console_session)
        if not identity.is_superadmin:
            raise HTTPException(status_code=403, detail="Forbidden")
        return identity

    return _dep
