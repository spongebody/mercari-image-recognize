import time

from app.observability.auth import (
    COOKIE_NAME,
    REMEMBER_TTL,
    SESSION_TTL,
    make_session_token,
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
