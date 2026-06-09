import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.auth import require_logs_auth


def _app(password: str) -> FastAPI:
    app = FastAPI()
    from fastapi import Depends

    @app.get("/secret", dependencies=[Depends(require_logs_auth(password))])
    def secret():
        return {"ok": True}

    return app


def test_no_password_returns_503():
    client = TestClient(_app(""))
    r = client.get("/secret")
    assert r.status_code == 503


def test_missing_credentials_returns_401():
    client = TestClient(_app("hunter2"))
    r = client.get("/secret")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Basic")


def test_wrong_password_returns_401():
    client = TestClient(_app("hunter2"))
    creds = base64.b64encode(b"admin:wrong").decode()
    r = client.get("/secret", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 401


def test_correct_password_returns_200():
    client = TestClient(_app("hunter2"))
    creds = base64.b64encode(b"admin:hunter2").decode()
    r = client.get("/secret", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_bearer_token_also_accepted():
    client = TestClient(_app("hunter2"))
    r = client.get("/secret", headers={"Authorization": "Bearer hunter2"})
    assert r.status_code == 200


def test_malformed_base64_returns_401():
    client = TestClient(_app("hunter2"))
    r = client.get("/secret", headers={"Authorization": "Basic !!!"})
    assert r.status_code == 401


def test_valid_session_cookie_accepted():
    from app.observability.auth import COOKIE_NAME, make_session_token

    client = TestClient(_app("hunter2"))
    client.cookies.set(COOKIE_NAME, make_session_token("hunter2", 3600))
    r = client.get("/secret")
    assert r.status_code == 200


def test_invalid_session_cookie_rejected():
    from app.observability.auth import COOKIE_NAME

    client = TestClient(_app("hunter2"))
    client.cookies.set(COOKIE_NAME, "bogus.token")
    r = client.get("/secret")
    assert r.status_code == 401


def test_is_console_authed_via_cookie_and_header():
    import base64 as _b64

    from fastapi import Request

    from app.observability.auth import COOKIE_NAME, is_console_authed, make_session_token

    def _request(headers):
        scope = {"type": "http", "headers": headers}
        return Request(scope)

    cookie = make_session_token("hunter2", 3600)
    req_cookie = _request([(b"cookie", f"{COOKIE_NAME}={cookie}".encode())])
    assert is_console_authed(req_cookie, "hunter2") is True

    basic = _b64.b64encode(b"a:hunter2").decode()
    req_header = _request([(b"authorization", f"Basic {basic}".encode())])
    assert is_console_authed(req_header, "hunter2") is True

    req_none = _request([])
    assert is_console_authed(req_none, "hunter2") is False
