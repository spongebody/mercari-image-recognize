import base64
import importlib

from fastapi.testclient import TestClient

import app.config


def _reload(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "secret")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    import main
    importlib.reload(app.config)
    return importlib.reload(main)


def _login_admin(client):
    return client.post(
        "/api/v1/console/login",
        json={"username": "admin", "password": "secret", "remember": True},
    )


def _basic(password="secret"):
    token = base64.b64encode(f"admin:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _bearer(password="secret"):
    return {"Authorization": f"Bearer {password}"}


def test_superadmin_can_create_update_delete_subaccount(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        _login_admin(client)
        created = client.post(
            "/api/v1/console/users",
            json={
                "username": "model-tester",
                "password": "secret123",
                "menus": ["evaluations", "accounts"],
                "enabled": True,
            },
        )
        assert created.status_code == 200
        assert created.json()["menus"] == ["evaluations"]

        listed = client.get("/api/v1/console/users")
        assert listed.status_code == 200
        assert listed.json()["users"][0]["username"] == "model-tester"
        assert "password_hash" not in listed.text

        updated = client.put("/api/v1/console/users/model-tester", json={"menus": ["test"], "enabled": False})
        assert updated.status_code == 200
        assert updated.json()["menus"] == ["test"]
        assert updated.json()["enabled"] is False

        deleted = client.delete("/api/v1/console/users/model-tester")
        assert deleted.status_code == 200
        assert client.get("/api/v1/console/users").json()["users"] == []


def test_subaccount_cannot_call_account_api(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])
    with TestClient(m.app) as client:
        client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert client.get("/api/v1/console/users").status_code == 403


def test_unauthenticated_account_api_returns_401(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        assert client.get("/api/v1/console/users").status_code == 401
        assert client.post("/api/v1/console/users", json={}).status_code == 401
        assert client.put("/api/v1/console/users/missing", json={}).status_code == 401
        assert client.delete("/api/v1/console/users/missing").status_code == 401


def test_create_duplicate_returns_400(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        _login_admin(client)
        payload = {"username": "model-tester", "password": "secret123", "menus": ["evaluations"]}
        assert client.post("/api/v1/console/users", json=payload).status_code == 200

        duplicate = client.post("/api/v1/console/users", json=payload)

        assert duplicate.status_code == 400


def test_update_delete_missing_user_returns_404(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        _login_admin(client)

        updated = client.put("/api/v1/console/users/missing", json={"enabled": False})
        deleted = client.delete("/api/v1/console/users/missing")

        assert updated.status_code == 404
        assert deleted.status_code == 404


def test_create_username_equal_to_superadmin_returns_400(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        _login_admin(client)

        created = client.post(
            "/api/v1/console/users",
            json={"username": "admin", "password": "secret123", "menus": ["evaluations"]},
        )

        assert created.status_code == 400


def test_empty_or_invalid_menus_return_400(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        _login_admin(client)

        empty = client.post(
            "/api/v1/console/users",
            json={"username": "empty-menu", "password": "secret123", "menus": []},
        )
        invalid = client.post(
            "/api/v1/console/users",
            json={"username": "invalid-menu", "password": "secret123", "menus": ["accounts"]},
        )

        assert empty.status_code == 400
        assert invalid.status_code == 400


def test_basic_and_bearer_logs_password_can_call_account_api_as_superadmin(monkeypatch, tmp_path):
    m = _reload(monkeypatch, tmp_path)
    with TestClient(m.app) as client:
        basic = client.post(
            "/api/v1/console/users",
            headers=_basic(),
            json={"username": "basic-user", "password": "secret123", "menus": ["logs"]},
        )
        bearer = client.post(
            "/api/v1/console/users",
            headers=_bearer(),
            json={"username": "bearer-user", "password": "secret456", "menus": ["test"]},
        )

        assert basic.status_code == 200
        assert bearer.status_code == 200
        listed = client.get("/api/v1/console/users", headers=_basic())
        assert listed.status_code == 200
        assert [user["username"] for user in listed.json()["users"]] == ["basic-user", "bearer-user"]
