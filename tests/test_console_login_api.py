import importlib

import app.config
import main
from app.observability.auth import COOKIE_NAME
from fastapi.testclient import TestClient


def _reload(monkeypatch, user="admin", password="secret"):
    monkeypatch.setenv("LOGS_USER", user)
    monkeypatch.setenv("LOGS_PASSWORD", password)
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def test_login_success_sets_cookie(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "secret", "remember": True})
        assert r.status_code == 200
        assert r.json() == {"ok": True}
        assert COOKIE_NAME in r.cookies


def test_login_wrong_password_401_no_cookie(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "nope", "remember": False})
        assert r.status_code == 401
        assert COOKIE_NAME not in r.cookies


def test_login_wrong_username_401(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "root", "password": "secret", "remember": False})
        assert r.status_code == 401


def test_login_not_configured_503(monkeypatch):
    m = _reload(monkeypatch, password="")
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "x", "remember": False})
        assert r.status_code == 503


def test_remember_false_has_no_max_age(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/login",
                        json={"username": "admin", "password": "secret", "remember": False})
        set_cookie = r.headers["set-cookie"]
        assert "Max-Age" not in set_cookie


def test_logout_clears_cookie(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post("/api/v1/console/logout")
        assert r.status_code == 200
        assert 'console_session=' in r.headers["set-cookie"]
        assert "Max-Age=0" in r.headers["set-cookie"] or "expires=" in r.headers["set-cookie"].lower()
