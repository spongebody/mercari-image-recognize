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


def test_login_page_served_without_auth(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.get("/login")
        assert r.status_code == 200
        assert "console/login" in r.text  # the page posts to the login endpoint


def test_unauthenticated_index_redirects_to_login(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")


def test_unauthenticated_config_redirects_to_login(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.get("/config", follow_redirects=False)
        assert r.status_code == 302
        assert "next=" in r.headers["location"]


def test_session_cookie_grants_index_access(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        login = client.post("/api/v1/console/login",
                            json={"username": "admin", "password": "secret", "remember": True})
        assert login.status_code == 200
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 200


def test_authed_user_visiting_login_is_redirected_home(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        client.post("/api/v1/console/login",
                    json={"username": "admin", "password": "secret", "remember": True})
        r = client.get("/login", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/"


def test_me_returns_superadmin_identity(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        client.post(
            "/api/v1/console/login",
            json={"username": "admin", "password": "secret", "remember": True},
        )

        r = client.get("/api/v1/console/me")

        assert r.status_code == 200
        assert r.json()["username"] == "admin"
        assert r.json()["role"] == "superadmin"
        assert "accounts" in r.json()["menus"]
        assert r.json()["defaultPath"] == "/"


def test_subaccount_login_uses_json_store_permissions(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    m = _reload(monkeypatch)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    with TestClient(m.app) as client:
        r = client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert r.status_code == 200

        me = client.get("/api/v1/console/me")
        assert me.status_code == 200
        assert me.json()["username"] == "model-tester"
        assert me.json()["role"] == "subaccount"
        assert me.json()["menus"] == ["evaluations"]
        assert me.json()["defaultPath"] == "/evaluations"


def test_subaccount_wrong_password_returns_401(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    m = _reload(monkeypatch)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    with TestClient(m.app) as client:
        r = client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "wrong123", "remember": True},
        )

        assert r.status_code == 401
        assert COOKIE_NAME not in r.cookies


def test_disabled_subaccount_existing_token_returns_403(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    m = _reload(monkeypatch)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    with TestClient(m.app) as client:
        login = client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert login.status_code == 200

        m.console_account_store.update_user("model-tester", enabled=False)
        me = client.get("/api/v1/console/me")

        assert me.status_code == 403


def test_non_ascii_login_credentials_return_401(monkeypatch):
    m = _reload(monkeypatch)
    with TestClient(m.app) as client:
        r = client.post(
            "/api/v1/console/login",
            json={"username": "管理员", "password": "秘密", "remember": False},
        )

        assert r.status_code == 401
