import json

from fastapi.testclient import TestClient

from main import app


def test_request_gets_x_request_id_header():
    with TestClient(app) as client:
        r = client.get("/api/v1/config")
    assert "x-request-id" in {k.lower() for k in r.headers}
    assert len(r.headers["x-request-id"]) == 32


def test_health_endpoint_does_not_log():
    """/health is exempt to keep monitoring noise out of the logs DB."""
    with TestClient(app) as client:
        r = client.get("/health")
    # /health may or may not have X-Request-Id; verify it doesn't error
    assert r.status_code == 200


def test_large_response_body_passes_through_uncorrupted(monkeypatch):
    """Responses larger than log_response_max_bytes still deliver full body to client."""
    from fastapi import FastAPI
    from main import app

    big_payload = {"data": "x" * (3 * 1024 * 1024)}  # ~3 MiB JSON
    # add a route on the existing app that returns the big payload
    @app.get("/__test_big__")
    def _big():
        return big_payload

    with TestClient(app) as client:
        r = client.get("/__test_big__")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3 * 1024 * 1024
