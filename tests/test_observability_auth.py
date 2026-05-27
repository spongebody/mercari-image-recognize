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
