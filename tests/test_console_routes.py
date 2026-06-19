import base64
import importlib

import app.config
import main


def _reload_with_password(monkeypatch, password: str):
    monkeypatch.setenv("LOGS_PASSWORD", password)
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _reload_with_console_store(monkeypatch, tmp_path, password: str = "secret"):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", password)
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _auth(password: str) -> dict:
    creds = base64.b64encode(f"a:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def _bearer(password: str) -> dict:
    return {"Authorization": f"Bearer {password}"}


def test_index_requires_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")


def test_index_served_with_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/", headers=_auth("secret"))
        assert r.status_code == 200
        assert "shell.js" in r.text  # test page now includes the shared shell


def test_evaluations_requires_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/evaluations", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"].startswith("/login")


def test_evaluations_served_with_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/evaluations", headers=_auth("secret"))
        assert r.status_code == 200
        assert "shell.js" in r.text  # standalone evaluations page uses the shared shell
        assert "run-list" in r.text  # evaluations console markup (sidebar run list)


def test_config_no_longer_hosts_evaluations(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        r = client.get("/config", headers=_auth("secret"))
        assert r.status_code == 200
        assert "evaluation-run-list" not in r.text  # moved to /evaluations


def test_shell_assets_served_without_password(monkeypatch):
    m = _reload_with_password(monkeypatch, "secret")
    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        assert client.get("/assets/shell.js").status_code == 200
        assert client.get("/assets/shell.css").status_code == 200


def test_subaccount_can_only_access_allowed_page(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert client.get("/evaluations", follow_redirects=False).status_code == 200
        assert client.get("/config", follow_redirects=False).status_code == 403
        assert client.get("/accounts", follow_redirects=False).status_code == 403


def test_accounts_page_only_served_to_superadmin(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "secret")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    importlib.reload(main)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        client.post("/api/v1/console/login", json={"username": "admin", "password": "secret", "remember": True})
        r = client.get("/accounts", follow_redirects=False)
        assert r.status_code == 200
        assert "账号管理" in r.text
        assert "console/users" in r.text


def test_subaccount_can_only_call_allowed_api(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert client.get("/api/v1/evaluations").status_code == 200
        assert client.get("/api/v1/config").status_code == 403


def test_subaccount_without_test_menu_cannot_call_test_api(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)
    m.console_account_store.create_user("config-user", "secret123", ["config"])

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        client.post(
            "/api/v1/console/login",
            json={"username": "config-user", "password": "secret123", "remember": True},
        )
        response = client.post(
            "/api/v1/mercari/title/analyze",
            json={"title": "demo", "language": "ja"},
        )
        assert response.status_code == 403


def test_config_subaccount_can_only_access_config_menu(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)
    m.console_account_store.create_user("config-user", "secret123", ["config"])

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        client.post(
            "/api/v1/console/login",
            json={"username": "config-user", "password": "secret123", "remember": True},
        )
        assert client.get("/config", follow_redirects=False).status_code == 200
        assert client.get("/api/v1/config").status_code == 200
        assert client.get("/evaluations", follow_redirects=False).status_code == 403


def test_logs_password_headers_act_as_superadmin_for_pages_and_apis(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        assert client.get("/config", headers=_auth("secret")).status_code == 200
        assert client.get("/evaluations", headers=_auth("secret")).status_code == 200
        assert client.get("/api/v1/config", headers=_auth("secret")).status_code == 200
        assert client.get("/api/v1/evaluations", headers=_auth("secret")).status_code == 200

        assert client.get("/config", headers=_bearer("secret")).status_code == 200
        assert client.get("/api/v1/config", headers=_bearer("secret")).status_code == 200


def test_disabled_subaccount_token_rejected_for_pages_and_apis(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)
    m.console_account_store.create_user("model-tester", "secret123", ["evaluations"])

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        login = client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert login.status_code == 200

        m.console_account_store.update_user("model-tester", enabled=False)

        page = client.get("/evaluations", follow_redirects=False)
        assert page.status_code == 302
        assert page.headers["location"].startswith("/login")
        assert client.get("/api/v1/evaluations").status_code == 403


def test_unauthenticated_protected_apis_return_401(monkeypatch, tmp_path):
    m = _reload_with_console_store(monkeypatch, tmp_path)

    from fastapi.testclient import TestClient
    with TestClient(m.app) as client:
        assert client.get("/api/v1/config").status_code == 401
        assert client.get("/api/v1/evaluations").status_code == 401
