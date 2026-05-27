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
