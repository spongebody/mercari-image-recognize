import time
import base64
import hashlib
import hmac
import json

import pytest
from fastapi import HTTPException, Request
from app.observability.auth import (
    COOKIE_NAME,
    ConsoleIdentity,
    REMEMBER_TTL,
    SESSION_TTL,
    identity_from_request,
    make_identity_session_token,
    make_session_token,
    require_menu_auth,
    require_superadmin_auth,
    verify_session_identity,
    verify_session_token,
)


def test_cookie_name_and_ttls():
    assert COOKIE_NAME == "console_session"
    assert REMEMBER_TTL == 30 * 24 * 3600
    assert SESSION_TTL == 12 * 3600


def test_token_roundtrip_valid():
    token = make_session_token("hunter2", 3600)
    assert verify_session_token(token, "hunter2") is True


def test_token_rejected_with_wrong_password():
    token = make_session_token("hunter2", 3600)
    assert verify_session_token(token, "other") is False


def test_token_rejected_when_expired():
    token = make_session_token("hunter2", -1)
    assert verify_session_token(token, "hunter2") is False


def test_token_rejected_when_tampered():
    token = make_session_token("hunter2", 3600)
    payload, _, sig = token.partition(".")
    tampered = payload + "x." + sig
    assert verify_session_token(tampered, "hunter2") is False


def test_token_rejected_when_password_empty():
    assert verify_session_token(make_session_token("", 3600), "") is False


def test_token_with_non_integer_exp_is_rejected():
    payload = base64.urlsafe_b64encode(json.dumps({"exp": "not-an-int"}).encode()).rstrip(b"=").decode()
    sig = base64.urlsafe_b64encode(
        hmac.new(b"hunter2", payload.encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    token = f"{payload}.{sig}"

    assert verify_session_identity(token, "hunter2") is None
    assert verify_session_token(token, "hunter2") is False


def test_token_with_non_ascii_signature_is_rejected():
    payload, _, _ = make_session_token("hunter2", 3600).partition(".")
    token = f"{payload}.é"

    assert verify_session_identity(token, "hunter2") is None
    assert verify_session_token(token, "hunter2") is False


def _request(headers: dict[str, str] | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in (headers or {}).items()
            ],
        }
    )


def _basic_auth(password: str, username: str = "admin") -> str:
    creds = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {creds}"


def test_identity_token_roundtrip_contains_user_role_and_menus():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["evaluations"],
    )

    identity = verify_session_identity(token, "hunter2")

    assert identity == ConsoleIdentity(
        username="model-tester",
        role="subaccount",
        menus=("evaluations",),
    )


def test_legacy_exp_only_token_is_superadmin_for_compatibility():
    token = make_session_token("hunter2", 3600)

    identity = verify_session_identity(token, "hunter2")

    assert identity.role == "superadmin"
    assert "accounts" in identity.menus


def test_verify_session_token_still_returns_bool_for_identity_tokens():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["logs"],
    )

    assert verify_session_token(token, "hunter2") is True
    assert verify_session_token(token, "wrong") is False


@pytest.mark.parametrize("authorization", [_basic_auth("hunter2"), "Bearer hunter2"])
def test_identity_from_request_accepts_password_headers_as_superadmin(authorization):
    identity = identity_from_request(_request({"Authorization": authorization}), "hunter2")

    assert identity == ConsoleIdentity(
        username="admin",
        role="superadmin",
        menus=("test", "config", "evaluations", "logs", "accounts"),
    )


@pytest.mark.parametrize("authorization", [_basic_auth("é"), "Bearer é"])
def test_identity_from_request_rejects_non_ascii_credentials(authorization):
    assert identity_from_request(_request({"Authorization": authorization}), "hunter2") is None


def test_identity_from_request_prefers_cookie_identity_over_header():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["logs"],
    )

    identity = identity_from_request(
        _request(
            {
                "Cookie": f"{COOKIE_NAME}={token}",
                "Authorization": _basic_auth("hunter2"),
            }
        ),
        "hunter2",
    )

    assert identity == ConsoleIdentity(
        username="model-tester",
        role="subaccount",
        menus=("logs",),
    )


def test_require_menu_auth_allows_identity_with_menu():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["evaluations"],
    )
    dep = require_menu_auth("hunter2", "evaluations")

    identity = dep(authorization=None, console_session=token)

    assert identity.username == "model-tester"
    assert identity.has_menu("evaluations") is True


def test_require_menu_auth_rejects_missing_menu_with_403():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["evaluations"],
    )
    dep = require_menu_auth("hunter2", "logs")

    with pytest.raises(HTTPException) as exc:
        dep(authorization=None, console_session=token)

    assert exc.value.status_code == 403


def test_require_menu_auth_returns_503_when_password_not_configured():
    dep = require_menu_auth("", "logs")

    with pytest.raises(HTTPException) as exc:
        dep(authorization=None, console_session=None)

    assert exc.value.status_code == 503


def test_require_superadmin_auth_rejects_subaccount_with_accounts_payload():
    token = make_identity_session_token(
        "hunter2",
        ttl_seconds=3600,
        username="model-tester",
        role="subaccount",
        menus=["accounts", "logs"],
    )
    dep = require_superadmin_auth("hunter2")

    with pytest.raises(HTTPException) as exc:
        dep(authorization=None, console_session=token)

    assert exc.value.status_code == 403
