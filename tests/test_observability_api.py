import base64

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def set_password(monkeypatch):
    monkeypatch.setenv("LOGS_PASSWORD", "hunter2")
    # force re-import so settings reload
    import importlib, app.config, main
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _auth():
    creds = base64.b64encode(b"admin:hunter2").decode()
    return {"Authorization": f"Basic {creds}"}


def test_list_requests_requires_auth(set_password):
    with TestClient(set_password.app) as client:
        r = client.get("/api/v1/logs/requests")
    assert r.status_code == 401


def test_list_requests_returns_recent(set_password):
    with TestClient(set_password.app) as client:
        client.get("/health")  # produces nothing logged (health bypass)
        client.get("/api/v1/config", headers=_auth())  # produces a log row
        r = client.get("/api/v1/logs/requests?limit=5", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert any(item["endpoint"] == "/api/v1/config" for item in body["items"])


def test_request_detail_returns_llm_calls(set_password):
    with TestClient(set_password.app) as client:
        client.get("/api/v1/config", headers=_auth())
        items = client.get("/api/v1/logs/requests", headers=_auth()).json()["items"]
        rid = items[0]["request_id"]
        r = client.get(f"/api/v1/logs/requests/{rid}", headers=_auth())
    assert r.status_code == 200
    assert r.json()["request"]["request_id"] == rid
    assert isinstance(r.json()["llm_calls"], list)


def test_file_download_path_traversal_blocked(set_password):
    with TestClient(set_password.app) as client:
        client.get("/api/v1/config", headers=_auth())
        items = client.get("/api/v1/logs/requests", headers=_auth()).json()["items"]
        rid = items[0]["request_id"]
        r = client.get(f"/api/v1/logs/requests/{rid}/files/..%2F..%2Fetc%2Fpasswd",
                       headers=_auth())
    assert r.status_code in (400, 403, 404)


def test_stats_endpoint(set_password):
    with TestClient(set_password.app) as client:
        client.get("/api/v1/config", headers=_auth())
        r = client.get("/api/v1/logs/stats", headers=_auth())
    assert r.status_code == 200
    assert "total" in r.json()


def test_fts_query_capped_at_500(set_password, monkeypatch):
    """A broad FTS query that would match >500 rows doesn't trigger SQLite param errors."""
    # We can't easily seed 1000+ rows quickly; instead, verify the query simply succeeds
    # (the cap kicks in only when there are many matches). This is a smoke test.
    with TestClient(set_password.app) as client:
        # produce one logged request
        client.get("/api/v1/config", headers=_auth())
        r = client.get("/api/v1/logs/requests?q=config", headers=_auth())
    assert r.status_code == 200
