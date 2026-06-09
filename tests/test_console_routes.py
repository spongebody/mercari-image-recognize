import base64
import importlib

import app.config
import main


def _reload_with_password(monkeypatch, password: str):
    monkeypatch.setenv("LOGS_PASSWORD", password)
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _auth(password: str) -> dict:
    creds = base64.b64encode(f"a:{password}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


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
        assert "evaluation-run-list" in r.text  # evaluations console markup


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
